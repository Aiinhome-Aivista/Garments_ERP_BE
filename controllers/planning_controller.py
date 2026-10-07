"""Production planning (sales orders -> FG balance -> BOM material need -> requisition) and Requisition for PO."""
from decimal import Decimal

from flask import g, jsonify, request

from app import db
from controllers.auth_controller import require
from app.errors import ApiError
from controllers.stock_controller import free_stock
from controllers.vouchers_controller import D, PENDING_SQL, next_number


MAX_DEPTH = 5

def _f(x):
    return float(D(x))

def _pick_type(conn, kind, given=None):
    if given:
        return int(given)
    tid = db.scalar(conn, "SELECT id FROM m_txn_type WHERE txn_kind=:k AND active=1 ORDER BY id LIMIT 1", k=kind)
    if not tid:
        raise ApiError(f"Create a transaction type of kind '{kind}' first (Masters > Transaction types)")
    return tid


# ------------------------------------------------------------------ analysis
def _po_pending(conn, pid):
    rows = db.all_(conn, PENDING_SQL.format(party=""), kind="purchase_order", excl=0)
    return sum(D(r["pending_qty"]) for r in rows if r["product_id"] == pid)

def analyze(conn, order_ids):
    if not order_ids:
        raise ApiError("Select at least one sales order")
    lines = [r for r in db.all_(conn, PENDING_SQL.format(party=""), kind="sales_order", excl=0) if r["src_header_id"] in set(order_ids)]
    if not lines:
        raise ApiError("The selected orders have nothing pending to produce")
    need = {}
    for r in lines:
        need[r["product_id"]] = need.get(r["product_id"], 0) + D(r["pending_qty"])
    names = lambda pid: db.scalar(conn, "SELECT item_name FROM m_product WHERE id=:i", i=pid)
    uom = lambda pid: db.scalar(conn, "SELECT u.name FROM m_product p JOIN m_uom u ON u.id=p.uom_id WHERE p.id=:i", i=pid)

    fg, balances = [], {}
    for pid, req in need.items():
        free = max(D(free_stock(conn, pid)), D(0))
        bal = max(req - free, D(0))
        balances[pid] = bal
        fg.append({"product_id": pid, "item_name": names(pid), "uom": uom(pid), "required_qty": _f(req),
                   "free_stock": _f(free), "balance_qty": _f(bal), "reserve_qty": _f(min(req, free))})

    raw, detail, used_free = {}, [], {}

    def explode(pid, qty, depth, root):
        rows = db.all_(conn, "SELECT pp.*, pr.name process_name FROM m_product_process pp JOIN m_process pr ON pr.id=pp.process_id "
                             "WHERE pp.product_id=:p ORDER BY pp.seq", p=pid)
        for r in rows:
            if not r["material_id"]:
                continue
            need_q = qty * D(r["qty"])
            if need_q <= 0:
                continue
            mid = r["material_id"]
            detail.append({"fg": names(root), "process": r["process_name"], "part_name": r["part_name"], "material": names(mid),
                           "per_unit": _f(r["qty"]), "required": _f(need_q), "out_status": r["out_status"]})
            has_bom = db.scalar(conn, "SELECT COUNT(*) FROM m_product_process WHERE product_id=:p AND material_id IS NOT NULL", p=mid)
            if has_bom and depth < MAX_DEPTH:              # semi-finished input: use its free stock, explode the rest
                left = max(D(free_stock(conn, mid)) - used_free.get(mid, D(0)), D(0))
                take = min(need_q, left)
                used_free[mid] = used_free.get(mid, D(0)) + take
                if need_q - take > 0:
                    explode(mid, need_q - take, depth + 1, root)
            else:
                raw[mid] = raw.get(mid, D(0)) + need_q

    for pid, bal in balances.items():
        if bal > 0:
            explode(pid, bal, 0, pid)

    materials = []
    for mid, req in raw.items():
        free = max(D(free_stock(conn, mid)), D(0))
        po = _po_pending(conn, mid)
        short = max(req - free - po, D(0))
        materials.append({"product_id": mid, "item_name": names(mid), "uom": uom(mid), "required_qty": _f(req),
                          "free_stock": _f(free), "po_pending": _f(po), "shortfall_qty": _f(short), "reserve_qty": _f(min(req, free))})
    materials.sort(key=lambda m: m["item_name"])
    missing = [f["item_name"] for f in fg if f["balance_qty"] > 0 and not any(d["fg"] == f["item_name"] for d in detail)]
    return {"fg": fg, "materials": materials, "detail": detail, "no_bom": missing,
            "orders": sorted({(r["src_header_id"], r["src_no"]) for r in lines})}


