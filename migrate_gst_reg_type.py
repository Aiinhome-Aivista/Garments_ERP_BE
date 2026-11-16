"""
Database migration script to add gst_registration_type column to m_ledger.
"""
import sys
import os

sys.path.insert(0, os.path.dirname(__file__))

from app import db
from app.config import Config
from sqlalchemy import text

def run_migration():
    db.init_engine(Config.DATABASE_URL)
    with db.tx() as conn:
        print("Checking if gst_registration_type column exists in m_ledger...")
        check_sql = text("""
            SELECT COUNT(*) 
            FROM information_schema.columns 
            WHERE table_schema = DATABASE() 
              AND table_name = 'm_ledger' 
              AND column_name = 'gst_registration_type'
        """)
        exists = conn.execute(check_sql).scalar()
        if exists:
            print("Column 'gst_registration_type' already exists in 'm_ledger'.")
        else:
            print("Adding 'gst_registration_type' VARCHAR(40) NULL AFTER 'gstin' to 'm_ledger'...")
            alter_sql = text("""
                ALTER TABLE m_ledger 
                ADD COLUMN gst_registration_type VARCHAR(40) NULL AFTER gstin
            """)
            conn.execute(alter_sql)
            print("Column 'gst_registration_type' successfully added!")

        # Verify
        print("\nVerifying columns in m_ledger:")
        cols = conn.execute(text("DESCRIBE m_ledger")).mappings().all()
        for c in cols:
            if c['Field'] in ('id', 'name', 'gstin', 'gst_registration_type', 'pan_no'):
                print(f" - {c['Field']}: {c['Type']}, Nullable={c['Null']}, Default={c['Default']}")

        total_rows = conn.execute(text("SELECT COUNT(*) FROM m_ledger")).scalar()
        print(f"\nTotal records in m_ledger: {total_rows} (unaffected)")

if __name__ == "__main__":
    run_migration()

