"""Barcode scan lookup and stock reports."""
from flask import jsonify, request

from app import db
from controllers.auth_controller import login_required, require


PRODUCT_SQL = ("SELECT p.id product_id, p.item_name, p.gst_rate, p.barcode product_barcode, u.name uom, "
               "p.default_sales_ledger_id, p.default_purchase_ledger_id, p.enable_batch FROM m_product p JOIN m_uom u ON u.id=p.uom_id")

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


def make_composite_str(qty_val, main_uom, alt_uoms):
    if not alt_uoms:
        return None
    q = float(qty_val or 0)
    if q <= 0:
        return None
    cand = next((a for a in alt_uoms if float(a.get("conversion_factor") or 1) > 1), alt_uoms[0])
    factor = float(cand.get("conversion_factor") or 1)
    if factor <= 1:
        return None
    alt_name = cand.get("name") or "ALT"
    alt_qty = int(q // factor)
    rem = round(q % factor, 3)
    if alt_qty > 0 and rem > 0:
        return f"{alt_qty} {alt_name} + {rem:g} {main_uom}"
    elif alt_qty > 0:
        return f"{alt_qty} {alt_name}"
    return f"{rem:g} {main_uom}"


@require("stock_report", "view")
def report():
    a = request.args
    mode = a.get("mode", "product")
    q = f"%{(a.get('q') or '').strip()}%"
    with db.tx() as conn:
        p = {"q": q}
        if mode == "lot":
            where = ["(p.item_name LIKE :q OR s.barcode LIKE :q OR g.name LIKE :q OR s.bin_no LIKE :q)"]
            if a.get("s_barcode"): where.append("s.barcode LIKE :s_barcode"); p["s_barcode"] = f"%{a['s_barcode']}%"
            if a.get("s_item_name"): where.append("p.item_name LIKE :s_item_name"); p["s_item_name"] = f"%{a['s_item_name']}%"
            if a.get("s_godown"): where.append("g.name LIKE :s_godown"); p["s_godown"] = f"%{a['s_godown']}%"
            if a.get("s_bin_no"): where.append("s.bin_no LIKE :s_bin_no"); p["s_bin_no"] = f"%{a['s_bin_no']}%"
            where_str = " AND ".join(where)
            
            rows = db.all_(conn, f"SELECT s.barcode,p.id product_id,p.item_name,g.name godown,s.bin_no,SUM(s.qty) balance,u.name uom,MIN(s.txn_date) since "
                                 f"FROM stock_ledger s JOIN m_product p ON p.id=s.product_id JOIN m_godown g ON g.id=s.godown_id "
                                 f"JOIN m_uom u ON u.id=p.uom_id WHERE {where_str} "
                                 f"GROUP BY s.barcode,p.id,p.item_name,g.name,s.bin_no,u.name HAVING SUM(s.qty)<>0 ORDER BY p.item_name,since LIMIT 1000", **p)
            for r in rows:
                alts = db.all_(conn, "SELECT pu.uom_id, u.name, pu.conversion_factor FROM m_product_uom pu JOIN m_uom u ON u.id=pu.uom_id WHERE pu.product_id=:pid ORDER BY pu.conversion_factor DESC", pid=r["product_id"])
                r["alternate_uoms"] = alts
                r["composite"] = make_composite_str(r["balance"], r["uom"], alts)
        else:
            where = ["(p.item_name LIKE :q OR c.name LIKE :q)"]
            if a.get("s_item_name"): where.append("p.item_name LIKE :s_item_name"); p["s_item_name"] = f"%{a['s_item_name']}%"
            if a.get("s_category"): where.append("c.name LIKE :s_category"); p["s_category"] = f"%{a['s_category']}%"
            where_str = " AND ".join(where)
            
            rows = db.all_(conn, f"SELECT p.id product_id,p.item_name,c.name category,u.name uom,p.min_stock,"
                                 f"COALESCE((SELECT SUM(qty) FROM stock_ledger WHERE product_id=p.id),0) on_hand,"
                                 f"COALESCE((SELECT SUM(qty) FROM stock_reservation WHERE product_id=p.id AND active=1),0) reserved "
                                 f"FROM m_product p JOIN m_uom u ON u.id=p.uom_id JOIN m_product_category c ON c.id=p.category_id "
                                 f"WHERE p.active=1 AND {where_str} ORDER BY p.item_name LIMIT 1000", **p)
            for r in rows:
                r["free"] = float(r["on_hand"]) - float(r["reserved"])
                alts = db.all_(conn, "SELECT pu.uom_id, u.name, pu.conversion_factor FROM m_product_uom pu JOIN m_uom u ON u.id=pu.uom_id WHERE pu.product_id=:pid ORDER BY pu.conversion_factor DESC", pid=r["product_id"])
                r["alternate_uoms"] = alts
                r["composite_on_hand"] = make_composite_str(r["on_hand"], r["uom"], alts)
                r["composite_free"] = make_composite_str(r["free"], r["uom"], alts)
    return jsonify(rows)


@login_required
def balance():
    pid = request.args.get("product_id")
    bc = request.args.get("barcode")
    gid = request.args.get("godown_id")
    bno = request.args.get("bin_no")
    with db.tx() as conn:
        if bc and gid:
            bal = db.scalar(conn, "SELECT COALESCE(SUM(qty),0) FROM stock_ledger WHERE barcode=:b AND godown_id=:g AND bin_no=:n", b=bc, g=gid, n=bno or "")
        elif pid and gid:
            bal = db.scalar(conn, "SELECT COALESCE(SUM(qty),0) FROM stock_ledger WHERE product_id=:p AND godown_id=:g", p=pid, g=gid)
        elif pid:
            bal = db.scalar(conn, "SELECT COALESCE(SUM(qty),0) FROM stock_ledger WHERE product_id=:p", p=pid)
        elif bc:
            bal = db.scalar(conn, "SELECT COALESCE(SUM(qty),0) FROM stock_ledger WHERE barcode=:b", b=bc)
        else:
            bal = 0
    return jsonify({"balance": float(bal)})


@login_required
def batches():
    pid = request.args.get("product_id")
    if not pid:
        return jsonify([])
    gid = request.args.get("godown_id")
    with db.tx() as conn:
        q = """
            SELECT s.batch_no, s.godown_id, g.name AS godown_name, s.bin_no,
                   SUM(s.qty) AS available_qty, u.name AS uom
            FROM stock_ledger s
            JOIN m_product p ON p.id = s.product_id
            JOIN m_uom u ON u.id = p.uom_id
            LEFT JOIN m_godown g ON g.id = s.godown_id
            WHERE s.product_id = :p AND s.batch_no IS NOT NULL AND TRIM(s.batch_no) <> ''
        """
        params = {"p": pid}
        if gid:
            q += " AND s.godown_id = :g"
            params["g"] = gid
        q += " GROUP BY s.batch_no, s.godown_id, g.name, s.bin_no, u.name HAVING SUM(s.qty) > 0 ORDER BY s.batch_no"
        rows = db.all_(conn, q, **params)
        for r in rows:
            r["available_qty"] = float(r["available_qty"])
    return jsonify(rows)


@require("stock_report", "view")
def batch_report():
    a = request.args
    q = f"%{(a.get('q') or '').strip()}%"
    category_id = a.get("category_id")
    godown_id = a.get("godown_id")
    available_only = a.get("available_only") in ("1", "true", "True")

    with db.tx() as conn:
        where = [
            "p.enable_batch = 1",
            "s.batch_no IS NOT NULL",
            "TRIM(s.batch_no) <> ''",
            "(p.item_name LIKE :q OR s.batch_no LIKE :q OR g.name LIKE :q OR c.name LIKE :q)",
        ]
        params = {"q": q}
        if category_id:
            where.append("p.category_id = :cat")
            params["cat"] = category_id
        if godown_id:
            where.append("s.godown_id = :gid")
            params["gid"] = godown_id

        where_str = " AND ".join(where)
        having_str = "HAVING SUM(s.qty) > 0" if available_only else ""

        sql = f"""
            SELECT 
                p.id AS product_id,
                p.item_name,
                c.name AS category,
                u.name AS uom,
                s.batch_no,
                s.godown_id,
                g.name AS godown,
                COALESCE(SUM(CASE WHEN s.qty > 0 THEN s.qty ELSE 0 END), 0) AS inward_qty,
                COALESCE(SUM(CASE WHEN s.qty < 0 THEN ABS(s.qty) ELSE 0 END), 0) AS outward_qty,
                COALESCE(SUM(s.qty), 0) AS available_qty,
                p.min_stock
            FROM stock_ledger s
            JOIN m_product p ON p.id = s.product_id
            JOIN m_uom u ON u.id = p.uom_id
            LEFT JOIN m_product_category c ON c.id = p.category_id
            LEFT JOIN m_godown g ON g.id = s.godown_id
            WHERE {where_str}
            GROUP BY p.id, p.item_name, c.name, u.name, s.batch_no, s.godown_id, g.name, p.min_stock
            {having_str}
            ORDER BY p.item_name, s.batch_no, g.name
        """
        rows = db.all_(conn, sql, **params)
        for r in rows:
            r["inward_qty"] = float(r["inward_qty"])
            r["outward_qty"] = float(r["outward_qty"])
            r["available_qty"] = float(r["available_qty"])
            r["min_stock"] = float(r["min_stock"] or 0)
            alts = db.all_(
                conn,
                "SELECT pu.uom_id, u.name, pu.conversion_factor FROM m_product_uom pu JOIN m_uom u ON u.id=pu.uom_id WHERE pu.product_id=:pid ORDER BY pu.conversion_factor DESC",
                pid=r["product_id"],
            )
            r["alternate_uoms"] = alts
            r["composite_available"] = make_composite_str(r["available_qty"], r["uom"], alts)
    return jsonify(rows)


@require("stock_report", "view")
def batch_history():
    a = request.args
    pid = a.get("product_id")
    batch_no = a.get("batch_no")
    gid = a.get("godown_id")
    if not pid or not batch_no:
        return jsonify({"error": "product_id and batch_no are required"}), 400

    with db.tx() as conn:
        prod = db.one(
            conn,
            "SELECT p.id, p.item_name, u.name AS uom, c.name AS category FROM m_product p JOIN m_uom u ON u.id=p.uom_id LEFT JOIN m_product_category c ON c.id=p.category_id WHERE p.id=:p",
            p=pid,
        )
        if not prod:
            return jsonify({"error": "Product not found"}), 404

        godown = None
        if gid:
            godown = db.one(conn, "SELECT id, name FROM m_godown WHERE id=:g", g=gid)

        where = ["s.product_id = :p", "s.batch_no = :b"]
        params = {"p": pid, "b": batch_no}
        if gid:
            where.append("s.godown_id = :g")
            params["g"] = gid

        where_str = " AND ".join(where)
        sql = f"""
            SELECT 
                s.id,
                s.txn_date,
                s.qty,
                s.godown_id,
                g.name AS godown_name,
                s.bin_no,
                h.id AS header_id,
                h.doc_type,
                h.voucher_no,
                h.voucher_date,
                l.name AS party_name
            FROM stock_ledger s
            LEFT JOIN txn_header h ON h.id = s.header_id
            LEFT JOIN m_godown g ON g.id = s.godown_id
            LEFT JOIN m_ledger l ON l.id = h.party_id
            WHERE {where_str}
            ORDER BY s.txn_date ASC, s.id ASC
        """
        raw_rows = db.all_(conn, sql, **params)

        running_bal = 0.0
        total_inw = 0.0
        total_outw = 0.0
        history = []
        for r in raw_rows:
            qty_val = float(r["qty"])
            inward = qty_val if qty_val > 0 else 0.0
            outward = abs(qty_val) if qty_val < 0 else 0.0
            total_inw += inward
            total_outw += outward
            running_bal += qty_val

            history.append({
                "id": r["id"],
                "txn_date": r["txn_date"].isoformat() if hasattr(r["txn_date"], "isoformat") else str(r["txn_date"]),
                "doc_type": r["doc_type"] or "manual",
                "voucher_no": r["voucher_no"] or f"TXN-{r['id']}",
                "party_name": r["party_name"] or "—",
                "godown_name": r["godown_name"] or "—",
                "bin_no": r["bin_no"] or "",
                "inward_qty": inward,
                "outward_qty": outward,
                "balance": round(running_bal, 3),
            })

        summary = {
            "product_id": prod["id"],
            "item_name": prod["item_name"],
            "category": prod["category"] or "—",
            "uom": prod["uom"],
            "batch_no": batch_no,
            "godown_id": godown["id"] if godown else None,
            "godown_name": godown["name"] if godown else "All Godowns",
            "total_inward": round(total_inw, 3),
            "total_outward": round(total_outw, 3),
            "available_stock": round(running_bal, 3),
        }

    return jsonify({"summary": summary, "history": history})

