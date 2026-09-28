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
    "bin_no": dict(label="Bin no", type="text", width=80),
    "product_id": dict(label="Item name", type="ref", ref="product", required=True, width=260),
    "description": dict(label="Description", type="text", width=180),
    "customer_item_name": dict(label="Customer item name", type="text", width=170),
    "qty": dict(label="Qty", type="decimal", required=True, width=90),
    "pricelist_id": dict(label="Price list", type="ref", ref="pricelist", width=140),
    "rate": dict(label="Rate", type="decimal", width=100),
    "disc_pct": dict(label="Disc %", type="decimal", width=80),
    "disc_amt": dict(label="Disc amt", type="decimal", width=100),
    "net_amount": dict(label="Net amount", type="decimal", readonly=True, width=120),
    "delivery_date": dict(label="Delivery by", type="date", width=130),
    "ref_no": dict(label="Order no", type="text", readonly=True, width=120),
    "indent_no": dict(label="Indent / requisition no", type="text", readonly=True, width=140),
    "src_no": dict(label="Source doc no", type="text", readonly=True, width=120),
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
        label="Sales order", plural="Sales orders", kind="Sales order", module="Sales", icon="cart",
        party_label="Party name", party_types=LEDGER_PARTY_CUSTOMER, api=True,
        header=[dict(name="salesman_id", label="Salesman name", type="ref", ref="ledger", filter={"ledger_type": "Salesman"}, party_default="salesman_id"),
                dict(name="broker_id", label="Broker name", type="ref", ref="ledger", filter={"ledger_type": "Broker"}, party_default="broker_id"),
                dict(name="retailer_name", label="Retailer name", type="text"), REMARKS],
        cols=_ORDER_LINE, payment_terms=True, source=None, stock=0, scan=False),
    "challan": dict(
        label="Packing list", plural="Packing lists (delivery challans)", kind="Challan", module="Sales", icon="truck",
        party_label="Party name", party_types=LEDGER_PARTY_CUSTOMER, api=True, header=[REMARKS],
        cols=_SALES_LINE, source=dict(kind="sales_order", multi=True, required=True, label="Sales orders", no_label="Sales order no"),
        stock=-1, scan=True),
    "sales": dict(
        label="Sales invoice", plural="Sales invoices", kind="Sales", module="Sales", icon="receipt",
        party_label="Party name", party_types=LEDGER_PARTY_CUSTOMER,
        header=[REMARKS],
        cols=_SALES_LINE, source=dict(kind="challan", multi=False, required=True, link="packing_list_id", label="Packing list", no_label="Packing list no"),
        stock=0, scan=False),
    "sales_return": dict(
        label="Sales return (credit note)", plural="Sales returns", kind="Sales Return", module="Sales", icon="return",
        party_label="Party name", party_types=LEDGER_PARTY_CUSTOMER,
        header=[dict(name="ref_voucher_id", label="Sales invoice no", type="voucher", doc="sales", required=True),
                dict(name="ref_doc_no", label="Party doc no", type="text"),
                dict(name="ref_doc_date", label="Party doc date", type="date"), REMARKS],
        cols=_SALES_LINE, source=None, stock=1, scan=True, return_of="sales"),
    "purchase_order": dict(
        label="Purchase order", plural="Purchase orders", kind="Purchase Order", module="Procurement", icon="cart",
        party_label="Party name", party_types=LEDGER_PARTY_SUPPLIER,
        header=[REMARKS],
        cols=_ORDER_LINE + ["indent_no"], payment_terms=True,
        source=dict(kind="requisition", multi=True, required=False, label="Requisitions", no_label="Requisition no"),
        stock=0, scan=False),
    "grn": dict(
        label="GRN", plural="Goods received notes", kind="GRN", module="Procurement", icon="box",
        party_label="Party name", party_types=LEDGER_PARTY_SUPPLIER, header=[REMARKS],
        cols=["barcode", "godown_id", "bin_no", "product_id", "description", "customer_item_name", "qty",
              "pricelist_id", "rate", "disc_pct", "disc_amt", "net_amount", "ref_no", "indent_no"],
        source=dict(kind="purchase_order", multi=True, required=False, label="Purchase orders", no_label="Purchase order no"),
        stock=1, scan=False, auto_barcode=True),
    "purchase": dict(
        label="Purchase invoice", plural="Purchase invoices", kind="Purchase", module="Procurement", icon="receipt",
        party_label="Party name", party_types=LEDGER_PARTY_SUPPLIER,
        header=[dict(name="ref_doc_no", label="Party doc no", type="text"),
                dict(name="ref_doc_date", label="Party doc date", type="date"), REMARKS],
        cols=["barcode", "godown_id", "bin_no", "product_id", "description", "customer_item_name", "qty",
              "pricelist_id", "rate", "disc_pct", "disc_amt", "net_amount", "src_no", "ref_no"],
        source=dict(kind="grn", multi=True, required=True, label="GRNs", no_label="GRN no"),
        stock=0, scan=False),
    "purchase_return": dict(
        label="Purchase return", plural="Purchase returns", kind="Purchase Return", module="Procurement", icon="return",
        party_label="Party name", party_types=LEDGER_PARTY_SUPPLIER,
        header=[dict(name="ref_voucher_id", label="Purchase invoice no", type="voucher", doc="purchase", required=True), REMARKS],
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
