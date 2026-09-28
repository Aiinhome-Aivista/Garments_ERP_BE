"""
Master registry.  ONE dict describes every master: its table, fields, child grids, hierarchy.
The generic CRUD API (masters.py) and the React forms (via /api/meta) are both driven from this,
so when the next set of metadata arrives you add an entry here and the screen + API exist.

Field types: text, textarea, int, decimal, date, bool, select, ref, attrs (product attribute panel)
Field keys : name, label, type, required, options, ref, filter, show_if, default, list, readonly, help
"""

LEDGER_TYPES = ["General", "Customer", "Supplier", "Broker", "Salesman", "Jobworker",
                "Transporter", "Purchase", "Sales", "Discount"]
TAX_TYPES = ["CGST", "SGST", "IGST", "TDS", "TCS"]

# Predefined "Type of transaction" list from the metadata document (+ 'Requisition', added because the
# Requisition for PO needs its own number series).
TXN_KINDS = ["Sales order", "Challan", "Sales", "Purchase Order", "GRN", "Purchase", "Sales Return",
             "Purchase Return", "JOB Card generation", "Production Plan", "Production Order",
             "Material Issue", "Material Recd", "Receipt", "Payment", "Journal", "stock journal",
             "Requisition"]

CUSTOMER_ONLY = {"ledger_type": ["Customer"]}


def T(name, label, **kw):
    return dict(name=name, label=label, type="text", **kw)


