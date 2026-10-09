"""Voucher engine shared by all transaction screens and by the API-integration endpoints."""
import datetime as dt
import json
from decimal import ROUND_HALF_UP, Decimal

from flask import g, jsonify, request

from app import db
from controllers.auth_controller import can, login_required, require
from app.errors import ApiError
from app.vouchers_cfg import ITEM_COLS, RATE_IN, RATE_ON, VOUCHERS


HEADER_TEXT = ["retailer_name", "remarks", "ref_doc_no"]
HEADER_INT = ["salesman_id", "broker_id", "ref_voucher_id", "packing_list_id"]
HEADER_DATE = ["ref_doc_date"]
LOGISTICS_DATES = ["einvoice_date", "eway_bill_date", "courier_slip_date", "transporter_cn_date"]
LOGISTICS_TEXT = ["einvoice_no", "eway_bill_no", "courier_slip_no", "transporter_cn_no"]

def D(x):
    try:
        return Decimal(str(x if x not in (None, "") else 0))
    except Exception:
        raise ApiError("A number field has an invalid value")

def q2(x):
    return x.quantize(Decimal("0.01"), ROUND_HALF_UP)

def cfg(doc):
    if doc not in VOUCHERS:
        raise ApiError("Unknown document type", 404)
    return VOUCHERS[doc]


# ------------------------------------------------------------------ numbering
def _fmt_no(tt, n):
    return f"{tt['prefix'] or ''}{str(n).zfill(4)}{tt['suffix'] or ''}"

def next_number(conn, txn_type_id, kind, commit=True):
    if not txn_type_id:
        tt = db.one(conn, "SELECT * FROM m_txn_type WHERE txn_kind=:k AND active=1 LIMIT 1", k=kind)
        if not tt:
            raise ApiError(f"Please create a Transaction Type for '{kind}' first in Masters -> System")
        txn_type_id = tt["id"]
    tt = db.one(conn, "SELECT * FROM m_txn_type WHERE id=:i" + (" FOR UPDATE" if commit else ""), i=txn_type_id)
    if not tt or not tt["active"]:
        raise ApiError("Pick a valid transaction type", field="txn_type_id")
    if tt["txn_kind"] != kind:
        raise ApiError(f"Transaction type '{tt['name']}' is for {tt['txn_kind']}, not {kind}", field="txn_type_id")
    n = max(tt["max_number"], tt["start_number"] - 1) + 1
    if commit:
        db.run(conn, "UPDATE m_txn_type SET max_number=:n WHERE id=:i", n=n, i=txn_type_id)
    return _fmt_no(tt, n), tt


# ------------------------------------------------------------------ totals
def compute(conn, items, ledgers):
    """Recalculate line nets, ledger amounts and totals. Mutates rows, returns totals."""
    tq = pv = Decimal(0)
    for it in items:
        qty, rate = D(it.get("qty")), D(it.get("rate"))
        gross = qty * rate
        pct = D(it.get("disc_pct"))
        da = q2(gross * pct / 100) if pct > 0 else q2(D(it.get("disc_amt")))
        it["disc_amt"] = da
        it["net_amount"] = q2(gross - da)
        tq += qty
        pv += it["net_amount"]
    types = {}
    for l in ledgers:
        if l["ledger_id"] not in types:
            types[l["ledger_id"]] = db.scalar(conn, "SELECT ledger_type FROM m_ledger WHERE id=:i", i=l["ledger_id"])

    def amount(l, base_val):
        rate, on, inn = D(l["rate"]), l["rate_on"], l["rate_in"]
        if on == "auto":
            a = rate
        elif inn == "value":
            a = rate * tq if on == "total_qty" else rate
        else:
            a = base_val * rate / 100
        a = q2(a)
        return -abs(a) if types.get(l["ledger_id"]) == "Discount" else a

    running = pv
    for l in ledgers:                                     # pass 1: everything except "net value" lines
        if l["rate_on"] == "net_value":
            continue
        base = {"total_qty": tq, "total_product_value": pv, "current_subtotal": running}.get(l["rate_on"], Decimal(0))
        l["amount"] = amount(l, base)
        running += l["amount"]
    netv = running
    for l in ledgers:                                     # pass 2: "net value" lines
        if l["rate_on"] == "net_value":
            l["amount"] = amount(l, netv)
            running += l["amount"]
    return {"total_qty": tq, "total_product_value": q2(pv), "total_value": q2(running)}


