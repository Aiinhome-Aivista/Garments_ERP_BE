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

        # Load nested materials for each process
        if "processes" in row:
            for pr in row["processes"]:
                if not pr.get("components_of_id"):
                    pr["components_of_id"] = rid
                    pr["components_of_id__label"] = row.get("item_name") or row.get("name")
                elif not pr.get("components_of_id__label"):
                    pr["components_of_id__label"] = db.scalar(conn, "SELECT item_name FROM m_product WHERE id=:i", i=pr["components_of_id"]) or row.get("item_name")
                pp_id = pr.get("id")
                mats = []
                if pp_id:
                    mats = db.all_(conn, """
                        SELECT ppm.id, ppm.product_process_id, ppm.material_id, p.item_name AS `material_id__label`,
                               ppm.godown_id, g.name AS `godown_id__label`,
                               COALESCE(ppm.item_type, '') AS item_type,
                               ppm.qty_per_unit, ppm.uom_id, u.name AS `uom_id__label`,
                               ppm.rate, ppm.rate_pct, ppm.seq
                        FROM m_product_process_material ppm
                        JOIN m_product p ON p.id = ppm.material_id
                        LEFT JOIN m_godown g ON g.id = ppm.godown_id
                        LEFT JOIN m_product_category c ON c.id = p.category_id
                        JOIN m_uom u ON u.id = ppm.uom_id
                        WHERE ppm.product_process_id = :pp
                        ORDER BY ppm.seq, ppm.id
                    """, pp=pp_id)
                    # Legacy fallback if no rows in m_product_process_material but material_id exists on m_product_process
                    if not mats and pr.get("material_id") and float(pr.get("qty") or 0) > 0:
                        mid = pr["material_id"]
                        mat_info = db.one(conn, """
                            SELECT p.item_name, COALESCE(c.name, 'Raw Material') AS cat_name, p.uom_id, u.name AS uom_name
                            FROM m_product p
                            LEFT JOIN m_product_category c ON c.id = p.category_id
                            JOIN m_uom u ON u.id = p.uom_id
                            WHERE p.id = :i
                        """, i=mid)
                        if mat_info:
                            mats = [{
                                "material_id": mid,
                                "material_id__label": mat_info["item_name"],
                                "item_type": mat_info["cat_name"],
                                "qty_per_unit": float(pr["qty"]),
                                "uom_id": mat_info["uom_id"],
                                "uom_id__label": mat_info["uom_name"],
                                "seq": 1
                            }]
                for m_row in mats:
                    mid = m_row["material_id"]
                    uoms = db.all_(conn, """
                        SELECT u.id AS uom_id, u.name, 1.0 AS conversion_factor, 1 AS is_main
                        FROM m_product p JOIN m_uom u ON u.id=p.uom_id WHERE p.id=:p
                        UNION ALL
                        SELECT u.id AS uom_id, u.name, pu.conversion_factor, 0 AS is_main
                        FROM m_product_uom pu JOIN m_uom u ON u.id=pu.uom_id WHERE pu.product_id=:p AND u.active=1
                    """, p=mid)
                    m_row["available_uoms"] = uoms
                pr["materials"] = mats
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
        if t in ("bool", "toggle"):
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
            elif t in ("select", "radio"):
                if f.get("options") and v not in f["options"]:
                    raise ApiError(f"{f['label']}: '{v}' is not an option", field=n)
            elif t == "attrs":
                v = json.dumps(v if isinstance(v, dict) else {})
        elif t in ("select", "radio") and "" in (f.get("options") or []):
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
    ptype = data.get("product_type") or payload.get("product_type") or "Finished"
    name = (data.get("name") or "").strip()

    cat = data.get("category_id")
    attrs = db.all_(conn, "SELECT attr_no,label FROM m_product_category_attr WHERE category_id=:c ORDER BY attr_no", c=cat) if cat else []
    vals = json.loads(data.get("attr_values") or "{}")
    clean = {}
    attr_parts = []
    for a in attrs:
        v = str(vals.get(str(a["attr_no"])) or "").strip()
        if not v:
            raise ApiError(f"{a['label']} is required for this category", field="attr_values")
        clean[str(a["attr_no"])] = v
        attr_parts.append(v)
    data["attr_values"] = json.dumps(clean)

    if attr_parts:
        parts = attr_parts
    else:
        parts = []

    if parts:
        data["item_name"] = "-".join(parts)
    else:
        data["item_name"] = (payload.get("item_name") or "").strip()

    if not data.get("item_name"):
        raise ApiError("Item Name (sku) is required", field="item_name")

    if ptype == "Others":
        if not data.get("name"):
            data["name"] = name
    else:
        if not data.get("name") or attr_parts:
            data["name"] = data["item_name"]

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

    # Process Definition with BOM Validation
    if "processes" in payload:
        processes = payload.get("processes") or []
        for p_idx, proc in enumerate(processes, 1):
            pid = proc.get("process_id")
            if not pid:
                raise ApiError(f"Process {p_idx}: Process selection is required", field="processes")
            
            seen_mats = set()
            mats = proc.get("materials") or []
            for m_idx, mat in enumerate(mats, 1):
                mid = mat.get("material_id")
                if not mid:
                    raise ApiError(f"Process {p_idx}, Material {m_idx}: Item selection is required")
                if mid in seen_mats:
                    mat_name = mat.get("material_id__label") or f"Item #{mid}"
                    raise ApiError(f"Process {p_idx}: Duplicate item '{mat_name}' cannot be added multiple times within the same process")
                seen_mats.add(mid)
                
                try:
                    q = Decimal(str(mat.get("qty_per_unit") or 0))
                    if q <= 0:
                        raise ApiError(f"Process {p_idx}, Material {m_idx}: Quantity must be greater than zero")
                except Exception:
                    raise ApiError(f"Process {p_idx}, Material {m_idx}: Quantity must be a valid number")
                
                if not mat.get("uom_id"):
                    raise ApiError(f"Process {p_idx}, Material {m_idx}: UOM is required")