MASTERS = {
    # ---------------------------------------------------------------- basics
    "generic_type": dict(
        table="m_generic_type", label="Generic master types", hidden=True, quick_add=True, group="Basics",
        icon="button", fields=[T("name", "Type name", required=True, list=True)]),
    "generic": dict(
        table="m_generic", label="Generic masters", group="Basics", icon="button", quick_add=True,
        help="Sizes, colours, parts, patterns, sleeves, collections. Pick a type, or type a new one to add it.",
        fields=[
            dict(name="type_id", label="Type", type="ref", ref="generic_type", required=True, list=True, quick_add=True),
            T("name", "Name", required=True, list=True),
        ]),
    "uom": dict(
        table="m_uom", label="Units of measure", group="Basics", icon="tape", quick_add=True,
        fields=[T("name", "UOM name", required=True, list=True),
                dict(name="decimals", label="Decimals", type="int", default=0, list=True)]),
    "branch": dict(
        table="m_branch", label="Branches", group="Basics", icon="factory",
        fields=[T("name", "Branch name", required=True, list=True),
                T("address", "Address"), T("gstin", "GSTIN", list=True), T("state", "State", list=True),
                T("pincode", "Pincode"), T("contact_person", "Contact person"),
                T("mobile", "Mobile", list=True), T("email", "Email id")]),
    "godown": dict(
        table="m_godown", label="Godowns / storage", group="Basics", icon="rack", hierarchical=True,
        fields=[T("name", "Name", required=True, list=True),
                dict(name="branch_id", label="Branch", type="ref", ref="branch", list=True)]),
    "process": dict(
        table="m_process", label="Processes", group="Production", icon="needle", quick_add=True,
        fields=[T("name", "Process name", required=True, list=True)]),
    "machine": dict(
        table="m_machine", label="Machines", group="Production", icon="machine",
        fields=[T("name", "Machine name", required=True, list=True)]),

    # ---------------------------------------------------------------- products
    "product_category": dict(
        table="m_product_category", label="Product categories", group="Products", icon="hanger",
        help="e.g. FG, RM, Consumables. Define up to four attributes; List attributes pull from a generic master type.",
        fields=[T("name", "Category name", required=True, list=True)],
        children=[dict(
            key="attributes", table="m_product_category_attr", fk="category_id", label="Attribute definition",
            order="attr_no", seq_col="attr_no", max_rows=4,
            fields=[T("label", "Attribute label", required=True),
                    dict(name="mode", label="Manual / List", type="select", options=["List", "Manual"], default="List", required=True),
                    dict(name="generic_type_id", label="Connected master", type="ref", ref="generic_type",
                         show_if={"mode": ["List"]})])]),
    "product_group": dict(
        table="m_product_group", label="Product groups", group="Products", icon="shirt", hierarchical=True,
        quick_add=True, fields=[T("name", "Name", required=True, list=True)]),
    "product": dict(
        table="m_product", label="Products", group="Products", icon="shirt", label_col="item_name", hook="product",
        help="The item name (SKU) is built from the product name and its attribute values.",
        fields=[
            T("name", "Product name", required=True, list=True),
            dict(name="item_name", label="Item name (SKU)", type="readonly", list=True),
            dict(name="category_id", label="Category", type="ref", ref="product_category", required=True, list=True),
            dict(name="group_id", label="Group", type="ref", ref="product_group", list=True),
            dict(name="uom_id", label="UOM", type="ref", ref="uom", required=True, list=True),
            dict(name="barcode", label="Barcode", type="readonly", list=True, help="Generated on first save"),
            dict(name="attr_values", label="Attributes", type="attrs", depends="category_id"),
            dict(name="default_sales_ledger_id", label="Default sales ledger", type="ref", ref="ledger", filter={"ledger_type": "Sales"}),
            dict(name="default_purchase_ledger_id", label="Default purchase ledger", type="ref", ref="ledger", filter={"ledger_type": "Purchase"}),
            dict(name="gst_rate", label="GST rate %", type="decimal", default=0),
            T("hsn_code", "HSN code"),
            dict(name="package_qty", label="Package qty", type="decimal"),
            dict(name="min_stock", label="Minimum stock level", type="decimal",
                 help="Used by 'Requisition for PO' to suggest re-orders"),
        ],
        children=[
            dict(key="customers", table="m_product_customer", fk="product_id", label="Connected customers",
                 fields=[dict(name="customer_id", label="Customer", type="ref", ref="ledger",
                              filter={"ledger_type": "Customer"}, required=True),
                         T("customer_item_name", "Customer item name")]),
            dict(key="processes", table="m_product_process", fk="product_id", label="Process definition with BOM",
                 order="seq", seq_col="seq",
                 fields=[dict(name="process_id", label="Process", type="ref", ref="process", required=True),
                         dict(name="material_id", label="Material to issue", type="ref", ref="product"),
                         T("part_name", "Part name"),
                         dict(name="qty", label="Qty / unit", type="decimal", default=0),
                         dict(name="process_rate", label="Process rate", type="decimal", default=0),
                         dict(name="out_status", label="Status", type="select", options=["WIP", "FP"], default="WIP"),
                         dict(name="overhead_pct", label="Overhead %", type="decimal", default=0)]),
        ]),
    "pricelist": dict(
        table="m_pricelist", label="Price lists", group="Products", icon="tag",
        fields=[T("name", "Price list name", required=True, list=True)],
        children=[dict(key="items", table="m_pricelist_item", fk="pricelist_id", label="Items",
                       fields=[dict(name="product_id", label="Item name", type="ref", ref="product", required=True),
                               dict(name="rate", label="Rate", type="decimal", default=0),
                               dict(name="disc_pct", label="Disc %", type="decimal", default=0),
                               dict(name="disc_amt", label="Disc in amt", type="decimal", default=0)])]),

    # ---------------------------------------------------------------- accounts
    "ledger_group": dict(
        table="m_ledger_group", label="Ledger groups", group="Accounts", icon="book", hierarchical=True,
        fields=[T("name", "Name", required=True, list=True),
                dict(name="category", label="Category", type="select", required=True, list=True,
                     options=["Asset", "Liability", "Income", "Expense"])]),
    "ledger_account": dict(
        table="m_ledger_account", label="Ledger A/c", group="Accounts", icon="book", quick_add=True,
        fields=[T("name", "Ledger A/c name", required=True, list=True)]),
    "ledger": dict(
        table="m_ledger", label="Ledgers (parties)", group="Accounts", icon="users", hook="ledger",
        fields=[
            T("name", "Ledger name", required=True, list=True),
            dict(name="ledger_type", label="Ledger type", type="select", options=LEDGER_TYPES, default="General", required=True, list=True),
            dict(name="ledger_group_id", label="Ledger parent (group)", type="ref", ref="ledger_group"),
            dict(name="ledger_account_id", label="Ledger A/c", type="ref", ref="ledger_account", list=True),
            T("address", "Address"), T("city", "City", list=True), T("state", "State", list=True),
            T("pincode", "Pincode"), T("gstin", "GSTIN"), T("pan_no", "PAN no"),
            T("contact_person", "Contact person"), T("mobile", "Mobile", list=True), T("email", "Email id"),
            dict(name="tax_type", label="Tax type", type="select", options=[""] + TAX_TYPES),
            dict(name="broker_id", label="Broker name", type="ref", ref="ledger", filter={"ledger_type": "Broker"}, show_if=CUSTOMER_ONLY),
            dict(name="salesman_id", label="Salesman name", type="ref", ref="ledger", filter={"ledger_type": "Salesman"}, show_if=CUSTOMER_ONLY),
            dict(name="brokerage_rate", label="Brokerage rate %", type="decimal", show_if=CUSTOMER_ONLY),
            dict(name="cash_discount", label="Cash discount %", type="decimal", show_if=CUSTOMER_ONLY),
            dict(name="transporter_id", label="Transporter name", type="ref", ref="ledger", filter={"ledger_type": "Transporter"}, show_if=CUSTOMER_ONLY),
        ],
        children=[dict(key="discounts", table="m_ledger_discount", fk="ledger_id", label="Discount structure",
                       show_if=CUSTOMER_ONLY,
                       fields=[dict(name="product_group_id", label="Product group", type="ref", ref="product_group", required=True),
                               dict(name="disc_pct", label="Discount %", type="decimal", default=0),
                               dict(name="pricelist_id", label="Price list", type="ref", ref="pricelist"),
                               dict(name="applicable_date", label="Applicable date", type="date")])]),

    # ---------------------------------------------------------------- system
    "txn_type": dict(
        table="m_txn_type", label="Transaction types", group="System", icon="ticket", hierarchical=True, hook="txn_type",
        help="Number series per branch. 'Max number' is maintained automatically.",
        fields=[T("name", "Name", required=True, list=True),
                dict(name="txn_kind", label="Type of transaction", type="select", options=TXN_KINDS, required=True, list=True),
                dict(name="branch_id", label="Branch", type="ref", ref="branch", list=True),
                dict(name="start_date", label="Starting date", type="date"),
                T("prefix", "Prefix", list=True), T("suffix", "Suffix"),
                dict(name="start_number", label="Start number", type="int", default=1),
                dict(name="max_number", label="Max number (auto)", type="readonly", list=True)],
        children=[dict(key="terms", table="m_txn_type_terms", fk="txn_type_id", label="Terms & conditions",
                       order="sl_no", seq_col="sl_no",
                       fields=[dict(name="description", label="Description", type="textarea", required=True)])]),
}

DEFAULT_LABEL_COL = "name"

# Left-menu structure for masters (vouchers/reports are added on the frontend from /api/meta)
MASTER_GROUPS = ["Basics", "Products", "Accounts", "Production", "System"]


def label_col(key):
    return MASTERS[key].get("label_col", DEFAULT_LABEL_COL)


def field_names(key):
    return {f["name"] for f in MASTERS[key]["fields"]} | {"id", "active"}


def public_registry():
    """Registry as sent to the browser (no hooks, no table names)."""
    out = {}
    for k, m in MASTERS.items():
        out[k] = {kk: vv for kk, vv in m.items() if kk not in ("table", "hook")}
        out[k]["key"] = k
        out[k]["label_col"] = label_col(k)
        if "children" in m:
            out[k]["children"] = [{kk: vv for kk, vv in c.items() if kk not in ("table", "fk")} for c in m["children"]]
    return out