# ------------------------------------------------------------------ reads
HDR_SQL = """
SELECT h.*, tt.name AS `txn_type_id__label`, b.name AS `branch_id__label`, pa.name AS `party_id__label`,
       pa.state AS party_state, la.name AS party_account, sm.name AS `salesman_id__label`, bk.name AS `broker_id__label`,
       rv.voucher_no AS `ref_voucher_id__label`, rv.voucher_date AS ref_voucher_date, pl.voucher_no AS `packing_list_id__label`,
       tr.name AS `transporter_id__label`
FROM txn_header h
JOIN m_txn_type tt ON tt.id=h.txn_type_id JOIN m_branch b ON b.id=h.branch_id JOIN m_ledger pa ON pa.id=h.party_id
LEFT JOIN m_ledger_account la ON la.id=pa.ledger_account_id
LEFT JOIN m_ledger sm ON sm.id=h.salesman_id LEFT JOIN m_ledger bk ON bk.id=h.broker_id
LEFT JOIN txn_header rv ON rv.id=h.ref_voucher_id LEFT JOIN txn_header pl ON pl.id=h.packing_list_id
LEFT JOIN m_ledger tr ON tr.id=h.transporter_id
"""

ITEM_SQL = """
SELECT i.*, p.item_name AS `product_id__label`, p.gst_rate, p.barcode AS product_barcode, u.name AS uom,
       gd.name AS `godown_id__label`, pl.name AS `pricelist_id__label`, sm.name AS `salesman_id__label`,
       bk.name AS `broker_id__label`, sh.voucher_no AS src_no
FROM txn_item i JOIN m_product p ON p.id=i.product_id JOIN m_uom u ON u.id=p.uom_id
LEFT JOIN m_godown gd ON gd.id=i.godown_id LEFT JOIN m_pricelist pl ON pl.id=i.pricelist_id
LEFT JOIN m_ledger sm ON sm.id=i.salesman_id LEFT JOIN m_ledger bk ON bk.id=i.broker_id
LEFT JOIN txn_header sh ON sh.id=i.src_header_id
WHERE i.header_id=:h ORDER BY i.line_no
"""

def used_downstream(conn, hid, strict=False):
    flt = "" if strict else " AND {a}.approval_status<>'Rejected'"
    n = db.scalar(conn, "SELECT COUNT(*) FROM txn_item x JOIN txn_header xh ON xh.id=x.header_id WHERE x.src_header_id=:h"
                  + flt.format(a="xh"), h=hid)
    n += db.scalar(conn, "SELECT COUNT(*) FROM txn_header xh WHERE (xh.packing_list_id=:h OR xh.ref_voucher_id=:h)"
                   + flt.format(a="xh"), h=hid)
    n += db.scalar(conn, "SELECT COUNT(*) FROM prod_plan_order po JOIN prod_plan pp ON pp.id=po.plan_id "
                         "WHERE po.order_id=:h AND pp.status='Accepted'", h=hid)
    return n > 0

def get_voucher(conn, hid, doc=None):
    h = db.one(conn, HDR_SQL + " WHERE h.id=:i", i=hid)
    if not h or (doc and h["doc_type"] != doc):
        raise ApiError("Voucher not found", 404)
    h["items"] = db.all_(conn, ITEM_SQL, h=hid)
    h["ledgers"] = db.all_(conn, "SELECT l.*, m.name AS `ledger_id__label`, m.ledger_type FROM txn_ledger l "
                                 "JOIN m_ledger m ON m.id=l.ledger_id WHERE l.header_id=:h ORDER BY l.line_no", h=hid)
    terms = db.all_(conn, "SELECT kind,sl_no,description FROM txn_terms WHERE header_id=:h ORDER BY kind,sl_no", h=hid)
    h["terms"] = [t for t in terms if t["kind"] == "terms"]
    h["payments"] = [t for t in terms if t["kind"] == "payment"]
    h["locked"] = h["approval_status"] == "Approved" or used_downstream(conn, hid)
    return h

def check_voucher_permission(conn, hid, action):
    if "*" in g.user["permissions"]:
        return
    tid = db.scalar(conn, "SELECT txn_type_id FROM txn_header WHERE id=:i", i=hid)
    if not tid:
        raise ApiError("Voucher not found", 404)
    acts = g.user["permissions"].get(f"txn_type_{tid}", [])
    if "*" not in acts and action not in acts:
        raise ApiError(f"You don't have permission to {action} this transaction", 403)


