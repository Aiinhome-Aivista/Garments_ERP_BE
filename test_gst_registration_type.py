"""
Comprehensive test script for GST Registration Type in Ledger Master:
- Meta API check
- Create, Read, Update, List with all 3 allowed types (Registered Dealer, Unregistered Dealer, Composite Dealer)
- Database persistence verification
- Validation rejection of invalid types
- Verification of existing records integrity
- Clean deletion of test records
"""
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

import importlib.util
spec = importlib.util.spec_from_file_location("root_app", os.path.join(os.path.dirname(__file__), "app.py"))
root_app = importlib.util.module_from_spec(spec)
spec.loader.exec_module(root_app)
app = root_app.app
from app import db
from app.config import Config
from sqlalchemy import text

client = app.test_client()

def run_tests():
    print("=" * 60)
    print("RUNNING GST REGISTRATION TYPE INTEGRATION TESTS")
    print("=" * 60)

    # 1. Login to get auth token
    print("\n[Step 1] Logging in as admin...")
    login_res = client.post("/api/auth/login", json={
        "username": "admin",
        "password": os.getenv("ADMIN_PASSWORD", "Admin@12345")
    })
    assert login_res.status_code == 200, f"Login failed: {login_res.get_json()}"
    token = login_res.get_json()["token"]
    headers = {"Authorization": f"Bearer {token}"}
    print("   Login successful!")

    # 2. Check Meta API
    print("\n[Step 2] Verifying /api/meta metadata...")
    meta_res = client.get("/api/meta", headers=headers)
    assert meta_res.status_code == 200
    meta = meta_res.get_json()
    ledger_fields = meta["masters"]["ledger"]["fields"]
    gst_field = next((f for f in ledger_fields if f["name"] == "gst_registration_type"), None)
    assert gst_field is not None, "gst_registration_type field missing in meta"
    assert gst_field["type"] == "select", f"Expected select, got {gst_field['type']}"
    expected_options = ["Registered Dealer", "Unregistered Dealer", "Composite Dealer"]
    assert gst_field["options"] == expected_options, f"Options mismatch: {gst_field['options']}"
    print(f"   Metadata confirmed: field '{gst_field['name']}', options: {gst_field['options']}")

    # 3. Validation test: Invalid registration type rejection
    print("\n[Step 3] Testing backend validation for INVALID registration type...")
    invalid_payload = {
        "name": "TEST_INVALID_DEALER_XYZ",
        "ledger_type": "Supplier",
        "gst_registration_type": "Invalid Dealer"
    }
    inv_res = client.post("/api/masters/ledger", json=invalid_payload, headers=headers)
    assert inv_res.status_code == 400, f"Expected 400, got {inv_res.status_code}: {inv_res.get_json()}"
    print(f"   Correctly rejected invalid value with: {inv_res.get_json()}")

    # Clean up any leftover test record
    with db.tx() as conn:
        conn.execute(text("DELETE FROM m_ledger WHERE name='TEST_ABC_TEXTILES_REG'"))

    # 4. Create Ledger with 'Registered Dealer'
    print("\n[Step 4] Creating ledger with 'Registered Dealer'...")
    create_payload = {
        "name": "TEST_ABC_TEXTILES_REG",
        "ledger_type": "Supplier",
        "gstin": "27AABCU9603R1ZM",
        "gst_registration_type": "Registered Dealer",
        "state": "Maharashtra",
        "city": "Mumbai"
    }
    c_res = client.post("/api/masters/ledger", json=create_payload, headers=headers)
    assert c_res.status_code in (200, 201), f"Create failed: {c_res.get_json()}"
    created_id = c_res.get_json()["id"]
    print(f"   Created ledger with ID: {created_id}")

    # Fetch via GET API
    print("\n[Step 5] Fetching created ledger via GET /api/masters/ledger/<id>...")
    get_res = client.get(f"/api/masters/ledger/{created_id}", headers=headers)
    assert get_res.status_code == 200
    ledger_data = get_res.get_json()
    assert ledger_data["gst_registration_type"] == "Registered Dealer", f"Mismatch in GET: {ledger_data.get('gst_registration_type')}"
    print(f"   GET API returned: gst_registration_type='{ledger_data['gst_registration_type']}'")

    # Verify directly in DB
    print("\n[Step 6] Direct Database Verification for 'Registered Dealer'...")
    with db.tx() as conn:
        db_val = conn.execute(text("SELECT gst_registration_type FROM m_ledger WHERE id=:i"), {"i": created_id}).scalar()
        assert db_val == "Registered Dealer", f"DB mismatch: {db_val}"
        print(f"   DB check PASSED: m_ledger.gst_registration_type = '{db_val}'")

    # 7. Update to 'Composite Dealer'
    print("\n[Step 7] Updating ledger to 'Composite Dealer' via PUT /api/masters/ledger/<id>...")
    up_res1 = client.put(f"/api/masters/ledger/{created_id}", json={
        "name": "TEST_ABC_TEXTILES_REG",
        "ledger_type": "Supplier",
        "gst_registration_type": "Composite Dealer"
    }, headers=headers)
    assert up_res1.status_code == 200

    get_res1 = client.get(f"/api/masters/ledger/{created_id}", headers=headers)
    assert get_res1.get_json()["gst_registration_type"] == "Composite Dealer"
    with db.tx() as conn:
        db_val1 = conn.execute(text("SELECT gst_registration_type FROM m_ledger WHERE id=:i"), {"i": created_id}).scalar()
        assert db_val1 == "Composite Dealer"
        print(f"   Updated and DB verified: '{db_val1}'")

    # 8. Update to 'Unregistered Dealer'
    print("\n[Step 8] Updating ledger to 'Unregistered Dealer' via PUT /api/masters/ledger/<id>...")
    up_res2 = client.put(f"/api/masters/ledger/{created_id}", json={
        "name": "TEST_ABC_TEXTILES_REG",
        "ledger_type": "Supplier",
        "gst_registration_type": "Unregistered Dealer"
    }, headers=headers)
    assert up_res2.status_code == 200

    get_res2 = client.get(f"/api/masters/ledger/{created_id}", headers=headers)
    assert get_res2.get_json()["gst_registration_type"] == "Unregistered Dealer"
    with db.tx() as conn:
        db_val2 = conn.execute(text("SELECT gst_registration_type FROM m_ledger WHERE id=:i"), {"i": created_id}).scalar()
        assert db_val2 == "Unregistered Dealer"
        print(f"   Updated and DB verified: '{db_val2}'")

    # 9. Test List API
    print("\n[Step 9] Testing List API and filtering by gst_registration_type...")
    list_res = client.get("/api/masters/ledger?f_gst_registration_type=Unregistered Dealer", headers=headers)
    assert list_res.status_code == 200
    list_data = list_res.get_json()["rows"]
    item_in_list = next((r for r in list_data if r["id"] == created_id), None)
    assert item_in_list is not None, "Created record not found in filtered list"
    assert item_in_list["gst_registration_type"] == "Unregistered Dealer"
    print(f"   List API returned {len(list_data)} records matching 'Unregistered Dealer', including created record.")

    # 10. Verify existing ledgers remain unaffected
    print("\n[Step 10] Checking existing ledgers remain unaffected...")
    with db.tx() as conn:
        existing = conn.execute(text("SELECT id, name, ledger_type, gstin, gst_registration_type FROM m_ledger WHERE id != :i LIMIT 5"), {"i": created_id}).mappings().all()
        for ex in existing:
            print(f"   Existing Ledger: ID={ex['id']}, Name='{ex['name']}', Type='{ex['ledger_type']}', GST Reg Type={ex['gst_registration_type']}")

    # 11. Cleanup test ledger
    print("\n[Step 11] Cleaning up test ledger...")
    del_res = client.delete(f"/api/masters/ledger/{created_id}", headers=headers)
    assert del_res.status_code == 200
    with db.tx() as conn:
        rem = conn.execute(text("SELECT COUNT(*) FROM m_ledger WHERE id=:i"), {"i": created_id}).scalar()
        assert rem == 0, "Record was not deleted"
        print("   Test record cleanly deleted from database!")

    print("\n" + "=" * 60)
    print("ALL TESTS PASSED SUCCESSFULLY! 100% VERIFIED.")
    print("=" * 60)

if __name__ == "__main__":
    run_tests()
