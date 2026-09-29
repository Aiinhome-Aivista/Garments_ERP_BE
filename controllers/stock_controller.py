"""Barcode scan lookup and stock reports."""
from flask import jsonify, request

from app import db
from controllers.auth_controller import login_required, require


PRODUCT_SQL = ("SELECT p.id product_id, p.item_name, p.gst_rate, p.barcode product_barcode, u.name uom, "
               "p.default_sales_ledger_id, p.default_purchase_ledger_id FROM m_product p JOIN m_uom u ON u.id=p.uom_id")

def on_hand(conn, pid):
    return db.scalar(conn, "SELECT COALESCE(SUM(qty),0) FROM stock_ledger WHERE product_id=:p", p=pid)

def reserved(conn, pid):
    return db.scalar(conn, "SELECT COALESCE(SUM(qty),0) FROM stock_reservation WHERE product_id=:p AND active=1", p=pid)

def free_stock(conn, pid):
    return float(on_hand(conn, pid)) - float(reserved(conn, pid))


@login_required
def scan(code):
    """Resolve a scanned code: a lot barcode (from GRN) or a product barcode (from the item master)."""
    code = code.strip()
    ref = request.args.get("ref_voucher_id")
    with db.tx() as conn:
        lot_sql = ("SELECT s.barcode, s.godown_id, g.name godown_label, s.bin_no, SUM(s.qty) balance, MIN(s.txn_date) since "
                   "FROM stock_ledger s JOIN m_godown g ON g.id=s.godown_id WHERE {w} GROUP BY s.barcode,s.godown_id,s.bin_no,g.name")
        lots = db.all_(conn, lot_sql.format(w="s.barcode=:c") + " HAVING SUM(s.qty)>0 ORDER BY since", c=code)
        prod, kind = None, None
        if lots:
            kind = "lot"
            prod = db.one(conn, PRODUCT_SQL + " WHERE p.id=(SELECT product_id FROM stock_ledger WHERE barcode=:c LIMIT 1)", c=code)
        else:
            known = db.one(conn, "SELECT product_id, godown_id, bin_no FROM txn_item WHERE barcode=:c ORDER BY id LIMIT 1", c=code)
            if known:                                         # a lot that is empty right now (e.g. returns)
                kind = "lot"
                prod = db.one(conn, PRODUCT_SQL + " WHERE p.id=:p", p=known["product_id"])
                gd = db.one(conn, "SELECT id,name FROM m_godown WHERE id=:g", g=known["godown_id"]) if known["godown_id"] else None
                lots = [{"barcode": code, "godown_id": gd["id"] if gd else None, "godown_label": gd["name"] if gd else None,
                         "bin_no": known["bin_no"] or "", "balance": 0}]
            else:
                prod = db.one(conn, PRODUCT_SQL + " WHERE p.barcode=:c", c=code)
                if prod:
                    kind = "product"
                    lots = db.all_(conn, lot_sql.format(w="s.product_id=:p") + " HAVING SUM(s.qty)>0 ORDER BY since", p=prod["product_id"])
        if not prod:
            return jsonify({"error": f"No item or lot found for barcode {code}"}), 404
        ref_item = None
        if ref and lots:
            ref_item = db.one(conn, "SELECT rate,disc_pct,disc_amt,pricelist_id,description,customer_item_name,ref_no,salesman_id,broker_id,"
                                    "retailer_name,(SELECT SUM(qty) FROM txn_item WHERE header_id=:h AND barcode=:b) AS max_qty "
                                    "FROM txn_item WHERE header_id=:h AND barcode=:b LIMIT 1", h=ref, b=lots[0]["barcode"])
    return jsonify({"kind": kind, "product": prod, "lots": lots, "ref_item": ref_item})


@require("stock_report", "view")
def report():
    a = request.args
    mode = a.get("mode", "product")
    q = f"%{(a.get('q') or '').strip()}%"
    with db.tx() as conn:
        if mode == "lot":
            rows = db.all_(conn, "SELECT s.barcode,p.item_name,g.name godown,s.bin_no,SUM(s.qty) balance,u.name uom,MIN(s.txn_date) since "
                                 "FROM stock_ledger s JOIN m_product p ON p.id=s.product_id JOIN m_godown g ON g.id=s.godown_id "
                                 "JOIN m_uom u ON u.id=p.uom_id WHERE (p.item_name LIKE :q OR s.barcode LIKE :q) "
                                 "GROUP BY s.barcode,p.item_name,g.name,s.bin_no,u.name HAVING SUM(s.qty)<>0 ORDER BY p.item_name,since LIMIT 1000", q=q)
        else:
            rows = db.all_(conn, "SELECT p.id product_id,p.item_name,c.name category,u.name uom,p.min_stock,"
                                 "COALESCE((SELECT SUM(qty) FROM stock_ledger WHERE product_id=p.id),0) on_hand,"
                                 "COALESCE((SELECT SUM(qty) FROM stock_reservation WHERE product_id=p.id AND active=1),0) reserved "
                                 "FROM m_product p JOIN m_uom u ON u.id=p.uom_id JOIN m_product_category c ON c.id=p.category_id "
                                 "WHERE p.active=1 AND p.item_name LIKE :q ORDER BY p.item_name LIMIT 1000", q=q)
            for r in rows:
                r["free"] = float(r["on_hand"]) - float(r["reserved"])
    return jsonify(rows)