def _product_after(conn, rid, created):
    if created:
        db.run(conn, "UPDATE m_product SET barcode=:b WHERE id=:i", b=_ean13(f"200{rid:09d}"), i=rid)

def _ledger_before(conn, data, payload, rid):
    gst_reg = data.get("gst_registration_type")
    from app.registry import GST_REGISTRATION_TYPES
    if gst_reg and gst_reg not in GST_REGISTRATION_TYPES:
        raise ApiError(f"GST Registration Type must be one of: {', '.join(GST_REGISTRATION_TYPES)}", field="gst_registration_type")
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
            
            # For processes, handle components_of_id and maintain legacy material_id & qty
            if child_key == "processes":
                comp_id = row.get("components_of_id")
                if comp_id:
                    cd["components_of_id"] = int(comp_id)
                elif not cd.get("components_of_id"):
                    cd["components_of_id"] = rid

                if row.get("materials"):
                    first_mat = row["materials"][0]
                    if first_mat.get("material_id"):
                        cd["material_id"] = int(first_mat["material_id"])
                        cd["qty"] = Decimal(str(first_mat.get("qty_per_unit") or 0))

            pp_id = db.insert(conn, ch["table"], cd)

            # Insert nested materials into relational table m_product_process_material
            if child_key == "processes" and "materials" in row:
                m_seq = 0
                for mat in row.get("materials") or []:
                    mid = mat.get("material_id")
                    if not mid:
                        continue
                    m_seq += 1
                    uid = mat.get("uom_id")
                    if not uid:
                        uid = db.scalar(conn, "SELECT uom_id FROM m_product WHERE id=:i", i=mid)
                    cat_name = mat.get("item_type") if mat.get("item_type") is not None else ""
                    gid = mat.get("godown_id")
                    rate_val = Decimal(str(mat.get("rate") or 0))
                    rate_pct_val = Decimal(str(mat.get("rate_pct") or 0))

                    db.insert(conn, "m_product_process_material", {
                        "product_process_id": pp_id,
                        "material_id": int(mid),
                        "godown_id": int(gid) if gid else None,
                        "item_type": cat_name,
                        "qty_per_unit": Decimal(str(mat.get("qty_per_unit") or 0)),
                        "uom_id": int(uid),
                        "rate": rate_val,
                        "rate_pct": rate_pct_val,
                        "seq": m_seq
                    })
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


