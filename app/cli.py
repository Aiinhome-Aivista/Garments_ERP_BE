"""flask --app wsgi init-db [--demo]   |   flask --app wsgi create-admin"""
import json
import os
import re
import secrets

import click
from werkzeug.security import generate_password_hash

from . import db
from .masters import save
from .registry import TXN_KINDS

SCHEMA = os.path.join(os.path.dirname(__file__), "..", "sql", "schema.sql")

PREFIX = {"Sales order": "SO/", "Challan": "PL/", "Sales": "SI/", "Purchase Order": "PO/", "GRN": "GRN/", "Purchase": "PI/",
          "Sales Return": "SR/", "Purchase Return": "PR/", "JOB Card generation": "JC/", "Production Plan": "PP/",
          "Production Order": "PRO/", "Material Issue": "MI/", "Material Recd": "MR/", "Receipt": "RC/", "Payment": "PY/",
          "Journal": "JV/", "stock journal": "SJ/", "Requisition": "REQ/"}

STANDARD_DOCS = ["sales_order", "challan", "sales", "sales_return", "purchase_order", "grn", "purchase", "purchase_return"]
MASTER_KEYS = ["generic_type", "generic", "uom", "branch", "godown", "process", "machine", "product_category", "product_group",
               "product", "pricelist", "ledger_group", "ledger_account", "ledger", "txn_type"]


def run_schema(conn):
    sql = re.sub(r"--[^\n]*", "", open(SCHEMA).read())
    for stmt in [s.strip() for s in sql.split(";") if s.strip()]:
        conn.exec_driver_sql(stmt)


def ensure(conn, key, data, lookup="name", **extra):
    """Create a master row through the same validation path as the UI, unless it already exists."""
    from .registry import MASTERS
    m = MASTERS[key]
    row = db.one(conn, f"SELECT id FROM `{m['table']}` WHERE `{lookup}`=:v", v=data[lookup])
    return row["id"] if row else save(conn, key, {**data, **extra})


def seed_base(conn):
    roles = {
        "Admin": {"*": ["*"]},
        "Sales & dispatch": {**{d: ["view", "create", "edit", "delete", "approve"] for d in ("sales_order", "challan", "sales", "sales_return")},
                             "logistics": ["view", "edit"], "stock_report": ["view"], "planning": ["view"]},
        "Stores & purchase": {**{d: ["view", "create", "edit", "delete"] for d in ("purchase_order", "grn", "purchase", "purchase_return")},
                              "requisition": ["view", "create", "edit"], "stock_report": ["view"], "planning": ["view"]},
    }
    for name, perms in roles.items():
        if not db.scalar(conn, "SELECT COUNT(*) FROM app_role WHERE name=:n", n=name):
            db.insert(conn, "app_role", {"name": name, "permissions": json.dumps(perms)})

    branch = ensure(conn, "branch", {"name": "Head office"})
    for t in ["Size", "Parts", "Colour", "Pattern", "Sleeves", "Collection"]:
        ensure(conn, "generic_type", {"name": t})
    for name, dec in [("Pcs", 0), ("Mtr", 2), ("Kg", 3), ("Box", 0)]:
        ensure(conn, "uom", {"name": name, "decimals": dec})
    for name in ["Cutting", "Stitching", "Finishing", "Packing"]:
        ensure(conn, "process", {"name": name})
    ensure(conn, "godown", {"name": "Main store", "branch_id": branch})
    ensure(conn, "godown", {"name": "Finished goods", "branch_id": branch})

    size = db.scalar(conn, "SELECT id FROM m_generic_type WHERE name='Size'")
    colour = db.scalar(conn, "SELECT id FROM m_generic_type WHERE name='Colour'")
    ensure(conn, "product_category", {"name": "FG", "attributes": [
        {"label": "Size", "mode": "List", "generic_type_id": size}, {"label": "Colour", "mode": "List", "generic_type_id": colour}]})
    ensure(conn, "product_category", {"name": "RM", "attributes": [{"label": "Colour", "mode": "List", "generic_type_id": colour}]})
    ensure(conn, "product_category", {"name": "Consumables", "attributes": []})

    for n, cat in [("Sundry debtors", "Asset"), ("Sundry creditors", "Liability"), ("Sales accounts", "Income"),
                   ("Purchase accounts", "Expense"), ("Duties & taxes", "Liability")]:
        ensure(conn, "ledger_group", {"name": n, "category": cat})
    for n in ["Sundry debtors", "Sundry creditors", "Sales", "Purchase", "Duties & taxes", "Freight & other"]:
        ensure(conn, "ledger_account", {"name": n})
    grp = lambda n: db.scalar(conn, "SELECT id FROM m_ledger_group WHERE name=:n", n=n)
    acc = lambda n: db.scalar(conn, "SELECT id FROM m_ledger_account WHERE name=:n", n=n)
    for name, typ, g, a, tax in [("Sales", "Sales", "Sales accounts", "Sales", None), ("Purchase", "Purchase", "Purchase accounts", "Purchase", None),
                                 ("CGST", "General", "Duties & taxes", "Duties & taxes", "CGST"), ("SGST", "General", "Duties & taxes", "Duties & taxes", "SGST"),
                                 ("IGST", "General", "Duties & taxes", "Duties & taxes", "IGST"), ("Freight", "General", "Sales accounts", "Freight & other", None),
                                 ("Trade discount", "Discount", "Sales accounts", "Freight & other", None)]:
        ensure(conn, "ledger", {"name": name, "ledger_type": typ, "ledger_group_id": grp(g), "ledger_account_id": acc(a), "tax_type": tax})

    for kind in TXN_KINDS:
        ensure(conn, "txn_type", {"name": kind[0].upper() + kind[1:], "txn_kind": kind, "branch_id": branch,
                                  "prefix": PREFIX[kind], "start_number": 1})