@require("planning", "view")
def open_orders():
    with db.tx() as conn:
        rows = db.all_(conn, PENDING_SQL.format(party=""), kind="sales_order", excl=0)
        planned = {r["order_id"] for r in db.all_(conn, "SELECT po.order_id FROM prod_plan_order po JOIN prod_plan p ON p.id=po.plan_id WHERE p.status='Accepted'")}
        by = {}
        for r in rows:
            if r["src_header_id"] in planned:
                continue
            o = by.setdefault(r["src_header_id"], {"id": r["src_header_id"], "voucher_no": r["src_no"], "voucher_date": r["src_date"],
                                                   "lines": 0, "pending_qty": 0, "party": None})
            o["lines"] += 1
            o["pending_qty"] += float(r["pending_qty"])
        for oid, o in by.items():
            o["party"] = db.scalar(conn, "SELECT l.name FROM txn_header h JOIN m_ledger l ON l.id=h.party_id WHERE h.id=:i", i=oid)
    return jsonify(list(by.values()))


@require("planning", "view")
def analyze_():
    with db.tx() as conn:
        res = analyze(conn, [int(x) for x in (request.get_json(force=True) or {}).get("order_ids", [])])
    res["orders"] = [{"id": a, "voucher_no": b} for a, b in res["orders"]]
    return jsonify(res)


@require("planning", "create")
def accept():
    d = request.get_json(force=True) or {}
    with db.tx() as conn:
        res = analyze(conn, [int(x) for x in d.get("order_ids", [])])
        plan_no, _ = next_number(conn, _pick_type(conn, "Production Plan", d.get("txn_type_id")), "Production Plan")
        pid = db.insert(conn, "prod_plan", {"plan_no": plan_no, "plan_date": (d.get("plan_date") or "")[:10] or db.scalar(conn, "SELECT CURDATE()"),
                                            "remarks": (d.get("remarks") or "").strip() or None, "created_by": g.user["id"]})
        for oid, _ in res["orders"]:
            db.insert(conn, "prod_plan_order", {"plan_id": pid, "order_id": oid})
        for f in res["fg"]:
            db.insert(conn, "prod_plan_fg", {"plan_id": pid, "product_id": f["product_id"], "required_qty": f["required_qty"],
                                             "free_stock": f["free_stock"], "balance_qty": f["balance_qty"]})
            if f["reserve_qty"] > 0:
                db.insert(conn, "stock_reservation", {"plan_id": pid, "product_id": f["product_id"], "qty": f["reserve_qty"]})
        short = []
        for m in res["materials"]:
            db.insert(conn, "prod_plan_material", {"plan_id": pid, "product_id": m["product_id"], "required_qty": m["required_qty"],
                                                   "free_stock": m["free_stock"], "po_pending": m["po_pending"], "shortfall_qty": m["shortfall_qty"]})
            if m["reserve_qty"] > 0:
                db.insert(conn, "stock_reservation", {"plan_id": pid, "product_id": m["product_id"], "qty": m["reserve_qty"]})
            if m["shortfall_qty"] > 0:
                short.append(m)
        req_no = None
        if short:
            req_tt = _pick_type(conn, "Requisition", d.get("req_txn_type_id"))
            req_no, _ = next_number(conn, req_tt, "Requisition")
            rid = db.insert(conn, "requisition", {"req_no": req_no, "req_date": db.scalar(conn, "SELECT CURDATE()"), "source": "Planning",
                                                  "plan_id": pid, "txn_type_id": req_tt, "created_by": g.user["id"],
                                                  "remarks": f"Auto from plan {plan_no}"})
            for m in short:
                db.insert(conn, "requisition_item", {"requisition_id": rid, "product_id": m["product_id"], "qty": m["shortfall_qty"]})
            db.run(conn, "UPDATE prod_plan SET requisition_id=:r WHERE id=:p", r=rid, p=pid)
    return jsonify({"id": pid, "plan_no": plan_no, "requisition_no": req_no}), 201