# ------------------------------------------------------------------ pending source lines
PENDING_SQL = """
SELECT * FROM (
 SELECT h.id AS src_header_id, h.voucher_no AS src_no, h.voucher_date AS src_date,
        h.salesman_id AS h_salesman_id, h.broker_id AS h_broker_id, h.retailer_name AS h_retailer_name,
        sm.name AS h_salesman_label, bk.name AS h_broker_label,
        i.id AS src_item_id, i.barcode, i.godown_id, gd.name AS godown_label, i.bin_no, i.product_id,
        p.item_name, p.gst_rate, u.name AS uom, i.description, i.customer_item_name, i.pricelist_id,
        pl.name AS pricelist_label, i.rate, i.disc_pct, i.disc_amt, i.delivery_date, i.ref_no, i.indent_no,
        i.salesman_id, i.broker_id, i.retailer_name, i.req_item_id,
        i.qty - COALESCE((SELECT SUM(x.qty) FROM txn_item x JOIN txn_header xh ON xh.id=x.header_id
                          WHERE x.src_item_id=i.id AND xh.approval_status<>'Rejected' AND xh.id<>:excl),0) AS pending_qty
 FROM txn_item i JOIN txn_header h ON h.id=i.header_id JOIN m_product p ON p.id=i.product_id
 JOIN m_uom u ON u.id=p.uom_id LEFT JOIN m_godown gd ON gd.id=i.godown_id LEFT JOIN m_pricelist pl ON pl.id=i.pricelist_id
 LEFT JOIN m_ledger sm ON sm.id=h.salesman_id LEFT JOIN m_ledger bk ON bk.id=h.broker_id
 WHERE h.doc_type=:kind AND h.approval_status='Approved' {party}
) t WHERE pending_qty>0 ORDER BY src_date, src_header_id, src_item_id
"""

REQ_PENDING_SQL = """
SELECT * FROM (
 SELECT r.id AS src_header_id, r.req_no AS src_no, r.req_date AS src_date, ri.id AS req_item_id, ri.product_id,
        p.item_name, p.gst_rate, u.name AS uom, r.req_no AS indent_no,
        ri.qty - COALESCE((SELECT SUM(x.qty) FROM txn_item x JOIN txn_header xh ON xh.id=x.header_id
                           WHERE x.req_item_id=ri.id AND xh.approval_status<>'Rejected' AND xh.id<>:excl),0) AS pending_qty
 FROM requisition_item ri JOIN requisition r ON r.id=ri.requisition_id JOIN m_product p ON p.id=ri.product_id
 JOIN m_uom u ON u.id=p.uom_id WHERE r.status='Open'
) t WHERE pending_qty>0 ORDER BY src_date, src_header_id
"""

def pending_lines(conn, doc, party_id=None, exclude=0):
    src = cfg(doc).get("source")
    if not src:
        return []
    if src["kind"] == "requisition":
        return db.all_(conn, REQ_PENDING_SQL, excl=exclude)
    party = " AND h.party_id=:party" if party_id else ""
    p = {"kind": src["kind"], "excl": exclude}
    if party_id:
        p["party"] = party_id
    return db.all_(conn, PENDING_SQL.format(party=party), **p)

def _pending_one(conn, col, item_id, exclude):
    if col == "req_item_id":
        base = db.scalar(conn, "SELECT qty FROM requisition_item WHERE id=:i", i=item_id)
    else:
        base = db.scalar(conn, "SELECT qty FROM txn_item WHERE id=:i", i=item_id)
    if base is None:
        raise ApiError("A source line no longer exists")
    used = db.scalar(conn, f"SELECT COALESCE(SUM(x.qty),0) FROM txn_item x JOIN txn_header xh ON xh.id=x.header_id "
                           f"WHERE x.`{col}`=:i AND xh.approval_status<>'Rejected' AND xh.id<>:e", i=item_id, e=exclude)
    return D(base) - D(used)


# ------------------------------------------------------------------ stock helpers
def lot_balance(conn, barcode, godown_id, bin_no, exclude_header=0):
    return D(db.scalar(conn, "SELECT COALESCE(SUM(qty),0) FROM stock_ledger WHERE barcode=:b AND godown_id=:g "
                             "AND bin_no=:n AND header_id<>:h", b=barcode, g=godown_id, n=bin_no or "", h=exclude_header))