@login_required
def get_eligible_materials():
    """Return all active items eligible for BOM with category name, main UOM, and alternate UOMs."""
    with db.tx() as conn:
        rows = db.all_(conn, """
            SELECT p.id, p.name, p.item_name, p.category_id,
                   COALESCE(c.name, 'Raw Material') AS category_name,
                   p.uom_id, u.name AS uom_name
            FROM m_product p
            LEFT JOIN m_product_category c ON c.id = p.category_id
            JOIN m_uom u ON u.id = p.uom_id
            WHERE p.active = 1
            ORDER BY p.item_name
        """)
        p_ids = [r["id"] for r in rows]
        uoms_map = {}
        if p_ids:
            alt_rows = db.all_(conn, f"""
                SELECT pu.product_id, pu.uom_id, u.name, pu.conversion_factor
                FROM m_product_uom pu
                JOIN m_uom u ON u.id = pu.uom_id
                WHERE pu.product_id IN ({','.join(map(str, p_ids))}) AND u.active = 1
                ORDER BY u.name
            """)
            for ar in alt_rows:
                uoms_map.setdefault(ar["product_id"], []).append({
                    "uom_id": ar["uom_id"],
                    "name": ar["name"],
                    "conversion_factor": float(ar["conversion_factor"]),
                    "is_main": False
                })

        for r in rows:
            alts = uoms_map.get(r["id"], [])
            main_uom = {
                "uom_id": r["uom_id"],
                "name": r["uom_name"],
                "conversion_factor": 1.0,
                "is_main": True
            }
            r["available_uoms"] = [main_uom] + alts
            r["label"] = r["item_name"]
            r["item_type"] = r["category_name"]
        return jsonify(rows)


@login_required
def get_godowns():
    """Return all active godowns for BOM material storage selection."""
    with db.tx() as conn:
        rows = db.all_(conn, "SELECT id, name FROM m_godown WHERE active = 1 ORDER BY (name LIKE '%Main%') DESC, name ASC")
        return jsonify(rows)


@login_required
def get_uoms():
    """Return all active UOMs for process/BOM selection."""
    with db.tx() as conn:
        rows = db.all_(conn, "SELECT id, name FROM m_uom WHERE active = 1 ORDER BY name ASC")
        return jsonify(rows)


