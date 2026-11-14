"""Resolve price list, rate and discount for a customer + item, using the customer's discount structure."""
from decimal import Decimal

from flask import jsonify, request

from app import db
from controllers.auth_controller import login_required


def group_chain(conn, group_id):
    chain, seen = [], set()
    while group_id and group_id not in seen:
        seen.add(group_id)
        chain.append(group_id)
        group_id = db.scalar(conn, "SELECT parent_id FROM m_product_group WHERE id=:i", i=group_id)
    return chain            # nearest group first

def resolve(conn, party_id, product_id, on_date):
    out = {
        "pricelist_id": None,
        "pricelist_label": None,
        "rate": 0,
        "disc_pct": 0,
        "disc_amt": 0,
        "customer_item_name": None,
        "has_pricelist": False,
        "available_pricelists": [],
    }
    if not product_id:
        return out
    prod = db.one(conn, "SELECT p.group_id, p.gst_type, p.gst_rate, p.uom_id, u.name uom_name, u.decimals, u.allow_fraction "
                        "FROM m_product p JOIN m_uom u ON u.id=p.uom_id WHERE p.id=:i", i=product_id)
    if not prod:
        return out
    gst_type = prod.get("gst_type") or "Flat"
    out["gst_type"] = gst_type
    out["gst_rate"] = float(prod.get("gst_rate") or 0)
    slabs = []
    if gst_type == "Slab":
        slab_rows = db.all_(conn, "SELECT min_price, max_price, gst_rate FROM m_product_gst_slab WHERE product_id=:p ORDER BY min_price ASC", p=product_id)
        for s in slab_rows:
            slabs.append({
                "min_price": float(s["min_price"]),
                "max_price": float(s["max_price"]) if s["max_price"] is not None else None,
                "gst_rate": float(s["gst_rate"]),
            })
    out["gst_slabs"] = slabs
    out["main_uom_id"] = prod["uom_id"]
    out["main_uom_name"] = prod["uom_name"]

    # Fetch Main UOM and all Alternate UOMs for this product
    uoms = [{
        "uom_id": prod["uom_id"],
        "name": prod["uom_name"],
        "conversion_factor": 1.0,
        "is_main": True,
        "allow_fraction": bool(prod.get("allow_fraction") or (prod.get("decimals") or 0) > 0)
    }]
    alts = db.all_(conn, """
        SELECT u.id AS uom_id, u.name, u.decimals, u.allow_fraction, pu.conversion_factor
        FROM m_product_uom pu
        JOIN m_uom u ON u.id=pu.uom_id
        WHERE pu.product_id=:p AND u.active=1
        ORDER BY u.name
    """, p=product_id)
    for a in alts:
        uoms.append({
            "uom_id": a["uom_id"],
            "name": a["name"],
            "conversion_factor": float(a["conversion_factor"]),
            "is_main": False,
            "allow_fraction": bool(a.get("allow_fraction") or (a.get("decimals") or 0) > 0)
        })
    out["uoms"] = uoms

    # Check active price lists that contain this item
    available_pls = db.all_(conn, """
        SELECT p.id, p.name 
        FROM m_pricelist_item pi 
        JOIN m_pricelist p ON p.id = pi.pricelist_id 
        WHERE pi.product_id = :p AND p.active = 1
        ORDER BY p.name
    """, p=product_id)
    out["has_pricelist"] = len(available_pls) > 0
    out["available_pricelists"] = available_pls

    if party_id:
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

    # If no pricelist has been resolved yet but price lists are available for this product:
    if not out["pricelist_id"] and available_pls:
        pl = available_pls[0]
        out["pricelist_id"] = pl["id"]
        out["pricelist_label"] = pl["name"]
        item = db.one(conn, "SELECT rate, disc_pct, disc_amt FROM m_pricelist_item WHERE pricelist_id=:l AND product_id=:p LIMIT 1",
                      l=pl["id"], p=product_id)
        if item:
            out["rate"] = float(item["rate"])
            if out["disc_pct"] == 0:
                out["disc_pct"] = float(item["disc_pct"])
                out["disc_amt"] = float(item["disc_amt"])

    return out


@login_required
def resolve_():
    a = request.args
    with db.tx() as conn:
        return jsonify(resolve(conn, a.get("party_id"), a.get("product_id"), a.get("date") or "9999-12-31"))


@login_required
def pricelist_rate():
    a = request.args
    plid = a.get("pricelist_id")
    pid = a.get("product_id")
    party_id = a.get("party_id")
    vdate = a.get("date") or "9999-12-31"
    with db.tx() as conn:
        r = db.one(conn, "SELECT rate,disc_pct,disc_amt FROM m_pricelist_item WHERE pricelist_id=:l AND product_id=:p LIMIT 1",
                   l=plid, p=pid)
        out = dict(r) if r else {"rate": 0, "disc_pct": 0, "disc_amt": 0}
        if party_id and pid:
            prod = db.one(conn, "SELECT group_id FROM m_product WHERE id=:i", i=pid)
            if prod:
                for gid in group_chain(conn, prod["group_id"]):
                    chosen = db.one(conn, "SELECT disc_pct FROM m_ledger_discount WHERE ledger_id=:l AND product_group_id=:g "
                                          "AND (applicable_date IS NULL OR applicable_date<=:d) ORDER BY applicable_date DESC, id DESC LIMIT 1",
                                    l=party_id, g=gid, d=vdate)
                    if chosen and chosen["disc_pct"]:
                        out["disc_pct"] = float(chosen["disc_pct"])
                        break
    return jsonify(out)
