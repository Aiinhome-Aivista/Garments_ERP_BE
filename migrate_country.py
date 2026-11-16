"""
Database migration script to add country column to m_ledger.
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
        print("Checking if country column exists in m_ledger...")
        check_sql = text("""
            SELECT COUNT(*) 
            FROM information_schema.columns 
            WHERE table_schema = DATABASE() 
              AND table_name = 'm_ledger' 
              AND column_name = 'country'
        """)
        exists = conn.execute(check_sql).scalar()
        if exists:
            print("Column 'country' already exists in 'm_ledger'.")
        else:
            print("Adding 'country' VARCHAR(100) NULL AFTER 'address' to 'm_ledger'...")
            alter_sql = text("""
                ALTER TABLE m_ledger 
                ADD COLUMN country VARCHAR(100) NULL AFTER address
            """)
            conn.execute(alter_sql)
            print("Column 'country' successfully added!")

        # Verify
        print("\nVerifying columns in m_ledger:")
        cols = conn.execute(text("DESCRIBE m_ledger")).mappings().all()
        for c in cols:
            if c['Field'] in ('id', 'name', 'address', 'country', 'city', 'state'):
                print(f" - {c['Field']}: {c['Type']}, Nullable={c['Null']}, Default={c['Default']}")

        total_rows = conn.execute(text("SELECT COUNT(*) FROM m_ledger")).scalar()
        print(f"\nTotal records in m_ledger: {total_rows} (unaffected)")

if __name__ == "__main__":
    run_migration()

