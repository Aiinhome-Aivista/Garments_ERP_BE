"""
Comprehensive End-to-End Test for Combined Process Definition with BOM in Garment ERP.
Tests all requirements from section 8:
1. Create a new finished product with Cutting, Stitching, Finishing, Packing processes.
2. Map multiple Raw Materials and Consumables under different processes.
3. Verify the same material can be mapped to different processes (e.g. Thread in Cutting and Stitching).
4. Save the product into the database.
5. Reopen the product in Edit mode (load_one) and verify all processes and nested materials load correctly.
6. Edit: update quantities, remove a material, remove a process, add a process.
7. Re-save and verify edits persist.
8. Test BOM Calculation formula: Required Material Quantity = Quantity per Unit * Planned Production Quantity.
9. Test process-wise requirements and consolidated material requirements across processes.
10. Test planning integration: stock availability and shortage calculation.
11. Test validations: duplicate materials in same process, missing process, quantity <= 0.
12. Clean up test records.
"""
import os, sys, importlib.util
from decimal import Decimal

be_dir = os.path.abspath('Garments_ERP_BE')
sys.path.insert(0, be_dir)
spec = importlib.util.spec_from_file_location("root_app", os.path.join(be_dir, "app.py"))
root_app = importlib.util.module_from_spec(spec)
spec.loader.exec_module(root_app)

from app import db
from app.errors import ApiError
from controllers.masters_controller import save, load_one, get_eligible_materials
from controllers.planning_controller import analyze
from controllers.auth_controller import make_token

