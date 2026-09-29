import os

path = r"d:\work\ts\garment-erp\backend\controllers\vouchers_controller.py"
with open(path, "r", encoding="utf-8") as f:
    content = f.read()

old_code = '''def next_number(conn, txn_type_id, kind, commit=True):
    tt = db.one(conn, "SELECT * FROM m_txn_type WHERE id=:i" + (" FOR UPDATE" if commit else ""), i=txn_type_id)'''

new_code = '''def next_number(conn, txn_type_id, kind, commit=True):
    if not txn_type_id:
        tt = db.one(conn, "SELECT * FROM m_txn_type WHERE txn_kind=:k AND active=1 LIMIT 1", k=kind)
        if not tt:
            raise ApiError(f"Please create a Transaction Type for '{kind}' first in Masters -> System")
        txn_type_id = tt["id"]
    tt = db.one(conn, "SELECT * FROM m_txn_type WHERE id=:i" + (" FOR UPDATE" if commit else ""), i=txn_type_id)'''

content = content.replace(old_code, new_code)

with open(path, "w", encoding="utf-8") as f:
    f.write(content)

print("Fixed next_number fallback")
