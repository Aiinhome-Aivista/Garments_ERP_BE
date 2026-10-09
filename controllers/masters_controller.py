"""Generic CRUD for every master in registry.MASTERS, plus type-ahead lookup used by all forms."""
import datetime as dt
import json
from decimal import Decimal, InvalidOperation

from flask import g, jsonify, request
from sqlalchemy.exc import IntegrityError

from app import db
from controllers.auth_controller import login_required, require
from app.errors import ApiError
from app.registry import MASTERS, field_names, label_col


def _m(key):
    if key not in MASTERS:
        raise ApiError("Unknown master", 404)
    return MASTERS[key]


# ------------------------------------------------------------------ SQL builders
def _select(fields, table, hierarchical=False):
    joins, cols = [], ["t.*"]
    for i, f in enumerate(fields):
        if f["type"] == "ref":
            ref = MASTERS[f["ref"]]
            a = f"r{i}"
            joins.append(f"LEFT JOIN `{ref['table']}` {a} ON {a}.id=t.`{f['name']}`")
            cols.append(f"{a}.`{label_col(f['ref'])}` AS `{f['name']}__label`")
    if hierarchical:
        joins.append(f"LEFT JOIN `{table}` pp ON pp.id=t.parent_id")
        cols.append("pp.name AS parent__label")
    return f"SELECT {', '.join(cols)} FROM `{table}` t {' '.join(joins)}"

def _post(m, row):
    for f in m["fields"]:
        if f["type"] == "attrs" and isinstance(row.get(f["name"]), (str, bytes)):
            row[f["name"]] = json.loads(row[f["name"]])
    return row

def _paths(conn, table):
    rows = db.all_(conn, f"SELECT id,name,parent_id FROM `{table}`")
    by = {r["id"]: r for r in rows}
    out = {}
    for r in rows:
        chain, cur, seen = [], r, set()
        while cur and cur["id"] not in seen:
            seen.add(cur["id"])
            chain.append(cur["name"])
            cur = by.get(cur["parent_id"])
        out[r["id"]] = (" › ".join(reversed(chain)), len(chain) - 1)
    return out


# ------------------------------------------------------------------ list / lookup
def _filters(m, key):
    """Turn ?f_col=val into WHERE parts; columns must exist on the master."""
    ok = field_names(key) | ({"parent_id"} if m.get("hierarchical") else set())
    where, p = [], {}
    for k, v in request.args.items():
        if k.startswith("f_") and k[2:] in ok:
            vals = [x for x in v.split(",") if x != ""] if "," in v else [v]
            if len(vals) > 1:
                names = []
                for n, x in enumerate(vals):
                    p[f"{k}_{n}"] = x
                    names.append(f":{k}_{n}")
                where.append(f"t.`{k[2:]}` IN ({','.join(names)})")
            else:
                where.append(f"t.`{k[2:]}`=:{k}")
                p[k] = v
        elif k.startswith("s_") and v:
            col = k[2:]
            from app.registry import label_col as get_label_col
            if col in ok or col == get_label_col(key):
                field = next((f for f in m.get("fields", []) if f["name"] == col), None)
                vals = [x.strip() for x in v.split(",") if x.strip()]
                if not vals: continue
                or_conds = []
                for i, val in enumerate(vals):
                    pk = f"{k}_{i}"
                    if col == get_label_col(key) or (field and field["type"] in ("text", "textarea")):
                        or_conds.append(f"t.`{col}` LIKE :{pk}")
                        p[pk] = f"%{val}%"
                    elif field and field["type"] == "ref":
                        from app.registry import MASTERS
                        ref = MASTERS[field["ref"]]
                        or_conds.append(f"t.`{col}` IN (SELECT id FROM `{ref['table']}` WHERE `{get_label_col(field['ref'])}` LIKE :{pk})")
                        p[pk] = f"%{val}%"
                    else:
                        or_conds.append(f"t.`{col}` LIKE :{pk}")
                        p[pk] = f"%{val}%"
                where.append("(" + " OR ".join(or_conds) + ")")
    return where, p