def seed_demo(conn):
    """Small sample data so every screen has something to show. Safe to skip in production."""
    tid = lambda n: db.scalar(conn, "SELECT id FROM m_generic_type WHERE name=:n", n=n)
    for s in ["S", "M", "L", "XL"]:
        if not db.scalar(conn, "SELECT COUNT(*) FROM m_generic WHERE name=:n AND type_id=:t", n=s, t=tid("Size")):
            save(conn, "generic", {"name": s, "type_id": tid("Size")})
    for s in ["Navy", "White", "Red"]:
        if not db.scalar(conn, "SELECT COUNT(*) FROM m_generic WHERE name=:n AND type_id=:t", n=s, t=tid("Colour")):
            save(conn, "generic", {"name": s, "type_id": tid("Colour")})
    third = ensure(conn, "product_group", {"name": "Third party"})
    own = ensure(conn, "product_group", {"name": "Own production"})
    bh = ensure(conn, "product_group", {"name": "Baby Hug", "parent_id": third})
    bhk = ensure(conn, "product_group", {"name": "BHKIDS", "parent_id": bh})
    ensure(conn, "product_group", {"name": "BHGIRLS", "parent_id": bh})
    ensure(conn, "product_group", {"name": "BHKIDSSUMMER", "parent_id": bhk})
    ensure(conn, "product_group", {"name": "Fabrics", "parent_id": own})

    grp = lambda n: db.scalar(conn, "SELECT id FROM m_ledger_group WHERE name=:n", n=n)
    acc = lambda n: db.scalar(conn, "SELECT id FROM m_ledger_account WHERE name=:n", n=n)
    sm = ensure(conn, "ledger", {"name": "Ravi Kumar", "ledger_type": "Salesman"})
    bk = ensure(conn, "ledger", {"name": "Sharma Brokers", "ledger_type": "Broker"})
    tr = ensure(conn, "ledger", {"name": "Speedy Roadlines", "ledger_type": "Transporter"})
    ensure(conn, "ledger", {"name": "Fabric Mart Pvt Ltd", "ledger_type": "Supplier", "ledger_group_id": grp("Sundry creditors"),
                            "ledger_account_id": acc("Sundry creditors"), "city": "Surat", "state": "Gujarat"})
    ensure(conn, "pricelist", {"name": "Retail 2026", "items": []})
    cust = ensure(conn, "ledger", {"name": "Little Steps Retail", "ledger_type": "Customer", "ledger_group_id": grp("Sundry debtors"),
                                   "ledger_account_id": acc("Sundry debtors"), "city": "Kolkata", "state": "West Bengal",
                                   "salesman_id": sm, "broker_id": bk, "transporter_id": tr, "cash_discount": 2})

    cat = lambda n: db.scalar(conn, "SELECT id FROM m_product_category WHERE name=:n", n=n)
    uom = lambda n: db.scalar(conn, "SELECT id FROM m_uom WHERE name=:n", n=n)
    gid = lambda t, n: db.scalar(conn, "SELECT id FROM m_generic WHERE name=:n AND type_id=:t", n=n, t=tid(t))
    if not db.scalar(conn, "SELECT COUNT(*) FROM m_product WHERE name='Cotton jersey fabric'"):
        fabric = save(conn, "product", {"name": "Cotton jersey fabric", "category_id": cat("RM"), "uom_id": uom("Mtr"), "group_id": grp_id(conn, "Fabrics"),
                                        "attr_values": {"1": "White"}, "gst_rate": 5, "min_stock": 200})
        thread = save(conn, "product", {"name": "Sewing thread", "category_id": cat("Consumables"), "uom_id": uom("Kg"), "gst_rate": 12, "attr_values": {}})
        tee = save(conn, "product", {"name": "Baby Hug kids tee", "category_id": cat("FG"), "uom_id": uom("Pcs"), "group_id": bhk,
                                     "attr_values": {"1": "M", "2": "Navy"}, "gst_rate": 5, "hsn_code": "6109",
                                     "customers": [{"customer_id": cust, "customer_item_name": "LS-TEE-M"}],
                                     "processes": [
                                         {"process_id": db.scalar(conn, "SELECT id FROM m_process WHERE name='Cutting'"), "material_id": fabric, "part_name": "Body", "qty": 0.45, "out_status": "WIP"},
                                         {"process_id": db.scalar(conn, "SELECT id FROM m_process WHERE name='Stitching'"), "material_id": thread, "part_name": "Seams", "qty": 0.01, "process_rate": 18, "out_status": "WIP", "overhead_pct": 5},
                                         {"process_id": db.scalar(conn, "SELECT id FROM m_process WHERE name='Packing'"), "out_status": "FP"}]})
        pl = db.scalar(conn, "SELECT id FROM m_pricelist WHERE name='Retail 2026'")
        save(conn, "pricelist", {"name": "Retail 2026", "items": [{"product_id": tee, "rate": 320, "disc_pct": 0, "disc_amt": 0}]}, pl)
        save(conn, "ledger", {"name": "Little Steps Retail", "ledger_type": "Customer", "ledger_group_id": grp("Sundry debtors"),
                              "ledger_account_id": acc("Sundry debtors"), "city": "Kolkata", "state": "West Bengal", "salesman_id": sm,
                              "broker_id": bk, "transporter_id": tr, "cash_discount": 2,
                              "discounts": [{"product_group_id": bh, "disc_pct": 5, "pricelist_id": pl}]}, cust)


def grp_id(conn, name):
    return db.scalar(conn, "SELECT id FROM m_product_group WHERE name=:n", n=name)


def register_cli(app):
    @app.cli.command("init-db")
    @click.option("--demo", is_flag=True, help="Also load a small demo data set")
    def init_db(demo):
        """Create tables, base masters, roles and the admin user."""
        with db.tx() as conn:
            run_schema(conn)
            seed_base(conn)
            if demo:
                seed_demo(conn)
            role = db.scalar(conn, "SELECT id FROM app_role WHERE name='Admin'")
            if not db.scalar(conn, "SELECT COUNT(*) FROM app_user WHERE username='admin'"):
                pw = os.getenv("ADMIN_PASSWORD") or secrets.token_urlsafe(9)
                db.insert(conn, "app_user", {"username": "admin", "full_name": "Administrator",
                                             "password_hash": pw, "role_id": role})
                click.echo(f"Created user 'admin' with password: {pw}   (change it after first sign-in)")
        click.echo("Database ready.")

    @app.cli.command("reset-admin-password")
    @click.argument("password")
    def reset_pw(password):
        with db.tx() as conn:
            db.run(conn, "UPDATE app_user SET password_hash=:h WHERE username='admin'", h=password)
        click.echo("Admin password updated.")
