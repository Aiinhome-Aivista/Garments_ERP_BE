"""
Voucher (transaction) configuration.  Each document type is described declaratively; the generic
voucher service (vouchers.py) and the React voucher form both read this.

source : where lines are picked from -> kind (doc_type or 'requisition'), multi (pick several documents),
         required (every line must link to a source line), link (header column that stores a single source doc)
stock  : +1 stock in, -1 stock out, 0 none  (posted when the voucher is approved)
scan   : barcode scanning enabled on the grid
"""

LEDGER_PARTY_CUSTOMER = ["Customer"]
LEDGER_PARTY_SUPPLIER = ["Supplier", "General", "Jobworker"]

ITEM_COLS = {
    "barcode": dict(label="Barcode", type="text", width=150),
    "godown_id": dict(label="Godown", type="ref", ref="godown", width=150),
    "bin_no": dict(label="Bin No", type="text", width=80),
    "product_id": dict(label="Item Name", type="ref", ref="product", required=True, width=260),
    "description": dict(label="Description", type="text", width=180),
    "customer_item_name": dict(label="Customer Item Name", type="text", width=170),
    "qty": dict(label="Qty", type="decimal", required=True, width=90),
    "pricelist_id": dict(label="Price List", type="ref", ref="pricelist", width=140),
    "rate": dict(label="Rate", type="decimal", width=100),
    "disc_pct": dict(label="Disc %", type="decimal", width=80),
    "disc_amt": dict(label="Disc Amt", type="decimal", width=100),
    "net_amount": dict(label="Net Amount", type="decimal", readonly=True, width=120),
    "delivery_date": dict(label="Delivery by", type="date", width=130),
    "ref_no": dict(label="Order No", type="text", readonly=True, width=120),
    "indent_no": dict(label="Indent / Requisition No", type="text", readonly=True, width=140),
    "src_no": dict(label="Source Doc No", type="text", readonly=True, width=120),
    "salesman_id": dict(label="Salesman", type="ref", ref="ledger", filter={"ledger_type": "Salesman"}, width=140),
    "broker_id": dict(label="Broker", type="ref", ref="ledger", filter={"ledger_type": "Broker"}, width=140),
    "retailer_name": dict(label="Retailer", type="text", width=140),
}

_SALES_LINE = ["barcode", "godown_id", "bin_no", "product_id", "description", "customer_item_name", "qty",
               "pricelist_id", "rate", "disc_pct", "disc_amt", "net_amount", "ref_no", "salesman_id", "broker_id", "retailer_name"]
_ORDER_LINE = ["product_id", "description", "customer_item_name", "qty", "pricelist_id", "rate", "disc_pct",
               "disc_amt", "net_amount", "delivery_date"]

REMARKS = dict(name="remarks", label="Remarks", type="text", span=2)