def _query(key, limit=None, offset=0, lookup=False):
    m = _m(key)
    where, p = _filters(m, key)
    q = (request.args.get("q") or "").strip()
    search_col = (request.args.get("search_col") or "").strip()
    q_hier = False
    if q:
        if m.get("hierarchical") and not search_col:
            q_hier = True
        elif search_col:
            field = next((f for f in m["fields"] if f["name"] == search_col), None)
            if search_col == label_col(key):
                where.append(f"t.`{search_col}` LIKE :q")
                p["q"] = f"%{q}%"
            elif field:
                if field["type"] == "ref":
                    from app.registry import MASTERS, label_col as get_label_col
                    ref = MASTERS[field["ref"]]
                    where.append(f"t.`{search_col}` IN (SELECT id FROM `{ref['table']}` WHERE `{get_label_col(field['ref'])}` LIKE :q)")
                else:
                    where.append(f"t.`{search_col}` LIKE :q")
                p["q"] = f"%{q}%"
        else:
            or_parts = []
            cols_text = [label_col(key)] + [f["name"] for f in m["fields"] if f.get("list") and f["type"] == "text" and f["name"] != label_col(key)]
            for c in cols_text:
                or_parts.append(f"t.`{c}` LIKE :q")
            for f in m["fields"]:
                if f.get("list") and f["type"] == "ref":
                    from app.registry import MASTERS, label_col as get_label_col
                    ref = MASTERS[f["ref"]]
                    or_parts.append(f"t.`{f['name']}` IN (SELECT id FROM `{ref['table']}` WHERE `{get_label_col(f['ref'])}` LIKE :q)")
            if or_parts:
                where.append("(" + " OR ".join(or_parts) + ")")
                p["q"] = f"%{q}%"
    act = request.args.get("active", "1")
    if lookup or act in ("0", "1"):
        where.append("t.active=:act")
        p["act"] = 1 if lookup else int(act)
    base = _select(m["fields"], m["table"], m.get("hierarchical"))
    wsql = (" WHERE " + " AND ".join(where)) if where else ""
    with db.tx() as c:
        total = db.scalar(c, f"SELECT COUNT(*) FROM `{m['table']}` t{wsql}", **p)
        order = f"t.`{label_col(key)}`" if not m.get("hierarchical") else "t.id"
        lim = f" LIMIT {int(limit)} OFFSET {int(offset)}" if limit and not m.get("hierarchical") else ""
        rows = db.all_(c, f"{base}{wsql} ORDER BY {order}{lim}", **p)
        if m.get("hierarchical"):
            paths = _paths(c, m["table"])
            for r in rows:
                r["path"], r["depth"] = paths.get(r["id"], (r["name"], 0))
            if q_hier:
                q_lower = q.lower()
                rows = [r for r in rows if q_lower in r["path"].lower()]
                total = len(rows)
            rows.sort(key=lambda r: r["path"].lower())
            if limit:
                rows = rows[int(offset):int(offset)+int(limit)]
    return [_post(m, r) for r in rows], total


@require(lambda kw: kw["key"], "view")
def list_(key):
    page = max(int(request.args.get("page", 1)), 1)
    size = min(max(int(request.args.get("page_size", 50)), 1), 500)
    hier = _m(key).get("hierarchical")
    rows, total = _query(key, None if hier else size, 0 if hier else (page - 1) * size)
    return jsonify({"rows": rows, "total": total, "page": page, "page_size": size})


