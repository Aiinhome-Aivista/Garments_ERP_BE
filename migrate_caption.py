from sqlalchemy import create_engine, text
e = create_engine('mysql+pymysql://root:root@127.0.0.1:3306/garment_erp?charset=utf8mb4')
with e.begin() as conn:
    try:
        conn.execute(text("ALTER TABLE m_txn_type_terms ADD COLUMN caption VARCHAR(100) NOT NULL DEFAULT '' AFTER sl_no"))
        print("Column added")
    except Exception as ex:
        print("Error:", ex)
