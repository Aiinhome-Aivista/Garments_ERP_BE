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
        if key == "generic_type":
            order = "CASE t.name WHEN 'Size' THEN 1 WHEN 'Parts' THEN 2 WHEN 'Colour' THEN 3 WHEN 'Pattern' THEN 4 WHEN 'Sleeves' THEN 5 WHEN 'Collection' THEN 6 ELSE 99 END, t.name"
        else:
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


@login_required
def next_no(key):
    if key == "bom":
        with db.tx() as c:
            num = _generate_bom_no(c)
        return jsonify({"next_no": num})
    return jsonify({"next_no": None})


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
    if key == "product":
        # Ensure backwards and forwards compatibility between 'alternate_uoms' and 'units'
        alt = row.get("alternate_uoms") or row.get("units") or []
        if not alt and row.get("alt_uom_id") and row.get("conversion_factor"):
            label = db.scalar(conn, "SELECT name FROM m_uom WHERE id=:i", i=row["alt_uom_id"])
            alt = [{"uom_id": row["alt_uom_id"], "conversion_factor": row["conversion_factor"], "uom_id__label": label}]
        row["alternate_uoms"] = alt
        row["units"] = alt
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

    # Multi-UOM validation
    main_uom = data.get("uom_id")
    if not main_uom:
        raise ApiError("Main UOM is mandatory", field="uom_id")

    alt_uoms = payload.get("alternate_uoms") or payload.get("units")
    if alt_uoms:
        seen_alt = set()
        first_alt = None
        for idx, row in enumerate(alt_uoms, 1):
            auid = row.get("uom_id") or row.get("unit_id")
            if not auid:
                continue
            auid = int(auid)
            if auid == int(main_uom):
                raise ApiError(f"Alternate UOM (Row {idx}) cannot be the same as Main UOM")
            if auid in seen_alt:
                raise ApiError(f"Duplicate Alternate UOM (Row {idx}) is not allowed")
            seen_alt.add(auid)

            factor = row.get("conversion_factor") or row.get("factor")
            try:
                cf = Decimal(str(factor or 0))
            except Exception:
                raise ApiError(f"Conversion quantity in Alternate UOM row {idx} must be a number")
            if cf <= 0:
                raise ApiError(f"Conversion quantity in Alternate UOM row {idx} must be greater than zero")
            if not first_alt:
                first_alt = (auid, cf)
        if first_alt:
            data["alt_uom_id"] = first_alt[0]
            data["conversion_factor"] = first_alt[1]
    elif "alternate_uoms" in payload or "units" in payload:
        data["alt_uom_id"] = None
        data["conversion_factor"] = None

    # GST Type & Slabs Validation
    gst_type = data.get("gst_type") or payload.get("gst_type") or "Flat"
    data["gst_type"] = gst_type
    if gst_type == "Flat":
        rate = data.get("gst_rate")
        try:
            gr = Decimal(str(rate if rate is not None else 0))
            if gr < 0:
                raise ApiError("GST Percentage cannot be negative", field="gst_rate")
        except Exception:
            raise ApiError("GST Percentage must be a valid number", field="gst_rate")
    elif gst_type == "Slab":
        slabs = payload.get("gst_slabs")
        if not slabs:
            raise ApiError("At least one GST slab is required when GST Type is Slab Rate", field="gst_slabs")
        
        valid_slabs = []
        for idx, s in enumerate(slabs, 1):
            if not any(s.get(k) not in (None, "") for k in ("min_price", "max_price", "gst_rate")):
                continue
            try:
                min_p = Decimal(str(s.get("min_price") or 0))
                if min_p < 0:
                    raise ApiError(f"Slab row {idx}: Minimum amount cannot be negative")
            except Exception:
                raise ApiError(f"Slab row {idx}: Invalid minimum amount")

            max_p = None
            if s.get("max_price") not in (None, "", "No Limit", "null"):
                try:
                    max_p = Decimal(str(s.get("max_price")))
                    if max_p < min_p:
                        raise ApiError(f"Slab row {idx}: Maximum amount ({max_p}) must be greater than or equal to minimum amount ({min_p})")
                except Exception:
                    raise ApiError(f"Slab row {idx}: Invalid maximum amount")

            try:
                sr = Decimal(str(s.get("gst_rate") if s.get("gst_rate") is not None else 0))
                if sr < 0:
                    raise ApiError(f"Slab row {idx}: GST percentage cannot be negative")
            except Exception:
                raise ApiError(f"Slab row {idx}: Invalid GST percentage")

            valid_slabs.append((min_p, max_p, sr, idx))

        if not valid_slabs:
            raise ApiError("At least one valid GST slab is required when GST Type is Slab Rate", field="gst_slabs")

        # Check for overlaps and sort by min_p
        valid_slabs.sort(key=lambda x: x[0])
        for i in range(len(valid_slabs) - 1):
            cur_min, cur_max, _, idx1 = valid_slabs[i]
            next_min, _, _, idx2 = valid_slabs[i + 1]
            if cur_max is None:
                raise ApiError(f"Slab row {idx1} has no maximum amount (open-ended). No subsequent slab can be defined.")
            if cur_max >= next_min:
                raise ApiError(f"Overlapping price ranges between Slab row {idx1} (up to {cur_max}) and Slab row {idx2} (starts at {next_min})")

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