def post_stock(conn, h, c):
    sign = c.get("stock", 0)
    if not sign:
        return
    need = {}
    for it in db.all_(conn, "SELECT * FROM txn_item WHERE header_id=:h", h=h["id"]):
        key = (it["barcode"], it["godown_id"], it["bin_no"] or "")
        if not it["barcode"] or not it["godown_id"]:
            raise ApiError(f"Line {it['line_no']}: barcode and godown are needed to move stock")
        need[key] = need.get(key, 0) + D(it["qty"])
        db.insert(conn, "stock_ledger", {"header_id": h["id"], "item_id": it["id"], "txn_date": h["voucher_date"],
                                         "product_id": it["product_id"], "barcode": it["barcode"], "godown_id": it["godown_id"],
                                         "bin_no": it["bin_no"] or "", "qty": D(it["qty"]) * sign})
    if sign < 0:
        for (bc, gd, bn), q in need.items():
            if lot_balance(conn, bc, gd, bn) < 0:
                raise ApiError(f"Not enough stock in barcode {bc} (godown/bin selected). Reduce the quantity.")

def unpost_stock(conn, h):
    rows = db.all_(conn, "SELECT barcode,godown_id,bin_no,SUM(qty) q FROM stock_ledger WHERE header_id=:h GROUP BY barcode,godown_id,bin_no", h=h["id"])
    db.run(conn, "DELETE FROM stock_ledger WHERE header_id=:h", h=h["id"])
    for r in rows:
        if D(r["q"]) > 0 and lot_balance(conn, r["barcode"], r["godown_id"], r["bin_no"]) < 0:
            raise ApiError(f"Stock from barcode {r['barcode']} has already been used, so this can't be un-approved")


# ------------------------------------------------------------------ build / validate / save
def _clean_items(conn, doc, c, raw):
    if not raw:
        raise ApiError("Add at least one item line")
    items = []
    for n, r in enumerate(raw, 1):
        pid = r.get("product_id")
        if not pid:
            raise ApiError(f"Line {n}: pick an item")
        qty = D(r.get("qty"))
        if qty <= 0:
            raise ApiError(f"Line {n}: quantity must be more than zero")
        it = {"line_no": n, "product_id": int(pid), "qty": qty}
        for k in ("barcode", "bin_no", "description", "customer_item_name", "retailer_name", "ref_no", "indent_no"):
            v = (r.get(k) or "").strip() if isinstance(r.get(k), str) else r.get(k)
            it[k] = v or None
        for k in ("godown_id", "pricelist_id", "salesman_id", "broker_id", "src_header_id", "src_item_id", "req_item_id"):
            it[k] = int(r[k]) if r.get(k) else None
        it["delivery_date"] = str(r["delivery_date"])[:10] if r.get("delivery_date") else None
        for k in ("rate", "disc_pct", "disc_amt"):
            it[k] = D(r.get(k))
        items.append(it)
    return items

def _clean_ledgers(raw):
    out = []
    for n, r in enumerate(raw or [], 1):
        if not r.get("ledger_id"):
            continue
        if (r.get("rate_in") or "percent") not in RATE_IN or (r.get("rate_on") or "auto") not in dict(RATE_ON):
            raise ApiError(f"Ledger line {n}: invalid rate basis")
        out.append({"line_no": n, "ledger_id": int(r["ledger_id"]), "rate": D(r.get("rate")),
                    "rate_in": r.get("rate_in") or "percent", "rate_on": r.get("rate_on") or "auto"})
    return out

def _validate_links(conn, doc, c, h, items, hid):
    src, party = c.get("source"), h["party_id"]
    excl = hid or 0
    if src and src["required"] and any(not (i["src_item_id"] or i["req_item_id"]) for i in items):
        raise ApiError(f"Pick lines from {src['label'].lower()} — every line must link to one")
    if src:
        col = "req_item_id" if src["kind"] == "requisition" else "src_item_id"
        totals = {}
        for i in items:
            if i[col]:
                totals[i[col]] = totals.get(i[col], 0) + i["qty"]
        for sid, q in totals.items():
            if q > _pending_one(conn, col, sid, excl):
                raise ApiError("A line exceeds the pending quantity on its source. Reduce the quantity.")
        if src["kind"] != "requisition":
            for sh in {i["src_header_id"] for i in items if i["src_header_id"]}:
                s = db.one(conn, "SELECT doc_type,party_id,approval_status FROM txn_header WHERE id=:i", i=sh)
                if not s or s["doc_type"] != src["kind"] or s["approval_status"] != "Approved" or s["party_id"] != party:
                    raise ApiError("Source documents must be approved and belong to the same party")
        if src.get("link"):
            lid = h.get(src["link"])
            if not lid:
                raise ApiError(f"Select the {src['label'].lower()}", field=src["link"])
            if db.scalar(conn, "SELECT COUNT(*) FROM txn_header WHERE packing_list_id=:l AND id<>:i AND approval_status<>'Rejected'", l=lid, i=excl):
                raise ApiError("That packing list already has an invoice")
            if any(i["src_header_id"] != lid for i in items):
                raise ApiError("All lines must come from the selected packing list")