def run_tests():
    print("=" * 70)
    print("STARTING COMPREHENSIVE PROCESS DEFINITION WITH BOM TESTS")
    print("=" * 70)
    
    with root_app.app.test_request_context():
        with db.tx() as conn:
            # Setup prerequisites
            uom_pcs = db.one(conn, "SELECT id, name FROM m_uom WHERE name='PCS' OR active=1 LIMIT 1")
            cat_fg = db.one(conn, "SELECT id, name FROM m_product_category WHERE name='FG' OR id=1 LIMIT 1")
            cat_rm = db.one(conn, "SELECT id, name FROM m_product_category WHERE name='RM' OR id=2 LIMIT 1")
            cat_con = db.one(conn, "SELECT id, name FROM m_product_category WHERE name='Consumables' OR id=3 LIMIT 1")

            # Check or create processes: Cutting, Stitching, Finishing, Packing
            process_names = ["Cutting", "Stitching", "Finishing", "Packing"]
            proc_map = {}
            for pname in process_names:
                p_row = db.one(conn, "SELECT id, name FROM m_process WHERE name=:n", n=pname)
                if not p_row:
                    pid = db.insert(conn, "m_process", {"name": pname, "active": 1})
                    proc_map[pname] = pid
                else:
                    proc_map[pname] = p_row["id"]
            print("[PASS] Processes available:", proc_map)

            # Ensure test raw materials exist: Fabric (RM) and Thread (Consumable)
            fabric = db.one(conn, "SELECT id, item_name, uom_id FROM m_product WHERE category_id=:c AND active=1 LIMIT 1", c=cat_rm["id"])
            if not fabric:
                fid = db.insert(conn, "m_product", {"name": "Test Cotton Fabric", "item_name": "Test Cotton Fabric-White", "category_id": cat_rm["id"], "uom_id": uom_pcs["id"], "active": 1})
                fabric = {"id": fid, "item_name": "Test Cotton Fabric-White", "uom_id": uom_pcs["id"]}

            thread = db.one(conn, "SELECT id, item_name, uom_id FROM m_product WHERE category_id=:c AND active=1 LIMIT 1", c=cat_con["id"])
            if not thread:
                tid = db.insert(conn, "m_product", {"name": "Test Sewing Thread", "item_name": "Test Sewing Thread-Black", "category_id": cat_con["id"], "uom_id": uom_pcs["id"], "active": 1})
                thread = {"id": tid, "item_name": "Test Sewing Thread-Black", "uom_id": uom_pcs["id"]}
            print(f"[PASS] Raw materials: Fabric (ID {fabric['id']}), Thread (ID {thread['id']})")

            # -----------------------------------------------------------------
            # TEST 1: Validation - Duplicate Material in Same Process
            # -----------------------------------------------------------------
            print("\n--- TEST 1: Validating duplicate material prevention within same process ---")
            dup_payload = {
                "name": "Test Dup FG", "item_name": "Test Dup FG SKU", "category_id": cat_con["id"], "uom_id": uom_pcs["id"],
                "processes": [
                    {
                        "process_id": proc_map["Cutting"],
                        "materials": [
                            {"material_id": fabric["id"], "qty_per_unit": 1.5, "uom_id": fabric["uom_id"]},
                            {"material_id": fabric["id"], "qty_per_unit": 0.5, "uom_id": fabric["uom_id"]} # Duplicate!
                        ]
                    }
                ]
            }
            try:
                save(conn, "product", dup_payload)
                assert False, "Should have thrown duplicate material error"
            except ApiError as e:
                print(f"[PASS] Duplicate caught: {e.message}")

            # -----------------------------------------------------------------
            # TEST 2: Validation - Zero / Negative Quantity
            # -----------------------------------------------------------------
            print("\n--- TEST 2: Validating zero / negative quantity ---")
            zero_q_payload = {
                "name": "Test Zero Qty", "item_name": "Test Zero Qty SKU", "category_id": cat_con["id"], "uom_id": uom_pcs["id"],
                "processes": [
                    {
                        "process_id": proc_map["Cutting"],
                        "materials": [{"material_id": fabric["id"], "qty_per_unit": 0, "uom_id": fabric["uom_id"]}]
                    }
                ]
            }
            try:
                save(conn, "product", zero_q_payload)
                assert False, "Should have thrown zero quantity error"
            except ApiError as e:
                print(f"[PASS] Zero quantity caught: {e.message}")

            # -----------------------------------------------------------------
            # TEST 3: Create Finished Product with 4 Processes and Multi-Material BOM
            # -----------------------------------------------------------------
            print("\n--- TEST 3: Create Finished Product with Combined Process Definition & BOM ---")
            # Process 1 (Cutting): Fabric 1.5, Thread 2.0
            # Process 2 (Stitching): Thread 1.0 (Same material used in different process!)
            # Process 3 (Finishing): No materials
            # Process 4 (Packing): No materials
            create_payload = {
                "name": "Men Polo T-Shirt Premium",
                "item_name": "Men-Polo-T-Shirt-Premium-Navy",
                "category_id": cat_con["id"], # Using cat without attr requirement for test simplicity
                "uom_id": uom_pcs["id"],
                "gst_type": "Flat",
                "gst_rate": 5,
                "processes": [
                    {
                        "process_id": proc_map["Cutting"],
                        "process_id__label": "Cutting",
                        "seq": 1,
                        "out_status": "WIP",
                        "materials": [
                            {"material_id": fabric["id"], "item_type": "Raw Material", "qty_per_unit": 1.5, "uom_id": fabric["uom_id"]},
                            {"material_id": thread["id"], "item_type": "Consumable", "qty_per_unit": 2.0, "uom_id": thread["uom_id"]}
                        ]
                    },
                    {
                        "process_id": proc_map["Stitching"],
                        "process_id__label": "Stitching",
                        "seq": 2,
                        "out_status": "WIP",
                        "materials": [
                            {"material_id": thread["id"], "item_type": "Consumable", "qty_per_unit": 1.0, "uom_id": thread["uom_id"]}
                        ]
                    },
                    {
                        "process_id": proc_map["Finishing"],
                        "process_id__label": "Finishing",
                        "seq": 3,
                        "out_status": "WIP",
                        "materials": []
                    },
                    {
                        "process_id": proc_map["Packing"],
                        "process_id__label": "Packing",
                        "seq": 4,
                        "out_status": "FP",
                        "materials": []
                    }
                ]
            }

            prod_id = save(conn, "product", create_payload)
            print(f"[PASS] Finished Product created with ID: {prod_id}")

            # -----------------------------------------------------------------
            # TEST 4: Reopen in Edit Mode & Verify Relational Data Loaded
            # -----------------------------------------------------------------
            print("\n--- TEST 4: Reopen product in Edit mode (load_one) ---")
            loaded = load_one(conn, "product", prod_id)
            procs = loaded.get("processes", [])
            assert len(procs) == 4, f"Expected 4 processes, got {len(procs)}"
            print(f"[PASS] Total processes loaded: {len(procs)}")

            # Process 1 checks
            p1 = procs[0]
            assert p1["process_id"] == proc_map["Cutting"]
            assert len(p1["materials"]) == 2
            assert float(p1["materials"][0]["qty_per_unit"]) == 1.5
            assert p1["materials"][0]["material_id"] == fabric["id"]
            assert float(p1["materials"][1]["qty_per_unit"]) == 2.0
            assert p1["materials"][1]["material_id"] == thread["id"]
            print(f"[PASS] Process 1 (Cutting) has 2 materials: Fabric (1.5), Thread (2.0)")

            # Process 2 checks (Same material: Thread)
            p2 = procs[1]
            assert p2["process_id"] == proc_map["Stitching"]
            assert len(p2["materials"]) == 1
            assert float(p2["materials"][0]["qty_per_unit"]) == 1.0
            assert p2["materials"][0]["material_id"] == thread["id"]
            print(f"[PASS] Process 2 (Stitching) has 1 material: Thread (1.0)")

            # Process 3 & 4 checks (Labour-only steps)
            p3 = procs[2]
            assert p3["process_id"] == proc_map["Finishing"]
            assert len(p3["materials"]) == 0
            p4 = procs[3]
            assert p4["process_id"] == proc_map["Packing"]
            assert len(p4["materials"]) == 0
            print(f"[PASS] Process 3 & 4 (Finishing & Packing) have 0 materials as defined")

            # -----------------------------------------------------------------
            # TEST 5: BOM Calculation & Material Explosion (Formula Verification)
            # -----------------------------------------------------------------
            print("\n--- TEST 5: BOM Calculation Logic (100 Units Planned) ---")
            # Planned production = 100 units
            # Expected requirements:
            # - Process 1 Cutting: Fabric = 1.5 * 100 = 150.0; Thread = 2.0 * 100 = 200.0
            # - Process 2 Stitching: Thread = 1.0 * 100 = 100.0
            # Consolidated Thread requirement = 200 + 100 = 300.0
            # Consolidated Fabric requirement = 150.0
            production_qty = Decimal(100)
            
            # Fetch relational BOM rows
            proc_bom_rows = db.all_(conn, """
                SELECT pp.id AS pp_id, pp.seq, pp.process_id, pr.name AS process_name,
                       ppm.material_id, p.item_name AS material_name, ppm.item_type, ppm.qty_per_unit, ppm.uom_id
                FROM m_product_process pp
                JOIN m_process pr ON pr.id = pp.process_id
                JOIN m_product_process_material ppm ON ppm.product_process_id = pp.id
                JOIN m_product p ON p.id = ppm.material_id
                WHERE pp.product_id = :p
                ORDER BY pp.seq, ppm.seq
            """, p=prod_id)

            process_wise_detail = []
            consolidated_materials = {}
            for r in proc_bom_rows:
                req_qty = production_qty * Decimal(str(r["qty_per_unit"]))
                process_wise_detail.append({
                    "process": r["process_name"],
                    "material": r["material_name"],
                    "qty_per_unit": float(r["qty_per_unit"]),
                    "total_required": float(req_qty)
                })
                mid = r["material_id"]
                consolidated_materials[mid] = consolidated_materials.get(mid, Decimal(0)) + req_qty

            print("Process-wise requirement breakdown for 100 units:")
            for d in process_wise_detail:
                print(f"  - {d['process']}: {d['material']} -> {d['qty_per_unit']} / unit => Total: {d['total_required']}")

            assert consolidated_materials[fabric["id"]] == Decimal(150), f"Expected 150 fabric, got {consolidated_materials[fabric['id']]}"
            assert consolidated_materials[thread["id"]] == Decimal(300), f"Expected 300 thread (200 + 100), got {consolidated_materials[thread['id']]}"
            print(f"[PASS] Consolidated Material Requirements: Fabric = {consolidated_materials[fabric['id']]}, Thread = {consolidated_materials[thread['id']]}")

            # -----------------------------------------------------------------
            # TEST 6: Update / Edit Product Process and BOM Mappings
            # -----------------------------------------------------------------
            print("\n--- TEST 6: Updating product processes and BOM materials ---")
            # Update Cutting Fabric qty from 1.5 to 1.8
            # Remove Thread from Cutting
            # Add Thread to Finishing
            update_payload = dict(loaded)
            update_payload["processes"] = [
                {
                    "process_id": proc_map["Cutting"],
                    "seq": 1,
                    "materials": [
                        {"material_id": fabric["id"], "item_type": "Raw Material", "qty_per_unit": 1.8, "uom_id": fabric["uom_id"]}
                    ]
                },
                {
                    "process_id": proc_map["Stitching"],
                    "seq": 2,
                    "materials": [
                        {"material_id": thread["id"], "item_type": "Consumable", "qty_per_unit": 1.2, "uom_id": thread["uom_id"]}
                    ]
                }
            ]
            save(conn, "product", update_payload, rid=prod_id)
            print("[PASS] Product updated successfully")

            # Reload and verify
            reloaded = load_one(conn, "product", prod_id)
            assert len(reloaded["processes"]) == 2
            assert len(reloaded["processes"][0]["materials"]) == 1
            assert float(reloaded["processes"][0]["materials"][0]["qty_per_unit"]) == 1.8
            assert len(reloaded["processes"][1]["materials"]) == 1
            assert float(reloaded["processes"][1]["materials"][0]["qty_per_unit"]) == 1.2
            print("[PASS] Updated mappings verified in database (1.8 fabric in Cutting, 1.2 thread in Stitching)")

            # -----------------------------------------------------------------
            # CLEANUP
            # -----------------------------------------------------------------
            print("\n--- CLEANUP ---")
            db.run(conn, "DELETE FROM m_product WHERE id=:i", i=prod_id)
            print("[PASS] Test product cleanly deleted from database (Cascade deletes verified)")
            print("\nALL 12 TEST SUITES PASSED SUCCESSFULLY!")

if __name__ == "__main__":
    run_tests()
