"""API-key protected endpoints so external systems (e-commerce, EDI, mobile apps) can push documents.

POST /api/integrations/sales-orders    header X-API-Key: gk_...
POST /api/integrations/packing-lists
Names/codes in the payload are resolved to ids; documents land as *Pending* approval.
"""
import hashlib
from functools import wraps

from flask import g, jsonify, request

from app import db
from app.errors import ApiError
from controllers.pricing_controller import resolve
from controllers.vouchers_controller import PENDING_SQL, D, get_voucher, save_voucher


def api_key_required(fn):
    @wraps(fn)
    def w(*a, **kw):
        key = request.headers.get("X-API-Key", "")
        with db.tx() as c:
            cl = db.one(c, "SELECT * FROM api_client WHERE key_hash=:h AND active=1", h=hashlib.sha256(key.encode()).hexdigest())
        if not cl:
            raise ApiError("Invalid or disabled API key", 401)
        g.api_client = cl
        return fn(*a, **kw)
    return w

def _ledger(conn, ref, types, what):
    if isinstance(ref, dict):
        row = db.one(conn, "SELECT id,ledger_type FROM m_ledger WHERE (gstin=:g AND :g IS NOT NULL AND :g<>'') OR name=:n LIMIT 1",
                     g=ref.get("gstin"), n=ref.get("name"))
    else:
        row = db.one(conn, "SELECT id,ledger_type FROM m_ledger WHERE name=:n", n=ref)
    if not row:
        raise ApiError(f"{what} not found: {ref}")
    if types and row["ledger_type"] not in types:
        raise ApiError(f"{what} '{ref}' is a {row['ledger_type']} ledger")
    return row["id"]

def _txn_type(conn, kind, name):
    row = db.one(conn, "SELECT id FROM m_txn_type WHERE txn_kind=:k AND active=1 AND (:n IS NULL OR name=:n) ORDER BY id LIMIT 1", k=kind, n=name)
    if not row:
        raise ApiError(f"No active transaction type for {kind}")
    return row["id"]

def _product(conn, it):
    row = None
    if it.get("item_name"):
        row = db.one(conn, "SELECT id FROM m_product WHERE item_name=:n", n=it["item_name"])
    elif it.get("barcode"):
        row = db.one(conn, "SELECT id FROM m_product WHERE barcode=:b", b=it["barcode"]) or \
              db.one(conn, "SELECT product_id id FROM stock_ledger WHERE barcode=:b LIMIT 1", b=it["barcode"])
    if not row:
        raise ApiError(f"Item not found: {it.get('item_name') or it.get('barcode')}")
    return row["id"]

def _common(conn, d, party_types):
    date = (d.get("date") or "")[:10] or db.scalar(conn, "SELECT CURDATE()")
    return {"party_id": _ledger(conn, d.get("party"), party_types, "Party"), "voucher_date": date,
            "remarks": d.get("remarks"), "terms": [{"description": t} for t in d.get("terms", [])],
            "payments": [{"description": t} for t in d.get("payment_terms", [])],
            "ledgers": [{"ledger_id": _ledger(conn, l["ledger"], None, "Ledger"), "rate": l.get("rate", 0),
                         "rate_in": l.get("rate_in", "value"), "rate_on": l.get("rate_on", "auto")} for l in d.get("ledgers", [])]}


@api_key_required
def sales_order():
    d = request.get_json(force=True) or {}
    with db.tx() as conn:
        p = _common(conn, d, ["Customer"])
        p["txn_type_id"] = _txn_type(conn, "Sales order", d.get("txn_type"))
        if d.get("salesman"):
            p["salesman_id"] = _ledger(conn, d["salesman"], ["Salesman"], "Salesman")
        if d.get("broker"):
            p["broker_id"] = _ledger(conn, d["broker"], ["Broker"], "Broker")
        p["retailer_name"] = d.get("retailer")
        items = []
        for it in d.get("items") or []:
            pid = _product(conn, it)
            price = resolve(conn, p["party_id"], pid, p["voucher_date"])
            items.append({"product_id": pid, "qty": it.get("qty"), "description": it.get("description"),
                          "customer_item_name": it.get("customer_item_name") or price["customer_item_name"],
                          "pricelist_id": price["pricelist_id"], "delivery_date": it.get("delivery_date"),
                          "rate": it["rate"] if it.get("rate") is not None else price["rate"],
                          "disc_pct": it["disc_pct"] if it.get("disc_pct") is not None else price["disc_pct"],
                          "disc_amt": it.get("disc_amt", price["disc_amt"])})
        p["items"] = items
        hid = save_voucher(conn, "sales_order", p, None, channel="api")
        h = get_voucher(conn, hid)
    return jsonify({"id": hid, "voucher_no": h["voucher_no"], "total_value": h["total_value"], "status": h["approval_status"]}), 201


@api_key_required
def packing_list():
    d = request.get_json(force=True) or {}
    with db.tx() as conn:
        p = _common(conn, d, ["Customer"])
        p["txn_type_id"] = _txn_type(conn, "Challan", d.get("txn_type"))
        only = set(d.get("sales_orders") or [])
        pend = [r for r in db.all_(conn, PENDING_SQL.format(party=" AND h.party_id=:party"), kind="sales_order", excl=0, party=p["party_id"])
                if not only or r["src_no"] in only]
        items = []
        for it in d.get("items") or []:
            bc = it.get("barcode")
            lot = db.one(conn, "SELECT product_id,godown_id,bin_no,SUM(qty) bal FROM stock_ledger WHERE barcode=:b GROUP BY product_id,godown_id,bin_no HAVING SUM(qty)>0 LIMIT 1", b=bc)
            if not lot:
                raise ApiError(f"Barcode {bc} has no stock")
            qty = D(it.get("qty") or lot["bal"])
            line = next((r for r in pend if r["product_id"] == lot["product_id"] and D(r["pending_qty"]) >= qty), None)
            if not line:
                raise ApiError(f"Barcode {bc}: no matching pending sales order line")
            line["pending_qty"] = D(line["pending_qty"]) - qty
            items.append({"barcode": bc, "godown_id": lot["godown_id"], "bin_no": lot["bin_no"], "product_id": lot["product_id"], "qty": qty,
                          "rate": line["rate"], "disc_pct": line["disc_pct"], "disc_amt": 0, "pricelist_id": line["pricelist_id"],
                          "description": line["description"], "customer_item_name": line["customer_item_name"],
                          "src_header_id": line["src_header_id"], "src_item_id": line["src_item_id"], "ref_no": line["src_no"],
                          "salesman_id": line["h_salesman_id"], "broker_id": line["h_broker_id"], "retailer_name": line["h_retailer_name"]})
        p["items"] = items
        hid = save_voucher(conn, "challan", p, None, channel="api")
        h = get_voucher(conn, hid)
    return jsonify({"id": hid, "voucher_no": h["voucher_no"], "total_value": h["total_value"], "status": h["approval_status"]}), 201