def _validate_stock_lines(conn, doc, c, h, items, hid):
    sign = c.get("stock", 0)
    if c.get("auto_barcode"):
        for i in items:
            if not i["godown_id"]:
                raise ApiError(f"Line {i['line_no']}: pick a godown to receive into")
        return
    if not (sign and c.get("scan")):
        return
    ref_items = {}
    if c.get("return_of"):
        ref = db.one(conn, "SELECT id,doc_type,party_id,approval_status FROM txn_header WHERE id=:i", i=h.get("ref_voucher_id"))
        if not ref or ref["doc_type"] != c["return_of"] or ref["party_id"] != h["party_id"] or ref["approval_status"] != "Approved":
            raise ApiError("Pick an approved invoice of the same party", field="ref_voucher_id")
        for r in db.all_(conn, "SELECT barcode,SUM(qty) q FROM txn_item WHERE header_id=:h GROUP BY barcode", h=ref["id"]):
            ref_items[r["barcode"]] = D(r["q"])
        done = {r["barcode"]: D(r["q"]) for r in db.all_(
            conn, "SELECT x.barcode,SUM(x.qty) q FROM txn_item x JOIN txn_header xh ON xh.id=x.header_id WHERE xh.doc_type=:d "
                  "AND xh.ref_voucher_id=:r AND xh.approval_status<>'Rejected' AND xh.id<>:e GROUP BY x.barcode",
            d=doc, r=ref["id"], e=hid or 0)}
        mine = {}
        for i in items:
            mine[i["barcode"]] = mine.get(i["barcode"], 0) + i["qty"]
        for bc, q in mine.items():
            if bc not in ref_items:
                raise ApiError(f"Barcode {bc} is not on the selected invoice")
            if q + done.get(bc, 0) > ref_items[bc]:
                raise ApiError(f"Barcode {bc}: returning more than was invoiced")
    for i in items:
        if not i["barcode"]:
            raise ApiError(f"Line {i['line_no']}: scan or type a barcode")
        known = db.one(conn, "SELECT product_id FROM txn_item WHERE barcode=:b LIMIT 1", b=i["barcode"])
        if not known or known["product_id"] != i["product_id"]:
            raise ApiError(f"Line {i['line_no']}: barcode {i['barcode']} doesn't match this item")
        if not i["godown_id"]:
            raise ApiError(f"Line {i['line_no']}: godown is missing")
        if sign < 0 and not db.scalar(conn, "SELECT COUNT(*) FROM stock_ledger WHERE barcode=:b AND godown_id=:g AND bin_no=:n",
                                      b=i["barcode"], g=i["godown_id"], n=i["bin_no"] or ""):
            raise ApiError(f"Line {i['line_no']}: barcode {i['barcode']} isn't stocked in that godown/bin")

