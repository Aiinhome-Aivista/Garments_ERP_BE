"""
Database migration script for BOM Master redesign:
1. m_bom: add bom_name VARCHAR(120) NULL
2. m_bom_item: add godown_id INT NULL, item_type VARCHAR(60) NULL, rate DECIMAL(14,2) NOT NULL DEFAULT 0.00
3. Reuse wastage_pct in m_bom_item for Rate (%)
4. Add foreign key from m_bom_item.godown_id to m_godown.id
"""
import sys
import os

sys.path.insert(0, os.path.dirname(__file__))

from app import db
from app.config import Config
from sqlalchemy import text

def run_migration():
    engine = db.init_engine(Config.DATABASE_URL)
    with engine.begin() as conn:
        print("Checking m_bom table...")
        cols_bom = [r[0] for r in conn.execute(text("DESCRIBE m_bom")).fetchall()]
        if "bom_name" not in cols_bom:
            print("Adding bom_name to m_bom...")
            conn.execute(text("ALTER TABLE `m_bom` ADD COLUMN `bom_name` VARCHAR(120) NULL AFTER `bom_no`"))
            print("bom_name added successfully.")
        else:
            print("bom_name already exists in m_bom.")

        print("\nChecking m_bom_item table...")
        cols_item = [r[0] for r in conn.execute(text("DESCRIBE m_bom_item")).fetchall()]
        if "godown_id" not in cols_item:
            print("Adding godown_id to m_bom_item...")
            conn.execute(text("ALTER TABLE `m_bom_item` ADD COLUMN `godown_id` INT NULL AFTER `material_id`"))
            print("godown_id added successfully.")
        else:
            print("godown_id already exists in m_bom_item.")

        if "item_type" not in cols_item:
            print("Adding item_type to m_bom_item...")
            conn.execute(text("ALTER TABLE `m_bom_item` ADD COLUMN `item_type` VARCHAR(60) NULL AFTER `godown_id`"))
            print("item_type added successfully.")
        else:
            print("item_type already exists in m_bom_item.")

        if "rate" not in cols_item:
            print("Adding rate to m_bom_item...")
            conn.execute(text("ALTER TABLE `m_bom_item` ADD COLUMN `rate` DECIMAL(14,2) NOT NULL DEFAULT 0.00 AFTER `uom_id`"))
            print("rate added successfully.")
        else:
            print("rate already exists in m_bom_item.")

        # Check FK for godown_id
        fk_rows = conn.execute(text("""
            SELECT CONSTRAINT_NAME
            FROM INFORMATION_SCHEMA.KEY_COLUMN_USAGE
            WHERE TABLE_SCHEMA = DATABASE() 
              AND TABLE_NAME = 'm_bom_item' 
              AND COLUMN_NAME = 'godown_id' 
              AND REFERENCED_TABLE_NAME IS NOT NULL
        """)).fetchall()

        if not fk_rows:
            print("Adding FK constraint for godown_id -> m_godown(id)...")
            # Ensure index exists
            conn.execute(text("ALTER TABLE `m_bom_item` ADD KEY `ix_bom_item_godown` (`godown_id`)"))
            conn.execute(text("ALTER TABLE `m_bom_item` ADD CONSTRAINT `fk_bom_item_godown` FOREIGN KEY (`godown_id`) REFERENCES `m_godown`(`id`) ON DELETE SET NULL"))
            print("FK constraint fk_bom_item_godown added successfully.")
        else:
            print(f"FK constraint already exists: {[r[0] for r in fk_rows]}")

    # Final verification
    with engine.begin() as conn:
        print("\n=== Final m_bom Columns ===")
        for c in conn.execute(text("DESCRIBE m_bom")).fetchall():
            print(f"  {c[0]:15} {c[1]:15} Null={c[2]:4} Key={c[3]:4} Default={str(c[4]):10}")

        print("\n=== Final m_bom_item Columns ===")
        for c in conn.execute(text("DESCRIBE m_bom_item")).fetchall():
            print(f"  {c[0]:15} {c[1]:15} Null={c[2]:4} Key={c[3]:4} Default={str(c[4]):10}")

if __name__ == "__main__":
    run_migration()

