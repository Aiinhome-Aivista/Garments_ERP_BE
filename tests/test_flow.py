"""End-to-end smoke test. Run against a scratch database:
    flask --app wsgi init-db --demo && python tests/test_flow.py
It walks: pricing -> sales order -> planning -> requisition -> PO -> GRN -> purchase -> challan (scan)
-> sales invoice -> logistics -> sales return -> API integration."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from app import create_app  # noqa: E402

app = create_app()
cl = app.test_client()
H = {}


def call(method, url, body=None, ok=(200, 201), headers=None):
    r = getattr(cl, method)(url, json=body, headers={**H, **(headers or {})})
    if r.status_code not in ok:
        raise AssertionError(f"{method.upper()} {url} -> {r.status_code}: {r.get_data(as_text=True)[:400]}")
    return r.get_json()


def fail_expected(method, url, body, contains):
    r = getattr(cl, method)(url, json=body, headers=H)
    assert r.status_code >= 400 and contains.lower() in r.get_json()["error"].lower(), (r.status_code, r.get_json())
    print("   blocked as expected:", r.get_json()["error"])


def lk(key, **f):
    q = "&".join(f"f_{k}={v}" for k, v in f.items())
    return call("get", f"/api/lookup/{key}?{q}")


def by_name(rows, name):
    return next(r for r in rows if r["label"] == name)


print("1 login")
tok = call("post", "/api/auth/login", {"username": "admin", "password": os.getenv("ADMIN_PASSWORD", "Admin@12345")})["token"]
H["Authorization"] = "Bearer " + tok
meta = call("get", "/api/meta")
assert "sales_order" in meta["vouchers"] and "product" in meta["masters"]

print("2 masters / product sku / barcode")
prods = lk("product")
tee = next(p for p in prods if p["name"] == "Baby Hug kids tee")
assert tee["item_name"] == "Baby Hug kids tee-M-Navy", tee["item_name"]
assert len(tee["barcode"]) == 13
fabric = next(p for p in prods if p["name"] == "Cotton jersey fabric")
cust = by_name(lk("ledger", ledger_type="Customer"), "Little Steps Retail")
sup = by_name(lk("ledger", ledger_type="Supplier"), "Fabric Mart Pvt Ltd")
fail_expected("post", "/api/masters/product", {"name": "X", "category_id": tee["category_id"], "uom_id": tee["uom_id"], "attr_values": {}}, "required")

print("3 pricing from discount structure (group hierarchy: BHKIDS -> Baby Hug)")
pr = call("get", f"/api/pricing/resolve?party_id={cust['id']}&product_id={tee['id']}&date=2026-09-28")
assert pr["rate"] == 320 and pr["disc_pct"] == 5 and pr["pricelist_label"] == "Retail 2026", pr

tt = lambda kind: next(t for t in lk("txn_type", txn_kind=kind.replace(" ", "%20")))["id"]
godown = by_name(lk("godown"), "Main store")["id"]
cgst, sgst = by_name(lk("ledger"), "CGST")["id"], by_name(lk("ledger"), "SGST")["id"]

print("4 sales order + GST helper + totals")
gst = call("post", "/api/vouchers/sales_order/gst-suggest", {"party_id": cust["id"], "items": [{"product_id": tee["id"], "qty": 100, "rate": 320, "disc_pct": 5}]})
assert not gst["inter_state"] and len(gst["lines"]) == 2 and gst["lines"][0]["rate"] == 760.0, gst
so = call("post", "/api/vouchers/sales_order", {
    "txn_type_id": tt("Sales order"), "voucher_date": "2026-09-28", "party_id": cust["id"], "retailer_name": "Test retailer",
    "items": [{"product_id": tee["id"], "qty": 100, "rate": 320, "disc_pct": 5, "pricelist_id": pr["pricelist_id"]}],
    "ledgers": [{"ledger_id": g["ledger_id"], "rate": g["rate"], "rate_in": "value", "rate_on": "auto"} for g in gst["lines"]] +
               [{"ledger_id": by_name(lk("ledger"), "Freight")["id"], "rate": 2, "rate_in": "value", "rate_on": "total_qty"}],
    "payments": [{"description": "50% advance"}, {"description": "Balance in 30 days"}]})
assert so["voucher_no"] == "SO/0001", so["voucher_no"]
assert so["total_product_value"] == 30400.0 and so["total_value"] == 30400 + 760 * 2 + 200, so["total_value"]
call("post", f"/api/vouchers/sales_order/{so['id']}/approve")

print("5 planning: analyse, accept -> requisition, free stock reserved")
oo = call("get", "/api/planning/open-orders")
assert any(o["id"] == so["id"] for o in oo)
an = call("post", "/api/planning/analyze", {"order_ids": [so["id"]]})
assert an["fg"][0]["required_qty"] == 100 and an["fg"][0]["balance_qty"] == 100
mats = {m["item_name"]: m for m in an["materials"]}
assert abs(mats["Cotton jersey fabric-White"]["required_qty"] - 45) < 1e-6 and abs(mats["Sewing thread"]["required_qty"] - 1) < 1e-6, mats
plan = call("post", "/api/planning", {"order_ids": [so["id"]], "remarks": "test"})
assert plan["plan_no"] == "PP/0001" and plan["requisition_no"] == "REQ/0001", plan
fail_expected("post", f"/api/vouchers/sales_order/{so['id']}/unapprove", None, "can't be un-approved")

print("6 PO from requisition (over-quantity blocked) -> approve")
req = call("get", "/api/requisitions")[0]
reqd = call("get", f"/api/requisitions/{req['id']}")
rl = next(i for i in reqd["items"] if i["product_id"] == fabric["id"])
pend = call("get", f"/api/vouchers/purchase_order/pending-source")
assert any(p["req_item_id"] == rl["id"] for p in pend)
base_po = {"txn_type_id": tt("Purchase Order"), "voucher_date": "2026-09-28", "party_id": sup["id"]}
fail_expected("post", "/api/vouchers/purchase_order", {**base_po, "items": [{"product_id": fabric["id"], "qty": 46, "rate": 150, "req_item_id": rl["id"], "src_header_id": req["id"]}]}, "exceeds")
po = call("post", "/api/vouchers/purchase_order", {**base_po, "items": [{"product_id": fabric["id"], "qty": 45, "rate": 150, "req_item_id": rl["id"], "indent_no": req["req_no"]}]})
call("post", f"/api/vouchers/purchase_order/{po['id']}/approve")

print("7 GRN (auto barcode) -> stock in; purchase invoice from GRN")
pl = call("get", f"/api/vouchers/grn/pending-source?party_id={sup['id']}")
pol = next(p for p in pl if p["src_header_id"] == po["id"])
grn = call("post", "/api/vouchers/grn", {"txn_type_id": tt("GRN"), "voucher_date": "2026-09-29", "party_id": sup["id"], "items": [
    {"product_id": fabric["id"], "qty": 45, "rate": 150, "godown_id": godown, "bin_no": "A1", "src_header_id": po["id"], "src_item_id": pol["src_item_id"], "ref_no": po["voucher_no"]}]})
bc_fabric = grn["items"][0]["barcode"]
assert bc_fabric.startswith("B"), bc_fabric
call("post", f"/api/vouchers/grn/{grn['id']}/approve")
sr = call("get", "/api/stock/report?mode=product&q=jersey")
assert sr[0]["on_hand"] == 45
pl2 = call("get", f"/api/vouchers/purchase/pending-source?party_id={sup['id']}")
gl = next(p for p in pl2 if p["src_header_id"] == grn["id"])
pi = call("post", "/api/vouchers/purchase", {"txn_type_id": tt("Purchase"), "voucher_date": "2026-09-30", "party_id": sup["id"], "ref_doc_no": "FM/778",
      "items": [{"product_id": fabric["id"], "qty": 45, "rate": 150, "barcode": gl["barcode"], "godown_id": godown, "bin_no": "A1",
                 "src_header_id": grn["id"], "src_item_id": gl["src_item_id"], "ref_no": gl["ref_no"]}]})
call("post", f"/api/vouchers/purchase/{pi['id']}/approve")

print("8 receive finished goods (no PO), then packing list by barcode scan")
fg_grn = call("post", "/api/vouchers/grn", {"txn_type_id": tt("GRN"), "voucher_date": "2026-10-01", "party_id": sup["id"],
      "items": [{"product_id": tee["id"], "qty": 60, "rate": 200, "godown_id": godown, "bin_no": "F1"}]})
call("post", f"/api/vouchers/grn/{fg_grn['id']}/approve")
bc = fg_grn["items"][0]["barcode"]
scan = call("get", f"/api/stock/scan/{bc}")
assert scan["kind"] == "lot" and scan["lots"][0]["balance"] == 60
scan_p = call("get", f"/api/stock/scan/{tee['barcode']}")
assert scan_p["kind"] == "product" and scan_p["lots"][0]["barcode"] == bc
pend = call("get", f"/api/vouchers/challan/pending-source?party_id={cust['id']}")
line = next(p for p in pend if p["src_header_id"] == so["id"])
assert line["pending_qty"] == 100
mk = lambda q: {"txn_type_id": tt("Challan"), "voucher_date": "2026-10-02", "party_id": cust["id"], "items": [
    {"barcode": bc, "godown_id": godown, "bin_no": "F1", "product_id": tee["id"], "qty": q, "rate": line["rate"], "disc_pct": line["disc_pct"],
     "src_header_id": so["id"], "src_item_id": line["src_item_id"], "ref_no": so["voucher_no"]}]}
ch = call("post", "/api/vouchers/challan", mk(80))
fail_expected("post", f"/api/vouchers/challan/{ch['id']}/approve", None, "not enough stock")
call("put", f"/api/vouchers/challan/{ch['id']}", mk(50))
call("post", f"/api/vouchers/challan/{ch['id']}/approve")
ch = call("get", f"/api/vouchers/challan/{ch['id']}")          # re-read: editing re-creates the lines
assert call("get", "/api/stock/report?mode=lot&q=" + bc)[0]["balance"] == 10

print("9 sales invoice from packing list, logistics update, return by scan")
si = call("post", "/api/vouchers/sales", {"txn_type_id": tt("Sales"), "voucher_date": "2026-10-02", "party_id": cust["id"], "packing_list_id": ch["id"],
      "items": [{**i, "src_header_id": ch["id"], "src_item_id": i["id"], "godown_id": i["godown_id"]} for i in ch["items"]]})
call("post", f"/api/vouchers/sales/{si['id']}/approve")
fail_expected("post", "/api/vouchers/sales", {"txn_type_id": tt("Sales"), "voucher_date": "2026-10-02", "party_id": cust["id"], "packing_list_id": ch["id"],
      "items": [{**i, "src_header_id": ch["id"], "src_item_id": i["id"]} for i in ch["items"]]}, "exceeds")
call("put", f"/api/logistics/{si['id']}", {"eway_bill_no": "EW123", "eway_bill_date": "2026-10-02", "freight_amount": 750, "transporter_id": cust.get("transporter_id")})
assert call("get", f"/api/logistics/{si['id']}")["eway_bill_no"] == "EW123"
ret_scan = call("get", f"/api/stock/scan/{bc}?ref_voucher_id={si['id']}")
assert ret_scan["ref_item"]["max_qty"] == 50
sret = call("post", "/api/vouchers/sales_return", {"txn_type_id": tt("Sales Return"), "voucher_date": "2026-10-05", "party_id": cust["id"], "ref_voucher_id": si["id"],
      "items": [{"barcode": bc, "godown_id": godown, "bin_no": "F1", "product_id": tee["id"], "qty": 5, "rate": 320, "disc_pct": 5}]})
call("post", f"/api/vouchers/sales_return/{sret['id']}/approve")
assert call("get", "/api/stock/report?mode=lot&q=" + bc)[0]["balance"] == 15
fail_expected("post", "/api/vouchers/sales_return", {"txn_type_id": tt("Sales Return"), "voucher_date": "2026-10-05", "party_id": cust["id"], "ref_voucher_id": si["id"],
      "items": [{"barcode": bc, "godown_id": godown, "bin_no": "F1", "product_id": tee["id"], "qty": 60, "rate": 320}]}, "more than was invoiced")

print("10 API integration")
key = call("post", "/api/api-clients", {"name": "Shopify bridge"})["key"]
api = call("post", "/api/integrations/sales-orders", {"party": {"name": "Little Steps Retail"}, "items": [{"item_name": "Baby Hug kids tee-M-Navy", "qty": 12}]},
           headers={"X-API-Key": key, "Authorization": ""})
assert api["status"] == "Pending" and api["total_value"] == 12 * 320 * 0.95, api
r = cl.post("/api/integrations/sales-orders", json={}, headers={"X-API-Key": "nope"})
assert r.status_code == 401

print("11 permissions + dashboard")
d = call("get", "/api/dashboard")
assert d["counts"]["products"] >= 3
print("\nALL CHECKS PASSED")
