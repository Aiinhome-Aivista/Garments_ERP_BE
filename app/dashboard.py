from flask import Blueprint, jsonify

from . import db
from .auth import login_required

bp = Blueprint("dashboard", __name__, url_prefix="/api")


@bp.get("/dashboard")
@login_required
def dashboard():
    with db.tx() as c:
        pend = {r["doc_type"]: r["n"] for r in db.all_(c, "SELECT doc_type, COUNT(*) n FROM txn_header WHERE approval_status='Pending' GROUP BY doc_type")}
        flow = {
            "orders_open": db.scalar(c, "SELECT COUNT(DISTINCT src_header_id) FROM (SELECT h.id src_header_id, i.qty - COALESCE((SELECT SUM(x.qty) FROM txn_item x "
                                        "JOIN txn_header xh ON xh.id=x.header_id WHERE x.src_item_id=i.id AND xh.approval_status<>'Rejected'),0) pend "
                                        "FROM txn_item i JOIN txn_header h ON h.id=i.header_id WHERE h.doc_type='sales_order' AND h.approval_status='Approved') t WHERE pend>0"),
            "plans": db.scalar(c, "SELECT COUNT(*) FROM prod_plan WHERE status='Accepted'"),
            "req_open": db.scalar(c, "SELECT COUNT(*) FROM requisition WHERE status='Open'"),
            "po_open": db.scalar(c, "SELECT COUNT(DISTINCT src_header_id) FROM (SELECT h.id src_header_id, i.qty - COALESCE((SELECT SUM(x.qty) FROM txn_item x "
                                    "JOIN txn_header xh ON xh.id=x.header_id WHERE x.src_item_id=i.id AND xh.approval_status<>'Rejected'),0) pend "
                                    "FROM txn_item i JOIN txn_header h ON h.id=i.header_id WHERE h.doc_type='purchase_order' AND h.approval_status='Approved') t WHERE pend>0"),
            "grn_uninvoiced": db.scalar(c, "SELECT COUNT(DISTINCT src_header_id) FROM (SELECT h.id src_header_id, i.qty - COALESCE((SELECT SUM(x.qty) FROM txn_item x "
                                           "JOIN txn_header xh ON xh.id=x.header_id WHERE x.src_item_id=i.id AND xh.approval_status<>'Rejected'),0) pend "
                                           "FROM txn_item i JOIN txn_header h ON h.id=i.header_id WHERE h.doc_type='grn' AND h.approval_status='Approved') t WHERE pend>0"),
            "challan_uninvoiced": db.scalar(c, "SELECT COUNT(*) FROM txn_header h WHERE h.doc_type='challan' AND h.approval_status='Approved' "
                                               "AND NOT EXISTS (SELECT 1 FROM txn_header s WHERE s.packing_list_id=h.id AND s.approval_status<>'Rejected')"),
        }
        sales = db.all_(c, "SELECT DATE_FORMAT(voucher_date,'%Y-%m') ym, SUM(total_value) v FROM txn_header WHERE doc_type='sales' AND approval_status='Approved' "
                           "AND voucher_date >= DATE_SUB(CURDATE(), INTERVAL 5 MONTH) GROUP BY ym ORDER BY ym")
        low = db.scalar(c, "SELECT COUNT(*) FROM m_product p WHERE p.active=1 AND p.min_stock>0 AND "
                           "COALESCE((SELECT SUM(qty) FROM stock_ledger WHERE product_id=p.id),0) < p.min_stock")
        recent = db.all_(c, "SELECT h.id,h.doc_type,h.voucher_no,h.voucher_date,h.total_value,h.approval_status,pa.name party_name FROM txn_header h "
                            "JOIN m_ledger pa ON pa.id=h.party_id ORDER BY h.id DESC LIMIT 8")
        counts = {"products": db.scalar(c, "SELECT COUNT(*) FROM m_product WHERE active=1"),
                  "parties": db.scalar(c, "SELECT COUNT(*) FROM m_ledger WHERE active=1 AND ledger_type IN ('Customer','Supplier')"),
                  "stock_pcs": db.scalar(c, "SELECT COALESCE(SUM(qty),0) FROM stock_ledger")}
    return jsonify({"pending_approvals": pend, "flow": flow, "sales_trend": sales, "low_stock": low, "recent": recent, "counts": counts})