@login_required
def lookup(key):
    lim = min(int(request.args.get("limit", 30)), 100)
    rows, _ = _query(key, lim, 0, lookup=True)
    lc = label_col(key)
    for r in rows:
        r["label"] = r.get("path") or r.get(lc)
    # make sure a requested id is always resolvable (for edit forms on inactive rows)
    rid = request.args.get("id")
    if rid and not any(str(r["id"]) == rid for r in rows):
        m = _m(key)
        with db.tx() as c:
            one = db.one(c, _select(m["fields"], m["table"], m.get("hierarchical")) + " WHERE t.id=:i", i=rid)
        if one:
            one["label"] = one.get(lc)
            rows.insert(0, _post(m, one))
    return jsonify(rows)


# ------------------------------------------------------------------ get one
def load_one(conn, key, rid):
    m = _m(key)
    row = db.one(conn, _select(m["fields"], m["table"], m.get("hierarchical")) + " WHERE t.id=:i", i=rid)
    if not row:
        raise ApiError("Record not found", 404)
    _post(m, row)
    for ch in m.get("children", []):
        order = ch.get("order", "id")
        row[ch["key"]] = db.all_(conn, _select(ch["fields"], ch["table"]) + f" WHERE t.`{ch['fk']}`=:i ORDER BY t.`{order}`, t.id", i=rid)
    return row


@login_required
def get_(key, rid):
    with db.tx() as c:
        return jsonify(load_one(c, key, rid))


# ------------------------------------------------------------------ validation
def _num(v, label, integer=False):
    try:
        d = Decimal(str(v))
    except InvalidOperation:
        raise ApiError(f"{label} must be a number")
    return int(d) if integer else d

def clean_fields(fields, data, label_prefix="", creating=True):
    out = {}
    for f in fields:
        n, t = f["name"], f["type"]
        if t == "readonly":
            continue
        if n not in data and not creating:
            continue
        v = data.get(n)
        if isinstance(v, str):
            v = v.strip()
        if v == "" or v is None:
            v = None
        if v is None and f.get("required") and t != "bool":
            raise ApiError(f"{label_prefix}{f['label']} is required", field=n)
        if t == "bool":
            v = 1 if v else 0
        elif v is not None:
            if t in ("int",):
                v = _num(v, f["label"], True)
            elif t == "decimal":
                v = _num(v, f["label"])
            elif t == "ref":
                v = int(v)
            elif t == "date":
                try:
                    dt.date.fromisoformat(str(v)[:10])
                except ValueError:
                    raise ApiError(f"{f['label']} is not a valid date", field=n)
                v = str(v)[:10]
            elif t == "select":
                if f.get("options") and v not in f["options"]:
                    raise ApiError(f"{f['label']}: '{v}' is not an option", field=n)
            elif t == "attrs":
                v = json.dumps(v if isinstance(v, dict) else {})
        elif t == "select" and "" in (f.get("options") or []):
            v = None
        if v is None and creating and "default" in f and n not in data:
            v = f["default"]
        if n in data or f.get("required") or "default" in f:
            out[n] = v
    return out


# ------------------------------------------------------------------ hooks
def _ean13(base12):
    s = sum(int(d) * (3 if i % 2 else 1) for i, d in enumerate(base12))
    return base12 + str((10 - s % 10) % 10)

def _product_before(conn, data, payload, rid):
    cat = data.get("category_id")
    attrs = db.all_(conn, "SELECT attr_no,label FROM m_product_category_attr WHERE category_id=:c ORDER BY attr_no", c=cat)
    vals = json.loads(data.get("attr_values") or "{}")
    parts = [data["name"]]
    clean = {}
    for a in attrs:
        v = str(vals.get(str(a["attr_no"])) or "").strip()
        if not v:
            raise ApiError(f"{a['label']} is required for this category", field="attr_values")
        clean[str(a["attr_no"])] = v
        parts.append(v)
    data["attr_values"] = json.dumps(clean)
    data["item_name"] = "-".join(parts)

def _product_after(conn, rid, created):
    if created:
        db.run(conn, "UPDATE m_product SET barcode=:b WHERE id=:i", b=_ean13(f"200{rid:09d}"), i=rid)