@require("planning", "view")
def plans():
    with db.tx() as conn:
        return jsonify(db.all_(conn, "SELECT p.id,p.plan_no,p.plan_date,p.status,p.remarks,r.req_no,"
                                     "(SELECT COUNT(*) FROM prod_plan_order WHERE plan_id=p.id) orders "
                                     "FROM prod_plan p LEFT JOIN requisition r ON r.id=p.requisition_id ORDER BY p.id DESC LIMIT 200"))


@require("planning", "view")
def plan_get(pid):
    with db.tx() as conn:
        p = db.one(conn, "SELECT p.*, r.req_no FROM prod_plan p LEFT JOIN requisition r ON r.id=p.requisition_id WHERE p.id=:i", i=pid)
        if not p:
            raise ApiError("Plan not found", 404)
        p["orders"] = db.all_(conn, "SELECT h.id,h.voucher_no FROM prod_plan_order o JOIN txn_header h ON h.id=o.order_id WHERE o.plan_id=:i", i=pid)
        p["fg"] = db.all_(conn, "SELECT f.*,pr.item_name FROM prod_plan_fg f JOIN m_product pr ON pr.id=f.product_id WHERE f.plan_id=:i", i=pid)
        p["materials"] = db.all_(conn, "SELECT f.*,pr.item_name FROM prod_plan_material f JOIN m_product pr ON pr.id=f.product_id WHERE f.plan_id=:i", i=pid)
    return jsonify(p)


@require("planning", "delete")
def plan_cancel(pid):
    with db.tx() as conn:
        p = db.one(conn, "SELECT * FROM prod_plan WHERE id=:i FOR UPDATE", i=pid)
        if not p or p["status"] != "Accepted":
            raise ApiError("Only accepted plans can be cancelled")
        if p["requisition_id"] and db.scalar(conn, "SELECT COUNT(*) FROM txn_item x JOIN requisition_item ri ON ri.id=x.req_item_id "
                                                   "WHERE ri.requisition_id=:r", r=p["requisition_id"]):
            raise ApiError("Purchase orders were already raised from this plan's requisition", 409)
        db.run(conn, "UPDATE stock_reservation SET active=0 WHERE plan_id=:i", i=pid)
        db.run(conn, "UPDATE prod_plan SET status='Cancelled' WHERE id=:i", i=pid)
        if p["requisition_id"]:
            db.run(conn, "UPDATE requisition SET status='Cancelled' WHERE id=:r", r=p["requisition_id"])
    return jsonify({"ok": True})


