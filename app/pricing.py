"""Resolve price list, rate and discount for a customer + item, using the customer's discount structure."""
from decimal import Decimal

from flask import Blueprint, jsonify, request

from . import db
from .auth import login_required

bp = Blueprint("pricing", __name__, url_prefix="/api")


def group_chain(conn, group_id):
    chain, seen = [], set()
    while group_id and group_id not in seen:
        seen.add(group_id)
        chain.append(group_id)
        group_id = db.scalar(conn, "SELECT parent_id FROM m_product_group WHERE id=:i", i=group_id)
    return chain            # nearest group first


def resolve(conn, party_id, product_id, on_date):
    out = {"pricelist_id": None, "pricelist_label": None, "rate": 0, "disc_pct": 0, "disc_amt": 0, "customer_item_name": None}
    prod = db.one(conn, "SELECT group_id FROM m_product WHERE id=:i", i=product_id)
    if not prod:
        return out
    cin = db.scalar(conn, "SELECT customer_item_name FROM m_product_customer WHERE product_id=:p AND customer_id=:c LIMIT 1",
                    p=product_id, c=party_id)
    out["customer_item_name"] = cin
    chosen = None
    for gid in group_chain(conn, prod["group_id"]):        # nearest group wins; latest applicable date wins
        chosen = db.one(conn, "SELECT * FROM m_ledger_discount WHERE ledger_id=:l AND product_group_id=:g "
                              "AND (applicable_date IS NULL OR applicable_date<=:d) ORDER BY applicable_date DESC, id DESC LIMIT 1",
                        l=party_id, g=gid, d=on_date)
        if chosen:
            break
    pct = Decimal(0)
    if chosen:
        pct = Decimal(str(chosen["disc_pct"] or 0))
        if chosen["pricelist_id"]:
            item = db.one(conn, "SELECT i.*, p.name pl FROM m_pricelist_item i JOIN m_pricelist p ON p.id=i.pricelist_id "
                                "WHERE i.pricelist_id=:l AND i.product_id=:p LIMIT 1", l=chosen["pricelist_id"], p=product_id)
            out["pricelist_id"] = chosen["pricelist_id"]
            out["pricelist_label"] = db.scalar(conn, "SELECT name FROM m_pricelist WHERE id=:i", i=chosen["pricelist_id"])
            if item:
                out["rate"] = float(item["rate"])
                if pct == 0:
                    out["disc_pct"], out["disc_amt"] = float(item["disc_pct"]), float(item["disc_amt"])
    out["disc_pct"] = float(pct) if pct else out["disc_pct"]
    return out


@bp.get("/pricing/resolve")
@login_required
def resolve_():
    a = request.args
    with db.tx() as conn:
        return jsonify(resolve(conn, a.get("party_id"), a.get("product_id"), a.get("date") or "9999-12-31"))


@bp.get("/pricing/pricelist-rate")
@login_required
def pricelist_rate():
    a = request.args
    with db.tx() as conn:
        r = db.one(conn, "SELECT rate,disc_pct,disc_amt FROM m_pricelist_item WHERE pricelist_id=:l AND product_id=:p LIMIT 1",
                   l=a.get("pricelist_id"), p=a.get("product_id"))
    return jsonify(r or {"rate": 0, "disc_pct": 0, "disc_amt": 0})