def save_voucher(conn, doc, payload, user_id, hid=None, channel="ui"):
    c = cfg(doc)
    party = db.one(conn, "SELECT id,ledger_type,active FROM m_ledger WHERE id=:i", i=payload.get("party_id"))
    if not party or not party["active"]:
        raise ApiError("Pick the party", field="party_id")
    if party["ledger_type"] not in c["party_types"]:
        raise ApiError(f"Party must be a {' / '.join(c['party_types']).lower()} ledger", field="party_id")
    vdate = str(payload.get("voucher_date") or "")[:10]
    try:
        dt.date.fromisoformat(vdate)
    except ValueError:
        raise ApiError("Enter a valid voucher date", field="voucher_date")

    old = None
    if hid:
        old = db.one(conn, "SELECT * FROM txn_header WHERE id=:i AND doc_type=:d FOR UPDATE", i=hid, d=doc)
        if not old:
            raise ApiError("Voucher not found", 404)
        if old["approval_status"] == "Approved" or used_downstream(conn, hid):
            raise ApiError("This voucher is approved or already used downstream. Un-approve it first (if allowed).", 409)
        txn_type_id = old["txn_type_id"]
    else:
        txn_type_id = payload.get("txn_type_id")

    h = {"party_id": party["id"], "voucher_date": vdate}
    for k in HEADER_TEXT:
        v = payload.get(k)
        h[k] = v.strip() if isinstance(v, str) and v.strip() else None
    for k in HEADER_INT:
        h[k] = int(payload[k]) if payload.get(k) else None
    for k in HEADER_DATE:
        h[k] = str(payload[k])[:10] if payload.get(k) else None
    if c.get("return_of"):
        if not h["ref_voucher_id"]:
            raise ApiError("Select the invoice being returned", field="ref_voucher_id")

    items = _clean_items(conn, doc, c, payload.get("items"))
    ledgers = _clean_ledgers(payload.get("ledgers"))
    _validate_links(conn, doc, c, h, items, hid)
    _validate_stock_lines(conn, doc, c, h, items, hid)
    totals = compute(conn, items, ledgers)
    h.update(totals)

    if old:
        db.update(conn, "txn_header", {**h, "approval_status": "Pending", "approver_id": None,
                                       "approver_name": None, "approved_at": None}, "id", hid)
        for t in ("txn_item", "txn_ledger", "txn_terms"):
            db.run(conn, f"DELETE FROM {t} WHERE header_id=:h", h=hid)
    else:
        vno, tt = next_number(conn, txn_type_id, c["kind"])
        branch = tt["branch_id"] or payload.get("branch_id") or db.scalar(conn, "SELECT id FROM m_branch WHERE active=1 ORDER BY id LIMIT 1")
        if not branch:
            raise ApiError("Create a branch first")
        hid = db.insert(conn, "txn_header", {**h, "doc_type": doc, "txn_type_id": txn_type_id, "branch_id": branch,
                                             "voucher_no": vno, "created_by": user_id, "source_channel": channel})
    for it in items:
        it["header_id"] = hid
        db.insert(conn, "txn_item", it)
        if c.get("auto_barcode"):
            iid = db.scalar(conn, "SELECT id FROM txn_item WHERE header_id=:h AND line_no=:n", h=hid, n=it["line_no"])
            db.run(conn, "UPDATE txn_item SET barcode=:b WHERE id=:i", b=f"B{it['product_id']:05d}{hid:07d}{it['line_no']:02d}", i=iid)
    for l in ledgers:
        db.insert(conn, "txn_ledger", {**l, "header_id": hid})
    for kind, key in (("terms", "terms"), ("payment", "payments")):
        n = 0
        for t in payload.get(key) or []:
            d = (t.get("description") or "").strip()
            if d:
                n += 1
                db.insert(conn, "txn_terms", {"header_id": hid, "kind": kind, "sl_no": n, "description": d})
    return hid


# ------------------------------------------------------------------ approvals
def set_status(conn, doc, hid, action, user):
    c = cfg(doc)
    h = db.one(conn, "SELECT * FROM txn_header WHERE id=:i AND doc_type=:d FOR UPDATE", i=hid, d=doc)
    if not h:
        raise ApiError("Voucher not found", 404)
    st = h["approval_status"]
    if action == "approve":
        if st == "Approved":
            raise ApiError("Already approved")
        db.run(conn, "UPDATE txn_header SET approval_status='Approved', approver_id=:u, approver_name=:n, approved_at=NOW() WHERE id=:i",
               u=user["id"], n=user["full_name"], i=hid)
        post_stock(conn, h, c)
    elif action == "reject":
        if st != "Pending":
            raise ApiError("Only pending vouchers can be rejected")
        db.run(conn, "UPDATE txn_header SET approval_status='Rejected', approver_id=:u, approver_name=:n, approved_at=NOW() WHERE id=:i",
               u=user["id"], n=user["full_name"], i=hid)
    elif action == "unapprove":
        if st != "Approved":
            raise ApiError("Only approved vouchers can be un-approved")
        if used_downstream(conn, hid):
            raise ApiError("Later documents were created from this one, so it can't be un-approved", 409)
        unpost_stock(conn, h)
        db.run(conn, "UPDATE txn_header SET approval_status='Pending', approver_id=NULL, approver_name=NULL, approved_at=NULL WHERE id=:i", i=hid)


# ------------------------------------------------------------------ GST helper
def gst_suggest(conn, party_id, branch_id, items):
    party = db.one(conn, "SELECT state FROM m_ledger WHERE id=:i", i=party_id) or {}
    branch = db.one(conn, "SELECT state FROM m_branch WHERE id=:i", i=branch_id) if branch_id else \
        db.one(conn, "SELECT state FROM m_branch WHERE active=1 ORDER BY id LIMIT 1")
    ps, bs = (party.get("state") or "").strip().lower(), ((branch or {}).get("state") or "").strip().lower()
    inter = bool(ps and bs and ps != bs)
    tot = Decimal(0)
    for it in items:
        rate = D(db.scalar(conn, "SELECT gst_rate FROM m_product WHERE id=:i", i=it.get("product_id")))
        gross = D(it.get("qty")) * D(it.get("rate"))
        pct = D(it.get("disc_pct"))
        disc = gross * pct / 100 if pct > 0 else D(it.get("disc_amt"))
        tot += (gross - disc) * rate / 100
    wanted = ["IGST"] if inter else ["CGST", "SGST"]
    out = []
    for t in wanted:
        led = db.one(conn, "SELECT id,name FROM m_ledger WHERE tax_type=:t AND active=1 ORDER BY id LIMIT 1", t=t)
        if not led:
            raise ApiError(f"Create a ledger with tax type {t} first (Masters > Ledgers)")
        amt = q2(tot if inter else tot / 2)
        out.append({"ledger_id": led["id"], "ledger_id__label": led["name"], "rate": float(amt),
                    "rate_in": "value", "rate_on": "auto", "ledger_type": "General"})
    return {"lines": out, "inter_state": inter}