@require("planning", "edit")
def plan_complete(pid):
    with db.tx() as conn:
        p = db.one(conn, "SELECT * FROM prod_plan WHERE id=:i FOR UPDATE", i=pid)
        if not p or p["status"] != "Accepted":
            raise ApiError("Only accepted plans can be completed")
        
        # 1. Clear stock reservations
        db.run(conn, "UPDATE stock_reservation SET active=0 WHERE plan_id=:i", i=pid)
        
        # 2. Create a dummy txn_header for the production
        hid = db.insert(conn, "txn_header", {
            "doc_type": "production",
            "voucher_no": "PR/" + p["plan_no"],
            "voucher_date": db.scalar(conn, "SELECT CURRENT_DATE()"),
            "approval_status": "Approved",
            "remarks": "Auto-completed from plan"
        })
        
        # 3. Consume raw materials
        materials = db.all_(conn, "SELECT * FROM prod_plan_material WHERE plan_id=:i", i=pid)
        for m in materials:
            # find where it's stored and consume it
            lots = db.all_(conn, "SELECT barcode, godown_id, bin_no, SUM(qty) bal FROM stock_ledger WHERE product_id=:p GROUP BY barcode, godown_id, bin_no HAVING SUM(qty)>0 ORDER BY MIN(txn_date)", p=m["product_id"])
            needed = m["required_qty"]
            for lot in lots:
                if needed <= 0: break
                take = min(needed, lot["bal"])
                needed -= take
                it = {"header_id": hid, "product_id": m["product_id"], "qty": take, "barcode": lot["barcode"], "godown_id": lot["godown_id"], "bin_no": lot["bin_no"], "line_no": 1}
                iid = db.insert(conn, "txn_item", it)
                db.insert(conn, "stock_ledger", {"header_id": hid, "item_id": iid, "txn_date": p["plan_date"], "product_id": m["product_id"], "barcode": lot["barcode"], "godown_id": lot["godown_id"], "bin_no": lot["bin_no"], "qty": -take})
            
            if needed > 0:
                raise ApiError(f"Not enough stock to consume for RM. Need {needed} more.")
                
        # 4. Receive finished goods
        fgs = db.all_(conn, "SELECT * FROM prod_plan_fg WHERE plan_id=:i", i=pid)
        for f in fgs:
            # We need a godown_id. Let's just pick the first godown in the system.
            godown = db.one(conn, "SELECT id FROM m_godown LIMIT 1")
            gid = godown["id"] if godown else 1
            it = {"header_id": hid, "product_id": f["product_id"], "qty": f["required_qty"], "godown_id": gid, "line_no": 2}
            iid = db.insert(conn, "txn_item", it)
            bc = f"B{f['product_id']:05d}{hid:07d}02"
            db.run(conn, "UPDATE txn_item SET barcode=:b WHERE id=:i", b=bc, i=iid)
            db.insert(conn, "stock_ledger", {"header_id": hid, "item_id": iid, "txn_date": p["plan_date"], "product_id": f["product_id"], "barcode": bc, "godown_id": gid, "bin_no": "F1", "qty": f["required_qty"]})
            
        # 5. Update plan status
        db.run(conn, "UPDATE prod_plan SET status='Completed' WHERE id=:i", i=pid)
        
    return jsonify({"ok": True})


# ------------------------------------------------------------------ requisition for PO
REQ_LIST = """SELECT r.id,r.req_no,r.req_date,r.source,r.status,r.remarks,p.plan_no,
 (SELECT COUNT(*) FROM requisition_item WHERE requisition_id=r.id) line_count,
 (SELECT COALESCE(SUM(qty),0) FROM requisition_item WHERE requisition_id=r.id) qty,
 (SELECT COALESCE(SUM(x.qty),0) FROM txn_item x JOIN requisition_item ri ON ri.id=x.req_item_id
    JOIN txn_header xh ON xh.id=x.header_id WHERE ri.requisition_id=r.id AND xh.approval_status<>'Rejected') ordered
 FROM requisition r LEFT JOIN prod_plan p ON p.id=r.plan_id"""


@require("requisition", "view")
def req_list():
    st = request.args.get("status")
    with db.tx() as conn:
        return jsonify(db.all_(conn, REQ_LIST + (" WHERE r.status=:s" if st else "") + " ORDER BY r.id DESC LIMIT 200", **({"s": st} if st else {})))


@require("requisition", "view")
def req_get(rid):
    with db.tx() as conn:
        r = db.one(conn, REQ_LIST + " WHERE r.id=:i", i=rid)
        if not r:
            raise ApiError("Requisition not found", 404)
        r["items"] = db.all_(conn, "SELECT ri.id,ri.product_id,ri.qty,ri.remark,p.item_name `product_id__label`,u.name uom,"
                                   "(SELECT COALESCE(SUM(x.qty),0) FROM txn_item x JOIN txn_header xh ON xh.id=x.header_id "
                                   " WHERE x.req_item_id=ri.id AND xh.approval_status<>'Rejected') ordered "
                                   "FROM requisition_item ri JOIN m_product p ON p.id=ri.product_id JOIN m_uom u ON u.id=p.uom_id "
                                   "WHERE ri.requisition_id=:i", i=rid)
    return jsonify(r)


