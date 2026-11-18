"""
Database migration script to create m_product_process_material table
and migrate any existing m_product_process items.
"""
import os, sys
sys.path.insert(0, os.path.dirname(__file__))

from app import db
from app.config import Config
from sqlalchemy import text

DDL_PRODUCT_PROCESS_MATERIAL = """
CREATE TABLE IF NOT EXISTS `m_product_process_material` (
  `id` INT AUTO_INCREMENT PRIMARY KEY,
  `product_process_id` INT NOT NULL,
  `material_id` INT NOT NULL,
  `item_type` VARCHAR(80) NULL,
  `qty_per_unit` DECIMAL(14,4) NOT NULL DEFAULT 0.0000,
  `uom_id` INT NOT NULL,
  `seq` INT NOT NULL DEFAULT 0,
  `created_at` TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  `updated_at` TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  KEY `ix_ppm_product_process` (`product_process_id`),
  KEY `ix_ppm_material` (`material_id`),
  KEY `ix_ppm_uom` (`uom_id`),
  FOREIGN KEY (`product_process_id`) REFERENCES `m_product_process`(`id`) ON DELETE CASCADE,
  FOREIGN KEY (`material_id`) REFERENCES `m_product`(`id`),
  FOREIGN KEY (`uom_id`) REFERENCES `m_uom`(`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
"""

def run_migration():
    engine = db.init_engine(Config.DATABASE_URL)
    with engine.begin() as conn:
        print("Creating table m_product_process_material if not exists...")
        conn.execute(text(DDL_PRODUCT_PROCESS_MATERIAL))
        print("Table m_product_process_material created or already exists.")

        # Migrate existing rows from m_product_process that have material_id
        count = conn.execute(text("SELECT COUNT(*) FROM m_product_process_material")).scalar() or 0
        if count == 0:
            print("Checking existing rows in m_product_process to migrate...")
            legacy_rows = conn.execute(text("""
                SELECT pp.id, pp.material_id, pp.qty, p.uom_id, COALESCE(c.name, 'Raw Material') AS cat_name
                FROM m_product_process pp
                JOIN m_product p ON p.id = pp.material_id
                LEFT JOIN m_product_category c ON c.id = p.category_id
                WHERE pp.material_id IS NOT NULL AND pp.qty > 0
            """)).mappings().fetchall()
            for r in legacy_rows:
                conn.execute(text("""
                    INSERT INTO m_product_process_material (product_process_id, material_id, item_type, qty_per_unit, uom_id, seq)
                    VALUES (:pp_id, :mat_id, :item_type, :qty, :uom_id, 1)
                """), {
                    "pp_id": r["id"],
                    "mat_id": r["material_id"],
                    "item_type": r["cat_name"],
                    "qty": r["qty"],
                    "uom_id": r["uom_id"]
                })
            print(f"Migrated {len(legacy_rows)} legacy material rows into m_product_process_material.")
        else:
            print(f"m_product_process_material already has {count} rows.")

if __name__ == "__main__":
    run_migration()
