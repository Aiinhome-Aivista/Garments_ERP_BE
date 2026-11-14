"""
Database migration script to create BOM Master tables:
1. m_bom
2. m_bom_item
"""
import sys
import os

sys.path.insert(0, os.path.dirname(__file__))

from app import db
from app.config import Config
from sqlalchemy import text

DDL_BOM = """
CREATE TABLE IF NOT EXISTS m_bom (
  id INT AUTO_INCREMENT PRIMARY KEY,
  bom_no VARCHAR(60) NOT NULL UNIQUE,
  product_id INT NOT NULL,
  batch_qty DECIMAL(14,4) NOT NULL DEFAULT 1.0000,
  uom_id INT NOT NULL,
  
  remarks VARCHAR(500) NULL,
  active TINYINT(1) NOT NULL DEFAULT 1,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  KEY ix_bom_product (product_id),
  KEY ix_bom_uom (uom_id),
  FOREIGN KEY (product_id) REFERENCES m_product(id),
  FOREIGN KEY (uom_id) REFERENCES m_uom(id)
) ENGINE=InnoDB;
"""

DDL_BOM_ITEM = """
CREATE TABLE IF NOT EXISTS m_bom_item (
  id INT AUTO_INCREMENT PRIMARY KEY,
  bom_id INT NOT NULL,
  seq INT NOT NULL DEFAULT 0,
  material_id INT NOT NULL,
  qty_per_unit DECIMAL(14,4) NOT NULL DEFAULT 0.0000,
  uom_id INT NOT NULL,
  wastage_pct DECIMAL(6,2) NOT NULL DEFAULT 0.00,
  remarks VARCHAR(255) NULL,
  KEY ix_bom_item_bom (bom_id),
  KEY ix_bom_item_material (material_id),
  KEY ix_bom_item_uom (uom_id),
  FOREIGN KEY (bom_id) REFERENCES m_bom(id) ON DELETE CASCADE,
  FOREIGN KEY (material_id) REFERENCES m_product(id),
  FOREIGN KEY (uom_id) REFERENCES m_uom(id)
) ENGINE=InnoDB;
"""

def migrate():
    engine = db.init_engine(Config.DATABASE_URL)
    with engine.begin() as conn:
        print("Executing DDL for m_bom...")
        conn.execute(text(DDL_BOM))
        print("m_bom created/verified.")

        print("Executing DDL for m_bom_item...")
        conn.execute(text(DDL_BOM_ITEM))
        print("m_bom_item created/verified.")

    # Verification
    with engine.begin() as conn:
        print("\n--- Verifying m_bom columns ---")
        cols = conn.execute(text("DESCRIBE m_bom")).fetchall()
        for c in cols:
            print(f"  {c[0]:15} {c[1]:15} Null={c[2]:4} Key={c[3]:4} Default={str(c[4]):10}")

        print("\n--- Verifying m_bom_item columns ---")
        cols = conn.execute(text("DESCRIBE m_bom_item")).fetchall()
        for c in cols:
            print(f"  {c[0]:15} {c[1]:15} Null={c[2]:4} Key={c[3]:4} Default={str(c[4]):10}")

        print("\n--- Verifying Foreign Keys ---")
        fks = conn.execute(text("""
            SELECT TABLE_NAME, COLUMN_NAME, CONSTRAINT_NAME, REFERENCED_TABLE_NAME, REFERENCED_COLUMN_NAME
            FROM INFORMATION_SCHEMA.KEY_COLUMN_USAGE
            WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME IN ('m_bom', 'm_bom_item') AND REFERENCED_TABLE_NAME IS NOT NULL
        """)).fetchall()
        for fk in fks:
            print(f"  {fk[0]}.{fk[1]} -> {fk[3]}.{fk[4]} ({fk[2]})")

if __name__ == "__main__":
    migrate()

