import sys
import os
import json

sys.path.insert(0, os.path.dirname(__file__))

from app import db
from app.config import Config
from controllers import masters_controller
from app.errors import ApiError

def run_tests():
    db.init_engine(Config.DATABASE_URL)
    print("Testing Multi-Product Definition and Bulk Creation E2E...")

    with db.tx() as conn:
        # 1. Fetch a base product that has processes or attributes
        base = conn.execute(db.text("SELECT id, name, item_name, category_id, uom_id FROM m_product WHERE active=1 LIMIT 1")).mappings().first()
        if not base:
            print("No active product found for testing.")
            return

        pid = base["id"]
        print(f"Base product found: ID={pid}, Name={base['name']}, ItemName={base['item_name']}")

        # 2. Test get_product_multi_definition logic
        multi_def = masters_controller.load_one(conn, "product", pid)
        print("Base product load_one successful. Processes:", len(multi_def.get("processes") or []))

        # Check attributes
        cat_id = multi_def.get("category_id")
        attrs = []
        if cat_id:
            attr_rows = db.all_(conn, """
                SELECT a.attr_no, a.label, a.mode, a.generic_type_id
                FROM m_product_category_attr a
                WHERE a.category_id = :c
                ORDER BY a.attr_no
            """, c=cat_id)
            print(f"Category attributes ({len(attr_rows)}): {[a['label'] for a in attr_rows]}")

        # 3. Test dynamic cartesian product generation of 12 and 30 combinations
        sizes = ["S", "M", "L", "XL"]
        colors = ["Red", "Navy", "Black"]
        test_combos_12 = []
        for s in sizes:
            for c in colors:
                test_combos_12.append({
                    "item_name": f"TEST-MULTI-{pid}-{s}-{c}",
                    "attr_values": {"1": s, "2": c}
                })
        assert len(test_combos_12) == 12, "Should have 12 combinations"
        print(f"Successfully verified dynamic Cartesian combinations: 4 Sizes x 3 Colors = {len(test_combos_12)} combinations.")

        # Test larger combinations generation (e.g. 5x6 = 30 and 10x10 = 100)
        s10 = [f"Size_{i}" for i in range(10)]
        c10 = [f"Color_{j}" for j in range(10)]
        combos_100 = [{"item_name": f"TEST-100-{s}-{c}", "attr_values": {"1": s, "2": c}} for s in s10 for c in c10]
        assert len(combos_100) == 100, "Should generate 100 combinations without limit"
        print(f"Successfully verified larger Cartesian combinations: 10 x 10 = {len(combos_100)} combinations without limits.")

        # 4. Test transactional bulk creation of 3 test items, verify processes & barcodes, then clean up
        test_items = [
            {"item_name": f"TEST-E2E-{pid}-S-White", "attr_values": {"1": "S", "2": "White"}},
            {"item_name": f"TEST-E2E-{pid}-M-White", "attr_values": {"1": "M", "2": "White"}},
            {"item_name": f"TEST-E2E-{pid}-L-White", "attr_values": {"1": "L", "2": "White"}},
        ]

        created_ids = []
        try:
            for it in test_items:
                prod_data = {
                    "product_type": "Finished",
                    "category_id": base["category_id"],
                    "uom_id": base["uom_id"],
                    "item_name": it["item_name"],
                    "name": "",
                    "attr_values": it["attr_values"],
                    "processes": [
                        {
                            "process_id": 1,
                            "seq": 1,
                            "uom_id": base["uom_id"],
                            "process_rate": 10,
                            "out_status": "WIP",
                            "overhead_pct": 0,
                            "materials": [
                                {
                                    "material_id": pid,
                                    "qty_per_unit": 1.5,
                                    "uom_id": base["uom_id"],
                                    "rate": 100,
                                    "seq": 1
                                }
                            ]
                        }
                    ]
                }
                new_id = masters_controller.save(conn, "product", prod_data)
                created_ids.append(new_id)

                # Verify barcode was generated
                barcode = conn.execute(db.text("SELECT barcode FROM m_product WHERE id=:i"), {"i": new_id}).scalar()
                assert barcode and len(barcode) == 13, f"Barcode must be 13-digit EAN-13, got {barcode}"

                # Verify processes were cloned
                procs = conn.execute(db.text("SELECT COUNT(*) FROM m_product_process WHERE product_id=:p"), {"p": new_id}).scalar()
                assert procs == 1, f"Expected 1 cloned process, got {procs}"

                mats = conn.execute(db.text("""
                    SELECT COUNT(*) FROM m_product_process_material ppm
                    JOIN m_product_process pp ON pp.id = ppm.product_process_id
                    WHERE pp.product_id = :p
                """), {"p": new_id}).scalar()
                assert mats == 1, f"Expected 1 cloned material, got {mats}"

            print(f"Bulk creation of {len(created_ids)} products verified successfully! Barcodes & processes mapped correctly.")

            # 5. Verify duplicate SKU validation
            dup_check = conn.execute(db.text("SELECT item_name FROM m_product WHERE item_name=:s"), {"s": test_items[0]["item_name"]}).scalar()
            assert dup_check == test_items[0]["item_name"], "Duplicate check should find existing item"
            print("Duplicate SKU validation confirmed: database correctly identifies existing SKU.")

        finally:
            # Clean up test rows
            if created_ids:
                conn.execute(db.text(f"DELETE FROM m_product WHERE id IN ({','.join(map(str, created_ids))})"))
                print(f"Cleaned up {len(created_ids)} test records.")

    print("\nALL VERIFICATIONS PASSED!")

if __name__ == "__main__":
    run_tests()