def _generate_bom_no(conn):
    rows = db.all_(conn, "SELECT bom_no FROM m_bom WHERE bom_no LIKE 'BOM-%'")
    nums = []
    for r in rows:
        try:
            nums.append(int(r["bom_no"].split("-")[1]))
        except (IndexError, ValueError):
            pass
    max_id = db.scalar(conn, "SELECT COALESCE(MAX(id), 0) FROM m_bom") or 0
    next_num = max(max(nums, default=0), max_id) + 1
    while db.scalar(conn, "SELECT COUNT(*) FROM m_bom WHERE bom_no=:b", b=f"BOM-{next_num:04d}"):
        next_num += 1
    return f"BOM-{next_num:04d}"


def _bom_before(conn, data, payload, rid):
    # Auto-generate bom_no on create if missing or 'Auto'
    if rid is None:
        bno = (payload.get("bom_no") or "").strip()
        if not bno or bno == "Auto":
            data["bom_no"] = _generate_bom_no(conn)
        else:
            data["bom_no"] = bno

        # BOM Name is mandatory on create
        bname = (payload.get("bom_name") or "").strip()
        if not bname:
            raise ApiError("BOM Name is required", field="bom_name")
        data["bom_name"] = bname
    elif "bom_name" in payload:
        bname = (payload.get("bom_name") or "").strip()
        if not bname:
            raise ApiError("BOM Name cannot be empty", field="bom_name")
        data["bom_name"] = bname

    # 1. Validate batch_qty > 0
    if "batch_qty" in data and data["batch_qty"] is not None:
        try:
            if Decimal(str(data["batch_qty"])) <= 0:
                raise ApiError("Manufacturing quantity must be greater than zero", field="batch_qty")
        except (InvalidOperation, TypeError):
            raise ApiError("Manufacturing quantity must be a valid positive number", field="batch_qty")

    # 2. Validate Finished Good and UOM exist
    if data.get("product_id"):
        if not db.scalar(conn, "SELECT COUNT(*) FROM m_product WHERE id=:i", i=data["product_id"]):
            raise ApiError("Invalid Finished Good product ID", field="product_id")
    if data.get("uom_id"):
        if not db.scalar(conn, "SELECT COUNT(*) FROM m_uom WHERE id=:i", i=data["uom_id"]):
            raise ApiError("Invalid UOM ID", field="uom_id")

    # 3. Validate component items
    raw_items = payload.get("items")
    if rid is None and raw_items is None:
        raise ApiError("At least one component item is required", field="items")

    if raw_items is not None:
        valid_items = [
            r for r in raw_items
            if any(r.get(k) not in (None, "", 0) for k in ("material_id", "qty_per_unit", "uom_id"))
        ]
        if not valid_items:
            raise ApiError("At least one component item is required", field="items")

        for idx, item in enumerate(valid_items, 1):
            mid = item.get("material_id")
            if not mid:
                raise ApiError(f"Component row {idx}: Item is required", field="material_id")
            if not db.scalar(conn, "SELECT COUNT(*) FROM m_product WHERE id=:i", i=mid):
                raise ApiError(f"Component row {idx}: Invalid component Item ID", field="material_id")

            gid = item.get("godown_id")
            if gid and not db.scalar(conn, "SELECT COUNT(*) FROM m_godown WHERE id=:i", i=gid):
                raise ApiError(f"Component row {idx}: Invalid Godown ID", field="godown_id")

            try:
                q = Decimal(str(item.get("qty_per_unit") or 0))
                if q <= 0:
                    raise ApiError(f"Component row {idx}: Component quantity must be greater than zero", field="qty_per_unit")
            except (InvalidOperation, TypeError):
                raise ApiError(f"Component row {idx}: Component quantity must be a valid positive number", field="qty_per_unit")

            uid = item.get("uom_id")
            if not uid and mid:
                uid = db.scalar(conn, "SELECT uom_id FROM m_product WHERE id=:i", i=mid)
                item["uom_id"] = uid
            if not uid:
                raise ApiError(f"Component row {idx}: UOM is required", field="uom_id")
            if not db.scalar(conn, "SELECT COUNT(*) FROM m_uom WHERE id=:i", i=uid):
                raise ApiError(f"Component row {idx}: Invalid UOM ID", field="uom_id")


HOOKS = {
    "product": (_product_before, _product_after),
    "ledger": (_ledger_before, None),
    "txn_type": (None, _txn_type_after),
    "bom": (_bom_before, None),
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
        child_key = ch["key"]
        raw_rows = payload.get(child_key)
        if raw_rows is None and key == "product":
            if child_key == "alternate_uoms" and "units" in payload:
                raw_rows = payload.get("units")
            elif child_key == "units" and "alternate_uoms" in payload:
                raw_rows = payload.get("alternate_uoms")

        if raw_rows is None and child_key not in payload:
            continue
        db.run(conn, f"DELETE FROM `{ch['table']}` WHERE `{ch['fk']}`=:i", i=rid)
        seq = 0
        for row in raw_rows or []:
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