@login_required
def get_product_multi_definition(pid):
    """Fetch base product's complete definitions: details, category attributes with master options,
    alternate UOMs, GST slabs, Process definitions with nested materials, and standalone BOM if any."""
    with db.tx() as conn:
        p = db.one(conn, """
            SELECT p.*,
                   c.name as category_name,
                   g.name as group_name,
                   u.name as uom_name,
                   sl.name as default_sales_ledger_name,
                   pl.name as default_purchase_ledger_name
            FROM m_product p
            LEFT JOIN m_product_category c ON c.id = p.category_id
            LEFT JOIN m_product_group g ON g.id = p.group_id
            LEFT JOIN m_uom u ON u.id = p.uom_id
            LEFT JOIN m_ledger sl ON sl.id = p.default_sales_ledger_id
            LEFT JOIN m_ledger pl ON pl.id = p.default_purchase_ledger_id
            WHERE p.id = :id
        """, id=pid)
        if not p:
            raise ApiError("Base product not found", status=404)

        # Parse attr_values if string
        if isinstance(p.get("attr_values"), str):
            try:
                p["attr_values"] = json.loads(p["attr_values"])
            except Exception:
                p["attr_values"] = {}
        elif not p.get("attr_values"):
            p["attr_values"] = {}

        # Fetch Category Attributes and options
        cat_attrs = []
        if p.get("category_id"):
            attrs = db.all_(conn, """
                SELECT attr_no, label, mode, generic_type_id
                FROM m_product_category_attr
                WHERE category_id = :c
                ORDER BY attr_no
            """, c=p["category_id"])
            for a in attrs:
                opts = []
                t_id = a.get("generic_type_id")
                if a.get("mode") == "List":
                    if not t_id and a.get("label"):
                        gt = db.one(conn, "SELECT id FROM m_generic_type WHERE LOWER(TRIM(name)) = LOWER(TRIM(:lbl))", lbl=a.get("label", ""))
                        if gt:
                            t_id = gt["id"]
                    if t_id:
                        opts = db.all_(conn, """
                            SELECT id, name
                            FROM m_generic
                            WHERE type_id = :t AND active = 1
                            ORDER BY name
                        """, t=t_id)
                cat_attrs.append({
                    "attr_no": a["attr_no"],
                    "label": a["label"],
                    "mode": a["mode"],
                    "generic_type_id": t_id,
                    "options": opts,
                    "current_value": p["attr_values"].get(str(a["attr_no"]))
                })

        # Alternate UOMs
        alt_uoms = db.all_(conn, """
            SELECT u.id, u.uom_id, um.name as uom_name, u.conversion_factor
            FROM m_product_uom u
            JOIN m_uom um ON um.id = u.uom_id
            WHERE u.product_id = :pid
        """, pid=pid)

        # GST Slabs
        gst_slabs = db.all_(conn, """
            SELECT id, min_price, max_price, gst_rate
            FROM m_product_gst_slab
            WHERE product_id = :pid
            ORDER BY min_price
        """, pid=pid)

        # Process Definitions with materials
        procs = db.all_(conn, """
            SELECT pp.id, pp.process_id, pr.name as process_name, pp.seq,
                   pp.uom_id, u.name as uom_name, pp.qty, pp.qty as unit_qty,
                   pp.process_rate, pp.overhead_pct, pp.out_status
            FROM m_product_process pp
            JOIN m_process pr ON pr.id = pp.process_id
            LEFT JOIN m_uom u ON u.id = pp.uom_id
            WHERE pp.product_id = :pid
            ORDER BY pp.seq, pp.id
        """, pid=pid)

        for pr in procs:
            pr["process_id__label"] = pr.get("process_name") or f"Process #{pr.get('process_id')}"
            mats = db.all_(conn, """
                SELECT ppm.id, ppm.material_id, p.item_name as material_name, p.item_name as material_id__label,
                       ppm.godown_id, g.name as godown_name, g.name as godown_id__label, ppm.item_type,
                       ppm.qty_per_unit, ppm.uom_id, u.name as uom_name, u.name as uom_id__label,
                       ppm.rate, ppm.rate_pct, ppm.seq,
                       ROUND(ppm.qty_per_unit * COALESCE(ppm.rate, 0), 2) as amount
                FROM m_product_process_material ppm
                JOIN m_product p ON p.id = ppm.material_id
                LEFT JOIN m_godown g ON g.id = ppm.godown_id
                JOIN m_uom u ON u.id = ppm.uom_id
                WHERE ppm.product_process_id = :pp
                ORDER BY ppm.seq, ppm.id
            """, pp=pr["id"])
            pr["materials"] = mats

        # Standalone BOM details if any
        bom = db.one(conn, """
            SELECT b.id, b.bom_no, b.bom_name, b.product_id, b.batch_qty, b.uom_id, u.name as uom_name, b.remarks
            FROM m_bom b
            LEFT JOIN m_uom u ON u.id = b.uom_id
            WHERE b.product_id = :pid
            ORDER BY b.id DESC LIMIT 1
        """, pid=pid)
        if bom:
            bom_items = db.all_(conn, """
                SELECT bi.id, bi.material_id, p.item_name as material_name,
                       bi.godown_id, g.name as godown_name, bi.item_type,
                       bi.qty_per_unit, bi.rate, bi.wastage_pct, bi.remarks,
                       p.uom_id, u.name as uom_name,
                       ROUND(bi.qty_per_unit * COALESCE(bi.rate, 0), 2) as amount
                FROM m_bom_item bi
                JOIN m_product p ON p.id = bi.material_id
                LEFT JOIN m_godown g ON g.id = bi.godown_id
                LEFT JOIN m_uom u ON u.id = p.uom_id
                WHERE bi.bom_id = :bid
                ORDER BY bi.seq, bi.id
            """, bid=bom["id"])
            bom["items"] = bom_items

        return jsonify({
            "product": p,
            "category_attributes": cat_attrs,
            "alternate_uoms": alt_uoms,
            "gst_slabs": gst_slabs,
            "processes": procs,
            "bom": bom
        })