VOUCHERS = {
    "sales_order": dict(
        label="Sales Order", plural="Sales Orders", kind="Sales order", module="Sales", icon="cart",
        party_label="Party Name", party_types=LEDGER_PARTY_CUSTOMER, api=True,
        header=[dict(name="salesman_id", label="Salesman Name", type="ref", ref="ledger", filter={"ledger_type": "Salesman"}, party_default="salesman_id"),
                dict(name="broker_id", label="Broker Name", type="ref", ref="ledger", filter={"ledger_type": "Broker"}, party_default="broker_id"),
                dict(name="retailer_name", label="Retailer Name", type="text"), REMARKS],
        cols=_ORDER_LINE, payment_terms=True, source=None, stock=0, scan=False),
    "challan": dict(
        label="Packing List", plural="Packing Lists (Delivery Challans)", kind="Challan", module="Sales", icon="truck",
        party_label="Party Name", party_types=LEDGER_PARTY_CUSTOMER, api=True, header=[REMARKS],
        cols=_SALES_LINE, source=dict(kind="sales_order", multi=True, required=True, label="Sales Orders", no_label="Sales Order No"),
        stock=-1, scan=True),
    "sales": dict(
        label="Sales Invoice", plural="Sales Invoices", kind="Sales", module="Sales", icon="receipt",
        party_label="Party Name", party_types=LEDGER_PARTY_CUSTOMER,
        header=[REMARKS],
        cols=_SALES_LINE, source=dict(kind="challan", multi=False, required=True, link="packing_list_id", label="Packing List", no_label="Packing List No"),
        stock=0, scan=False),
    "sales_return": dict(
        label="Sales Return (Credit Note)", plural="Sales Returns", kind="Sales Return", module="Sales", icon="return",
        party_label="Party Name", party_types=LEDGER_PARTY_CUSTOMER,
        header=[dict(name="ref_voucher_id", label="Sales Invoice No", type="voucher", doc="sales", required=True),
                dict(name="ref_doc_no", label="Party Doc No", type="text"),
                dict(name="ref_doc_date", label="Party Doc Date", type="date"), REMARKS],
        cols=_SALES_LINE, source=None, stock=1, scan=True, return_of="sales"),
    "purchase_order": dict(
        label="Purchase Order", plural="Purchase Orders", kind="Purchase Order", module="Procurement", icon="cart",
        party_label="Party Name", party_types=LEDGER_PARTY_SUPPLIER,
        header=[REMARKS],
        cols=_ORDER_LINE + ["indent_no"], payment_terms=True,
        source=dict(kind="requisition", multi=True, required=False, label="Requisitions", no_label="Requisition No"),
        stock=0, scan=False),
    "grn": dict(
        label="GRN", plural="Goods Received Notes", kind="GRN", module="Procurement", icon="box",
        party_label="Party Name", party_types=LEDGER_PARTY_SUPPLIER, header=[REMARKS],
        cols=["barcode", "godown_id", "bin_no", "product_id", "description", "customer_item_name", "qty",
              "pricelist_id", "rate", "disc_pct", "disc_amt", "net_amount", "ref_no", "indent_no"],
        source=dict(kind="purchase_order", multi=True, required=False, label="Purchase Orders", no_label="Purchase Order No"),
        stock=1, scan=False, auto_barcode=True),
    "purchase": dict(
        label="Purchase Invoice", plural="Purchase Invoices", kind="Purchase", module="Procurement", icon="receipt",
        party_label="Party Name", party_types=LEDGER_PARTY_SUPPLIER,
        header=[dict(name="ref_doc_no", label="Party Doc No", type="text"),
                dict(name="ref_doc_date", label="Party Doc Date", type="date"), REMARKS],
        cols=["barcode", "godown_id", "bin_no", "product_id", "description", "customer_item_name", "qty",
              "pricelist_id", "rate", "disc_pct", "disc_amt", "net_amount", "src_no", "ref_no"],
        source=dict(kind="grn", multi=True, required=True, label="GRNs", no_label="GRN No"),
        stock=0, scan=False),
    "purchase_return": dict(
        label="Purchase Return", plural="Purchase Returns", kind="Purchase Return", module="Procurement", icon="return",
        party_label="Party Name", party_types=LEDGER_PARTY_SUPPLIER,
        header=[dict(name="ref_voucher_id", label="Purchase Invoice No", type="voucher", doc="purchase", required=True), REMARKS],
        cols=["barcode", "godown_id", "bin_no", "product_id", "description", "customer_item_name", "qty",
              "pricelist_id", "rate", "disc_pct", "disc_amt", "net_amount"],
        source=None, stock=-1, scan=True, return_of="purchase"),
}

RATE_IN = ["percent", "value"]
RATE_ON = [("auto", "Auto / entered value"), ("total_qty", "Total qty"), ("total_product_value", "Total product value"),
           ("current_subtotal", "Current subtotal"), ("net_value", "Net value")]


def public_vouchers():
    out = {}
    for k, v in VOUCHERS.items():
        d = {kk: vv for kk, vv in v.items()}
        d["key"] = k
        out[k] = d
    return out
