import sys
import os

sys.path.insert(0, os.path.dirname(__file__))

from app import db
from app.config import Config
from sqlalchemy import text

def run_migration():
    db.init_engine(Config.DATABASE_URL)
    with db.tx() as conn:
        print("Checking if product_type column exists in m_product...")
        check_sql = text("""
            SELECT COUNT(*) 
            FROM information_schema.columns 
            WHERE table_schema = DATABASE() 
              AND table_name = 'm_product' 
              AND column_name = 'product_type'
        """)
        exists = conn.execute(check_sql).scalar()
        if exists:
            print("Column 'product_type' already exists in 'm_product'.")
        else:
            print("Adding 'product_type' VARCHAR(30) NOT NULL DEFAULT 'Finished' to 'm_product'...")
            alter_sql = text("""
                ALTER TABLE m_product 
                ADD COLUMN product_type VARCHAR(30) NOT NULL DEFAULT 'Finished' AFTER id
            """)
            conn.execute(alter_sql)
            print("Column 'product_type' successfully added!")

        # Verify
        cols = conn.execute(text("DESCRIBE m_product")).mappings().all()
        print("Columns in m_product:")
        for c in cols:
            if c['Field'] in ('id', 'product_type', 'name', 'item_name'):
                print(f" - {c['Field']}: {c['Type']}, Nullable={c['Null']}, Default={c['Default']}")

if __name__ == "__main__":
    run_migration()