@login_required
def bulk_create_products_from_existing():
    """Bulk create multiple product combinations from an existing product in a single transaction."""
    payload = request.get_json(force=True) or {}
    base_product_id = payload.get("base_product_id")
    common_fields = payload.get("common_fields") or {}
    items = payload.get("items") or []
    alternate_uoms = payload.get("alternate_uoms") or []
    gst_slabs = payload.get("gst_slabs") or []
    processes = payload.get("processes") or []
    bom = payload.get("bom")

    if not base_product_id:
        raise ApiError("Base product ID is required", field="base_product_id")
    if not items:
        raise ApiError("At least one product combination must be provided", field="items")

    items_to_create = [it for it in items if not it.get("is_base")]
    if not items_to_create:
        raise ApiError("At least one new product line must be provided", field="items")

    # Clean & validate item names
    item_names = []
    for idx, it in enumerate(items_to_create, 1):
        name = (it.get("item_name") or "").strip()
        if not name:
            raise ApiError(f"Combination row {idx}: Item Name (SKU) cannot be empty")
        item_names.append(name)

    # Check for duplicates within the request
    if len(item_names) != len(set(item_names)):
        seen = set()
        dupes = set()
        for name in item_names:
            if name in seen:
                dupes.add(name)
            seen.add(name)
        raise ApiError(f"Duplicate SKUs in generated list: {', '.join(dupes)}")

    with db.tx() as conn:
        # Check base product exists
        base_prod = db.one(conn, "SELECT id, item_name FROM m_product WHERE id=:id", id=base_product_id)
        if not base_prod:
            raise ApiError("Selected base product does not exist")

        # Check for existing duplicate SKUs in database
        existing_dupes = []
        for name in item_names:
            if db.scalar(conn, "SELECT COUNT(*) FROM m_product WHERE item_name = :n", n=name):
                existing_dupes.append(name)
        if existing_dupes:
            raise ApiError(f"The following SKUs already exist in the database: {', '.join(existing_dupes[:5])}{'...' if len(existing_dupes) > 5 else ''}")

        created_products = []

        for it in items_to_create:
            p_type = it.get("product_type") or common_fields.get("product_type") or "Finished"
            prod_name = (it.get("name") or common_fields.get("name") or it.get("item_name") or "").strip()
            item_name = it["item_name"].strip()
            attr_values = it.get("attr_values") or {}
            attr_json = json.dumps(attr_values)

            # Insert into m_product
            p_data = {
                "product_type": p_type,
                "name": prod_name if prod_name else item_name,
                "item_name": item_name,
                "category_id": common_fields.get("category_id"),
                "group_id": common_fields.get("group_id"),
                "uom_id": common_fields.get("uom_id"),
                "default_sales_ledger_id": common_fields.get("default_sales_ledger_id"),
                "default_purchase_ledger_id": common_fields.get("default_purchase_ledger_id"),
                "gst_type": common_fields.get("gst_type") or "Flat",
                "gst_rate": Decimal(str(common_fields.get("gst_rate") or 0)),
                "hsn_code": common_fields.get("hsn_code"),
                "package_qty": common_fields.get("package_qty"),
                "min_stock": common_fields.get("min_stock"),
                "enable_batch": 1 if common_fields.get("enable_batch") else 0,
                "active": 1,
                "attr_values": attr_json
            }
            new_pid = db.insert(conn, "m_product", p_data)

            # Barcode generation
            barcode = _ean13(f"200{new_pid:09d}")
            db.run(conn, "UPDATE m_product SET barcode=:b WHERE id=:i", b=barcode, i=new_pid)

            # Clone Alternate UOMs
            for au in alternate_uoms:
                auid = au.get("uom_id")
                cf = au.get("conversion_factor")
                if auid and cf:
                    db.insert(conn, "m_product_uom", {
                        "product_id": new_pid,
                        "uom_id": int(auid),
                        "conversion_factor": Decimal(str(cf))
                    })

            # Clone GST Slabs
            if p_data["gst_type"] == "Slab":
                for sl in gst_slabs:
                    min_p = sl.get("min_price")
                    max_p = sl.get("max_price")
                    gr = sl.get("gst_rate")
                    if min_p is not None and gr is not None:
                        db.insert(conn, "m_product_gst_slab", {
                            "product_id": new_pid,
                            "min_price": Decimal(str(min_p)),
                            "max_price": Decimal(str(max_p)) if max_p not in (None, "", "null") else None,
                            "gst_rate": Decimal(str(gr))
                        })

            # Clone Processes & Process Materials (use item's specific processes if provided, else fallback to common processes)
            item_procs = it.get("processes") if it.get("processes") is not None else processes
            for p_idx, pr in enumerate(item_procs, 1):
                proc_id = pr.get("process_id")
                if not proc_id:
                    continue
                pp_id = db.insert(conn, "m_product_process", {
                    "product_id": new_pid,
                    "process_id": int(proc_id),
                    "components_of_id": new_pid,
                    "seq": int(pr.get("seq") or p_idx),
                    "uom_id": pr.get("uom_id"),
                    "qty": Decimal(str(pr.get("qty") or pr.get("unit_qty") or 1)),
                    "process_rate": Decimal(str(pr.get("process_rate") or 0)),
                    "overhead_pct": Decimal(str(pr.get("overhead_pct") or 0)),
                    "out_status": pr.get("out_status") or "WIP"
                })

                mats = pr.get("materials") or []
                for m_idx, mat in enumerate(mats, 1):
                    mid = mat.get("material_id")
                    if not mid:
                        continue
                    db.insert(conn, "m_product_process_material", {
                        "product_process_id": pp_id,
                        "material_id": int(mid),
                        "godown_id": int(mat["godown_id"]) if mat.get("godown_id") else None,
                        "item_type": mat.get("item_type") or "Raw Material",
                        "qty_per_unit": Decimal(str(mat.get("qty_per_unit") or 0)),
                        "uom_id": int(mat.get("uom_id") or p_data["uom_id"]),
                        "rate": Decimal(str(mat.get("rate") or 0)),
                        "rate_pct": Decimal(str(mat.get("rate_pct") or 0)),
                        "seq": m_idx
                    })

            # Clone Standalone BOM if provided, or auto-generate m_bom from processes materials
            all_process_mats = []
            for pr in item_procs:
                for m in (pr.get("materials") or []):
                    if m.get("material_id"):
                        all_process_mats.append(m)

            if bom and bom.get("items"):
                bitems = bom.get("items") or []
                valid_bitems = [bi for bi in bitems if bi.get("material_id")]
                if valid_bitems:
                    new_bno = _generate_bom_no(conn)
                    bname = f"{item_name} BOM"
                    bid = db.insert(conn, "m_bom", {
                        "bom_no": new_bno,
                        "bom_name": bname,
                        "product_id": new_pid,
                        "batch_qty": Decimal(str(bom.get("batch_qty") or 1)),
                        "uom_id": int(bom.get("uom_id") or p_data["uom_id"]),
                        "remarks": bom.get("remarks") or ""
                    })
                    for bi_idx, bi in enumerate(valid_bitems, 1):
                        db.insert(conn, "m_bom_item", {
                            "bom_id": bid,
                            "material_id": int(bi["material_id"]),
                            "godown_id": int(bi["godown_id"]) if bi.get("godown_id") else None,
                            "item_type": bi.get("item_type") or "Raw Material",
                            "qty_per_unit": Decimal(str(bi.get("qty_per_unit") or 0)),
                            "uom_id": int(bi.get("uom_id") or p_data["uom_id"]),
                            "rate": Decimal(str(bi.get("rate") or 0)),
                            "wastage_pct": Decimal(str(bi.get("wastage_pct") or 0)),
                            "seq": bi_idx,
                            "remarks": bi.get("remarks") or ""
                        })
            elif all_process_mats:
                new_bno = _generate_bom_no(conn)
                bname = f"{item_name} BOM"
                bid = db.insert(conn, "m_bom", {
                    "bom_no": new_bno,
                    "bom_name": bname,
                    "product_id": new_pid,
                    "batch_qty": Decimal("1"),
                    "uom_id": int(p_data["uom_id"]),
                    "remarks": "Auto-created from processes BOM"
                })
                for bi_idx, m in enumerate(all_process_mats, 1):
                    db.insert(conn, "m_bom_item", {
                        "bom_id": bid,
                        "material_id": int(m["material_id"]),
                        "godown_id": int(m["godown_id"]) if m.get("godown_id") else None,
                        "item_type": m.get("item_type") or "Raw Material",
                        "qty_per_unit": Decimal(str(m.get("qty_per_unit") or 0)),
                        "uom_id": int(m.get("uom_id") or p_data["uom_id"]),
                        "rate": Decimal(str(m.get("rate") or 0)),
                        "wastage_pct": Decimal(str(m.get("rate_pct") or 0)),
                        "seq": bi_idx,
                        "remarks": ""
                    })

            created_products.append({
                "id": new_pid,
                "item_name": item_name,
                "barcode": barcode
            })

        return jsonify({
            "success": True,
            "count": len(created_products),
            "products": created_products
        })