# ================================================================== routes
@require(lambda kw: kw["doc"], "view")
def list_(doc):
    cfg(doc)
    a = request.args
    where, p = ["h.doc_type=:d"], {"d": doc}
    for k, col in [("s_voucher_no", "h.voucher_no"), ("s_voucher_date", "h.voucher_date"), 
                   ("s_party_name", "pa.name"), ("s_txn_type", "tt.name"), ("s_status", "h.approval_status")]:
        if a.get(k):
            vals = [x.strip() for x in a[k].split(",") if x.strip()]
            if not vals: continue
            or_conds = []
            for i, val in enumerate(vals):
                pk = f"{k}_{i}"
                or_conds.append(f"{col} LIKE :{pk}")
                p[pk] = f"%{val}%"
            where.append("(" + " OR ".join(or_conds) + ")")
    if a.get("q"):
        where.append("(h.voucher_no LIKE :q OR pa.name LIKE :q)"); p["q"] = f"%{a['q'].strip()}%"
    
    # Enforce transaction-type level permissions
    if "*" not in g.user["permissions"]:
        kind = cfg(doc)["kind"]
        with db.tx() as c:
            types = db.all_(c, "SELECT id FROM m_txn_type WHERE txn_kind=:k", k=kind)
        allowed = []
        for t in types:
            acts = g.user["permissions"].get(f"txn_type_{t['id']}", [])
            if "*" in acts or "view" in acts:
                allowed.append(t["id"])
        
        # If they don't have access to any txn_type for this doc, return empty
        if not allowed:
            return jsonify({"rows": [], "total": 0, "page": 1, "page_size": 30})
            
        where.append(f"h.txn_type_id IN ({','.join(map(str, allowed))})")

    page, size = max(int(a.get("page", 1)), 1), min(int(a.get("page_size", 30)), 200)
    w = " AND ".join(where)
    with db.tx() as conn:
        total = db.scalar(conn, f"SELECT COUNT(*) FROM txn_header h JOIN m_ledger pa ON pa.id=h.party_id JOIN m_txn_type tt ON tt.id=h.txn_type_id WHERE {w}", **p)
        rows = db.all_(conn, f"SELECT h.id,h.voucher_no,h.voucher_date,h.total_qty,h.total_value,h.approval_status,h.source_channel,"
                             f"pa.name party_name,tt.name txn_type FROM txn_header h JOIN m_ledger pa ON pa.id=h.party_id "
                             f"JOIN m_txn_type tt ON tt.id=h.txn_type_id WHERE {w} ORDER BY h.voucher_date DESC,h.id DESC "
                             f"LIMIT {size} OFFSET {(page - 1) * size}", **p)
    return jsonify({"rows": rows, "total": total, "page": page, "page_size": size})


@require(lambda kw: kw["doc"], "view")
def get_(doc, hid):
    with db.tx() as conn:
        check_voucher_permission(conn, hid, "view")
        return jsonify(get_voucher(conn, hid, doc))


@require(lambda kw: kw["doc"], "view")
def preview_number(doc):
    tid = request.args.get("txn_type_id")
    if tid and "*" not in g.user["permissions"]:
        acts = g.user["permissions"].get(f"txn_type_{tid}", [])
        if "*" not in acts and "view" not in acts and "create" not in acts:
            raise ApiError("You don't have permission for this transaction type", 403)
    with db.tx() as conn:
        no, tt = next_number(conn, tid, cfg(doc)["kind"], commit=False)
    return jsonify({"voucher_no": no, "branch_id": tt["branch_id"]})


@require(lambda kw: kw["doc"], "create")
def create(doc):
    payload = request.get_json(force=True) or {}
    tid = payload.get("txn_type_id")
    if tid and "*" not in g.user["permissions"]:
        acts = g.user["permissions"].get(f"txn_type_{tid}", [])
        if "*" not in acts and "create" not in acts:
            raise ApiError("You don't have permission to create this type of transaction", 403)
            
    with db.tx() as conn:
        hid = save_voucher(conn, doc, payload, g.user["id"])
        return jsonify(get_voucher(conn, hid)), 201