@require("requisition", "create")
def req_suggest():
    """Items whose projected stock (free + open PO + open requisition) is below minimum level."""
    out = []
    with db.tx() as conn:
        for p in db.all_(conn, "SELECT p.id,p.item_name,p.min_stock,u.name uom FROM m_product p JOIN m_uom u ON u.id=p.uom_id "
                               "WHERE p.active=1 AND p.min_stock>0"):
            free = D(free_stock(conn, p["id"]))
            po = _po_pending(conn, p["id"])
            rq = D(db.scalar(conn, "SELECT COALESCE(SUM(ri.qty),0) - COALESCE((SELECT SUM(x.qty) FROM txn_item x JOIN txn_header xh ON xh.id=x.header_id "
                                   "JOIN requisition_item r2 ON r2.id=x.req_item_id JOIN requisition rq2 ON rq2.id=r2.requisition_id "
                                   "WHERE r2.product_id=:p AND rq2.status='Open' AND xh.approval_status<>'Rejected'),0) "
                                   "FROM requisition_item ri JOIN requisition r ON r.id=ri.requisition_id WHERE ri.product_id=:p AND r.status='Open'", p=p["id"]))
            proj = free + po + max(rq, D(0))
            if proj < D(p["min_stock"]):
                out.append({"product_id": p["id"], "product_id__label": p["item_name"], "uom": p["uom"], "qty": _f(D(p["min_stock"]) - proj),
                            "remark": f"Min {p['min_stock']}, free {_f(free)}, on order {_f(po + max(rq, D(0)))}"})
    return jsonify(out)

def _save_req(conn, payload, rid=None):
    items = [i for i in payload.get("items") or [] if i.get("product_id") and D(i.get("qty")) > 0]
    if not items:
        raise ApiError("Add at least one item with a quantity")
    if rid:
        r = db.one(conn, "SELECT * FROM requisition WHERE id=:i FOR UPDATE", i=rid)
        if not r or r["source"] != "Manual" or r["status"] != "Open":
            raise ApiError("Only open, manual requisitions can be edited")
        if db.scalar(conn, "SELECT COUNT(*) FROM txn_item x JOIN requisition_item ri ON ri.id=x.req_item_id WHERE ri.requisition_id=:r", r=rid):
            raise ApiError("Purchase orders already use this requisition", 409)
        db.run(conn, "DELETE FROM requisition_item WHERE requisition_id=:r", r=rid)
        db.run(conn, "UPDATE requisition SET remarks=:m, req_date=:d WHERE id=:r", m=(payload.get("remarks") or "").strip() or None,
               d=(payload.get("req_date") or "")[:10] or db.scalar(conn, "SELECT CURDATE()"), r=rid)
    else:
        no, _ = next_number(conn, _pick_type(conn, "Requisition", payload.get("txn_type_id")), "Requisition")
        rid = db.insert(conn, "requisition", {"req_no": no, "req_date": (payload.get("req_date") or "")[:10] or db.scalar(conn, "SELECT CURDATE()"),
                                              "source": "Manual", "remarks": (payload.get("remarks") or "").strip() or None,
                                              "txn_type_id": _pick_type(conn, "Requisition", payload.get("txn_type_id")), "created_by": g.user["id"]})
    for i in items:
        db.insert(conn, "requisition_item", {"requisition_id": rid, "product_id": int(i["product_id"]), "qty": D(i["qty"]),
                                             "remark": (i.get("remark") or "").strip() or None})
    return rid


@require("requisition", "create")
def req_create():
    with db.tx() as conn:
        rid = _save_req(conn, request.get_json(force=True) or {})
    return req_get(rid)


@require("requisition", "edit")
def req_update(rid):
    with db.tx() as conn:
        _save_req(conn, request.get_json(force=True) or {}, rid)
    return req_get(rid)


@require("requisition", "edit")
def req_close(rid):
    with db.tx() as conn:
        db.run(conn, "UPDATE requisition SET status='Closed' WHERE id=:i AND status='Open'", i=rid)
    return jsonify({"ok": True})


@require("requisition", "delete")
def req_delete(rid):
    with db.tx() as conn:
        r = db.one(conn, "SELECT * FROM requisition WHERE id=:i", i=rid)
        if not r or r["source"] != "Manual":
            raise ApiError("Only manual requisitions can be deleted")
        if db.scalar(conn, "SELECT COUNT(*) FROM txn_item x JOIN requisition_item ri ON ri.id=x.req_item_id WHERE ri.requisition_id=:r", r=rid):
            raise ApiError("Purchase orders already use this requisition", 409)
        db.run(conn, "DELETE FROM requisition WHERE id=:i", i=rid)
    return jsonify({"ok": True})
