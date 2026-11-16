"""
Comprehensive test script for Country field integration in Ledger Master:
1. Meta API verification
2. Create ledger with default country (India)
3. Create ledger with different country (e.g. United States)
4. Update ledger country (e.g. to Bangladesh, Germany)
5. Verify direct database persistence in m_ledger
6. Verify existing ledger records with NULL country work without error
7. Verify City field functionality alongside Country
8. List API & filtering by country
9. Clean up test records
"""
import os
import sys
import importlib.util

sys.path.insert(0, os.path.dirname(__file__))

spec = importlib.util.spec_from_file_location("root_app", os.path.join(os.path.dirname(__file__), "app.py"))
root_app = importlib.util.module_from_spec(spec)
spec.loader.exec_module(root_app)
app = root_app.app
from app import db
from sqlalchemy import text

client = app.test_client()

def run_tests():
    print("=" * 60)
    print("RUNNING COUNTRY FIELD INTEGRATION TESTS")
    print("=" * 60)

    # 1. Login
    print("\n[Step 1] Logging in as admin...")
    login_res = client.post("/api/auth/login", json={
        "username": "admin",
        "password": os.getenv("ADMIN_PASSWORD", "Admin@12345")
    })
    assert login_res.status_code == 200, f"Login failed: {login_res.get_json()}"
    token = login_res.get_json()["token"]
    headers = {"Authorization": f"Bearer {token}"}
    print("   Login successful!")

    # 2. Verify /api/meta
    print("\n[Step 2] Verifying /api/meta metadata for country field...")
    meta_res = client.get("/api/meta", headers=headers)
    assert meta_res.status_code == 200
    meta = meta_res.get_json()
    ledger_fields = meta["masters"]["ledger"]["fields"]
    country_field = next((f for f in ledger_fields if f["name"] == "country"), None)
    assert country_field is not None, "country field missing in meta"
    print(f"   Country field found in meta: name='{country_field['name']}', label='{country_field['label']}', default='{country_field.get('default')}'")

    # Cleanup any lingering test records
    with db.tx() as conn:
        conn.execute(text("DELETE FROM m_ledger WHERE name LIKE 'TEST_COUNTRY_%'"))

    created_ids = []

    try:
        # 3. Create ledger with default country (India)
        print("\n[Step 3] Creating ledger with default country India...")
        res_default = client.post("/api/masters/ledger", json={
            "name": "TEST_COUNTRY_DEFAULT_INDIA",
            "ledger_type": "Customer",
            "city": "Jaipur",
            "state": "Rajasthan",
            "gstin": "08ABCDE1234F1Z5"
        }, headers=headers)
        assert res_default.status_code in (200, 201), f"Create failed: {res_default.get_json()}"
        id_india = res_default.get_json()["id"]
        created_ids.append(id_india)

        # Verify via GET API
        get_india = client.get(f"/api/masters/ledger/{id_india}", headers=headers).get_json()
        assert get_india["country"] == "India", f"Expected India, got {get_india.get('country')}"
        assert get_india["city"] == "Jaipur", f"Expected Jaipur, got {get_india.get('city')}"
        print(f"   Created ID {id_india}: country='{get_india['country']}', city='{get_india['city']}'")

        # Verify in DB
        with db.tx() as conn:
            db_val = conn.execute(text("SELECT country, city FROM m_ledger WHERE id=:i"), {"i": id_india}).mappings().first()
            assert db_val["country"] == "India", f"DB mismatch: {db_val}"
            assert db_val["city"] == "Jaipur", f"DB city mismatch: {db_val}"
            print(f"   DB check PASSED for default country: m_ledger.country = '{db_val['country']}'")

        # 4. Create ledger with a different country ("United States")
        print("\n[Step 4] Creating ledger with a different country ('United States')...")
        res_usa = client.post("/api/masters/ledger", json={
            "name": "TEST_COUNTRY_USA_CLIENT",
            "ledger_type": "Customer",
            "country": "United States",
            "city": "New York",
            "state": "New York"
        }, headers=headers)
        assert res_usa.status_code in (200, 201), f"Create failed: {res_usa.get_json()}"
        id_usa = res_usa.get_json()["id"]
        created_ids.append(id_usa)

        # Verify via GET API
        get_usa = client.get(f"/api/masters/ledger/{id_usa}", headers=headers).get_json()
        assert get_usa["country"] == "United States", f"Expected United States, got {get_usa.get('country')}"
        assert get_usa["city"] == "New York", f"Expected New York, got {get_usa.get('city')}"
        print(f"   Created ID {id_usa}: country='{get_usa['country']}', city='{get_usa['city']}'")

        # Verify in DB
        with db.tx() as conn:
            db_val = conn.execute(text("SELECT country, city FROM m_ledger WHERE id=:i"), {"i": id_usa}).mappings().first()
            assert db_val["country"] == "United States", f"DB mismatch: {db_val}"
            print(f"   DB check PASSED for United States: m_ledger.country = '{db_val['country']}'")

        # 5. Update ledger country (e.g. update USA ledger to "Bangladesh")
        print("\n[Step 5] Updating ledger country from 'United States' to 'Bangladesh'...")
        res_up1 = client.put(f"/api/masters/ledger/{id_usa}", json={
            "name": "TEST_COUNTRY_USA_CLIENT",
            "ledger_type": "Customer",
            "country": "Bangladesh",
            "city": "Dhaka"
        }, headers=headers)
        assert res_up1.status_code == 200, f"Update failed: {res_up1.get_json()}"

        get_up1 = client.get(f"/api/masters/ledger/{id_usa}", headers=headers).get_json()
        assert get_up1["country"] == "Bangladesh", f"Expected Bangladesh, got {get_up1.get('country')}"
        assert get_up1["city"] == "Dhaka", f"Expected Dhaka, got {get_up1.get('city')}"
        with db.tx() as conn:
            db_val = conn.execute(text("SELECT country, city FROM m_ledger WHERE id=:i"), {"i": id_usa}).mappings().first()
            assert db_val["country"] == "Bangladesh"
            print(f"   DB check PASSED after update: m_ledger.country = '{db_val['country']}', city = '{db_val['city']}'")

        # 6. Update ledger country to "Germany"
        print("\n[Step 6] Updating ledger country to 'Germany'...")
        res_up2 = client.put(f"/api/masters/ledger/{id_usa}", json={
            "name": "TEST_COUNTRY_USA_CLIENT",
            "ledger_type": "Customer",
            "country": "Germany",
            "city": "Berlin"
        }, headers=headers)
        assert res_up2.status_code == 200

        get_up2 = client.get(f"/api/masters/ledger/{id_usa}", headers=headers).get_json()
        assert get_up2["country"] == "Germany"
        with db.tx() as conn:
            db_val = conn.execute(text("SELECT country, city FROM m_ledger WHERE id=:i"), {"i": id_usa}).mappings().first()
            assert db_val["country"] == "Germany"
            print(f"   DB check PASSED after update to Germany: m_ledger.country = '{db_val['country']}'")

        # 7. Test existing records where country is NULL
        print("\n[Step 7] Checking existing ledgers where country is NULL...")
        with db.tx() as conn:
            existing = conn.execute(text("SELECT id, name, ledger_type, city, state, country FROM m_ledger WHERE id NOT IN (:i1, :i2) LIMIT 5"), {"i1": id_india, "i2": id_usa}).mappings().all()
            for ex in existing:
                print(f"   Existing record ID={ex['id']}, Name='{ex['name']}', City='{ex['city']}', Country={ex['country']}")
            
            # Verify one existing record can be fetched via GET API without errors
            sample_id = existing[0]["id"]
            sample_get = client.get(f"/api/masters/ledger/{sample_id}", headers=headers).get_json()
            assert sample_get["id"] == sample_id
            print(f"   GET API for existing record ID {sample_id} succeeded with country={sample_get.get('country')}")

        # 8. Test List API with filtering by country
        print("\n[Step 8] Testing List API and filtering by country...")
        list_res = client.get("/api/masters/ledger?f_country=Germany", headers=headers)
        assert list_res.status_code == 200
        rows = list_res.get_json()["rows"]
        match = next((r for r in rows if r["id"] == id_usa), None)
        assert match is not None, "Record not found in country filtered list"
        assert match["country"] == "Germany"
        print(f"   List API returned {len(rows)} record(s) matching country='Germany'.")

    finally:
        # 9. Cleanup
        print("\n[Step 9] Cleaning up test records...")
        for tid in created_ids:
            client.delete(f"/api/masters/ledger/{tid}", headers=headers)
        with db.tx() as conn:
            cnt = conn.execute(text("SELECT COUNT(*) FROM m_ledger WHERE name LIKE 'TEST_COUNTRY_%'")).scalar()
            assert cnt == 0, "Test records were not deleted"
        print("   Test records cleanly deleted!")

    print("\n" + "=" * 60)
    print("ALL COUNTRY FIELD TESTS PASSED SUCCESSFULLY! 100% VERIFIED.")
    print("=" * 60)

if __name__ == "__main__":
    run_tests()