@require(lambda kw: kw["doc"], "edit")
def update_(doc, hid):
    with db.tx() as conn:
        check_voucher_permission(conn, hid, "edit")
        save_voucher(conn, doc, request.get_json(force=True) or {}, g.user["id"], hid)
        return jsonify(get_voucher(conn, hid))


@require(lambda kw: kw["doc"], "delete")
def delete_(doc, hid):
    with db.tx() as conn:
        check_voucher_permission(conn, hid, "delete")
        h = db.one(conn, "SELECT approval_status FROM txn_header WHERE id=:i AND doc_type=:d", i=hid, d=doc)
        if not h:
            raise ApiError("Voucher not found", 404)
        if h["approval_status"] == "Approved" or used_downstream(conn, hid, strict=True):
            raise ApiError("Approved or referenced vouchers can't be deleted", 409)
        db.run(conn, "DELETE FROM txn_header WHERE id=:i", i=hid)
    return jsonify({"ok": True})


@require(lambda kw: kw["doc"], "approve")
def action_(doc, hid, action):
    if action not in ("approve", "reject", "unapprove"):
        raise ApiError("Unknown action", 404)
    with db.tx() as conn:
        check_voucher_permission(conn, hid, "approve")
        set_status(conn, doc, hid, action, g.user)
        return jsonify(get_voucher(conn, hid))


@require(lambda kw: kw["doc"], "create")
def pending_(doc):
    with db.tx() as conn:
        return jsonify(pending_lines(conn, doc, request.args.get("party_id"), int(request.args.get("exclude", 0))))


@require(lambda kw: kw["doc"], "create")
def gst_(doc):
    d = request.get_json(force=True) or {}
    with db.tx() as conn:
        return jsonify(gst_suggest(conn, d.get("party_id"), d.get("branch_id"), d.get("items") or []))


@require(lambda kw: kw["doc"], "create")
def ref_vouchers(doc):
    """Approved invoices of a party, for returns."""
    base = cfg(doc).get("return_of")
    if not base:
        raise ApiError("Not a return document")
    with db.tx() as conn:
        return jsonify(db.all_(conn, "SELECT id,voucher_no,voucher_date,total_value FROM txn_header WHERE doc_type=:d AND party_id=:p "
                                     "AND approval_status='Approved' ORDER BY voucher_date DESC,id DESC LIMIT 100",
                               d=base, p=request.args.get("party_id")))


# ------------------------------------------------------------------ logistics updation (sales invoice)
@require("logistics", "view")
def logistics_list():
    q = f"%{(request.args.get('q') or '').strip()}%"
    with db.tx() as conn:
        return jsonify(db.all_(conn, "SELECT h.id,h.voucher_no,h.voucher_date,h.total_value,pa.name party_name,h.einvoice_no,h.eway_bill_no,"
                                     "h.transporter_cn_no,h.courier_slip_no FROM txn_header h JOIN m_ledger pa ON pa.id=h.party_id "
                                     "WHERE h.doc_type='sales' AND h.approval_status='Approved' AND (h.voucher_no LIKE :q OR pa.name LIKE :q) "
                                     "ORDER BY h.voucher_date DESC,h.id DESC LIMIT 100", q=q))


@require("logistics", "view")
def logistics_get(hid):
    with db.tx() as conn:
        h = db.one(conn, HDR_SQL + " WHERE h.id=:i AND h.doc_type='sales'", i=hid)
    if not h:
        raise ApiError("Sales invoice not found", 404)
    return jsonify(h)


@require("logistics", "edit")
def logistics_save(hid):
    d = request.get_json(force=True) or {}
    data = {}
    for k in LOGISTICS_TEXT:
        data[k] = (d.get(k) or "").strip() or None
    for k in LOGISTICS_DATES:
        data[k] = str(d[k])[:10] if d.get(k) else None
    data["transporter_id"] = int(d["transporter_id"]) if d.get("transporter_id") else None
    data["freight_amount"] = D(d.get("freight_amount")) if d.get("freight_amount") not in (None, "") else None
    with db.tx() as conn:
        if not db.scalar(conn, "SELECT COUNT(*) FROM txn_header WHERE id=:i AND doc_type='sales'", i=hid):
            raise ApiError("Sales invoice not found", 404)
        db.update(conn, "txn_header", data, "id", hid)
    return jsonify({"ok": True})