def _ledger_before(conn, data, payload, rid):
    if data.get("ledger_type") != "Customer":
        for k in ("broker_id", "salesman_id", "brokerage_rate", "cash_discount", "transporter_id"):
            data[k] = None
        payload["discounts"] = []

def _txn_type_after(conn, rid, created):
    db.run(conn, "UPDATE m_txn_type SET max_number=GREATEST(max_number, start_number-1) WHERE id=:i", i=rid)


HOOKS = {
    "product": (_product_before, _product_after),
    "ledger": (_ledger_before, None),
    "txn_type": (None, _txn_type_after),
}


# ------------------------------------------------------------------ save / delete
def _check_cycle(conn, table, rid, parent):
    seen = set()
    while parent:
        if parent == rid or parent in seen:
            raise ApiError("A record cannot sit under itself", field="parent_id")
        seen.add(parent)
        parent = db.scalar(conn, f"SELECT parent_id FROM `{table}` WHERE id=:i", i=parent)

def save(conn, key, payload, rid=None):
    m = _m(key)
    data = clean_fields(m["fields"], payload, creating=rid is None)
    if m.get("hierarchical"):
        pid = payload.get("parent_id") or None
        data["parent_id"] = int(pid) if pid else None
        if rid:
            _check_cycle(conn, m["table"], rid, data["parent_id"])
    if "active" in payload:
        data["active"] = 1 if payload["active"] else 0
    before, after = HOOKS.get(m.get("hook"), (None, None))
    if before:
        before(conn, data, payload, rid)
    created = rid is None
    if created:
        rid = db.insert(conn, m["table"], data)
    else:
        if not db.scalar(conn, f"SELECT COUNT(*) FROM `{m['table']}` WHERE id=:i", i=rid):
            raise ApiError("Record not found", 404)
        db.update(conn, m["table"], data, "id", rid)
    for ch in m.get("children", []):
        if ch["key"] not in payload:
            continue
        db.run(conn, f"DELETE FROM `{ch['table']}` WHERE `{ch['fk']}`=:i", i=rid)
        seq = 0
        for row in payload[ch["key"]] or []:
            if not any(v not in (None, "", 0) for k, v in row.items() if k in {f["name"] for f in ch["fields"]}):
                continue
            seq += 1
            cd = clean_fields(ch["fields"], row, f"{ch['label']} row {seq}: ")
            cd[ch["fk"]] = rid
            if ch.get("seq_col"):
                cd[ch["seq_col"]] = seq
            if ch.get("max_rows") and seq > ch["max_rows"]:
                raise ApiError(f"{ch['label']} allows at most {ch['max_rows']} rows")
            db.insert(conn, ch["table"], cd)
    if after:
        after(conn, rid, created)
    return rid

def _friendly(e):
    msg = str(e.orig)
    if "Duplicate" in msg:
        return ApiError("That already exists. Names must be unique.", 409)
    if "foreign key" in msg.lower():
        return ApiError("This record is used elsewhere. Mark it inactive instead of deleting.", 409)
    return ApiError("Could not save: " + msg[:160], 400)


@require(lambda kw: kw["key"], "create")
def create(key):
    try:
        with db.tx() as c:
            rid = save(c, key, request.get_json(force=True) or {})
            return jsonify(load_one(c, key, rid)), 201
    except IntegrityError as e:
        raise _friendly(e)


@require(lambda kw: kw["key"], "edit")
def update_(key, rid):
    try:
        with db.tx() as c:
            save(c, key, request.get_json(force=True) or {}, rid)
            return jsonify(load_one(c, key, rid))
    except IntegrityError as e:
        raise _friendly(e)


@require(lambda kw: kw["key"], "delete")
def delete_(key, rid):
    m = _m(key)
    try:
        with db.tx() as c:
            db.run(c, f"DELETE FROM `{m['table']}` WHERE id=:i", i=rid)
    except IntegrityError as e:
        raise _friendly(e)
    return jsonify({"ok": True})
