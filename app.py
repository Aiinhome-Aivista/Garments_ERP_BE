import datetime as dt
from decimal import Decimal

from flask import Flask, jsonify
from flask.json.provider import DefaultJSONProvider
from sqlalchemy.exc import DBAPIError

from app import db
from app.config import Config
from app.errors import ApiError


class Json(DefaultJSONProvider):
    sort_keys = False

    @staticmethod
    def default(o):
        if isinstance(o, Decimal):
            return float(o)
        if isinstance(o, (dt.datetime, dt.date)):
            return o.isoformat()
        return DefaultJSONProvider.default(o)

app = Flask(__name__)
app.config.from_object(Config)
app.json = Json(app)
db.init_engine(app.config["DATABASE_URL"])

@app.errorhandler(ApiError)
def handle_api_error(error):
    response = {"error": error.message}
    if error.field:
        response["field"] = error.field
    return jsonify(response), error.status

@app.errorhandler(Exception)
def handle_exception(error):
    # Log the original exception traceback here if needed
    import traceback
    traceback.print_exc()
    return jsonify({"error": str(error)}), 500

from controllers import auth_controller, dashboard_controller, integrations_controller, masters_controller, planning_controller, pricing_controller, stock_controller, vouchers_controller

@app.route("/")
def home():
    return "api is running"

# --- ROUTE DEFINITIONS ---
@app.route('/api/auth/login', methods=['POST'])
def auth_login():
    return auth_controller.login()

@app.route('/api/auth/me', methods=['GET'])
def auth_me():
    return auth_controller.me()

@app.route('/api/auth/change-password', methods=['POST'])
def auth_change_password():
    return auth_controller.change_password()

@app.route('/api/auth/register', methods=['POST'])
def auth_register():
    return auth_controller.register()

@app.route('/api/roles', methods=['GET'])
def auth_roles_list():
    return auth_controller.roles_list()

@app.route('/api/roles', methods=['POST'])
def auth_role_save(rid=None):
    return auth_controller.role_save(rid=rid)

@app.route('/api/roles/<int:rid>', methods=['PUT'])
def auth_role_save_1(rid=None):
    return auth_controller.role_save(rid=rid)

@app.route('/api/roles/<int:rid>', methods=['DELETE'])
def auth_role_delete(rid):
    return auth_controller.role_delete(rid=rid)

@app.route('/api/users', methods=['GET'])
def auth_users_list():
    return auth_controller.users_list()

@app.route('/api/users', methods=['POST'])
def auth_user_save(uid=None):
    return auth_controller.user_save(uid=uid)

@app.route('/api/users/<int:uid>', methods=['PUT'])
def auth_user_save_1(uid=None):
    return auth_controller.user_save(uid=uid)

@app.route('/api/api-clients', methods=['GET'])
def auth_api_clients():
    return auth_controller.api_clients()

@app.route('/api/api-clients', methods=['POST'])
def auth_api_client_create():
    return auth_controller.api_client_create()

@app.route('/api/api-clients/<int:cid>/toggle', methods=['PUT'])
def auth_api_client_toggle(cid):
    return auth_controller.api_client_toggle(cid=cid)

@app.route('/api/dashboard', methods=['GET'])
def dashboard_dashboard():
    return dashboard_controller.dashboard()

@app.route('/api/sales-orders', methods=['POST'])
def integrations_sales_order():
    return integrations_controller.sales_order()

@app.route('/api/packing-lists', methods=['POST'])
def integrations_packing_list():
    return integrations_controller.packing_list()

@app.route('/api/masters/<key>', methods=['GET'])
def masters_list_(key):
    return masters_controller.list_(key=key)

@app.route('/api/lookup/<key>', methods=['GET'])
def masters_lookup(key):
    return masters_controller.lookup(key=key)

@app.route('/api/masters/<key>/<int:rid>', methods=['GET'])
def masters_get_(key, rid):
    return masters_controller.get_(key=key, rid=rid)

@app.route('/api/masters/<key>', methods=['POST'])
def masters_create(key):
    return masters_controller.create(key=key)

@app.route('/api/masters/<key>/<int:rid>', methods=['PUT'])
def masters_update_(key, rid):
    return masters_controller.update_(key=key, rid=rid)

@app.route('/api/masters/<key>/<int:rid>', methods=['DELETE'])
def masters_delete_(key, rid):
    return masters_controller.delete_(key=key, rid=rid)

@app.route('/api/planning/open-orders', methods=['GET'])
def planning_open_orders():
    return planning_controller.open_orders()

@app.route('/api/planning/analyze', methods=['POST'])
def planning_analyze_():
    return planning_controller.analyze_()

@app.route('/api/planning', methods=['POST'])
def planning_accept():
    return planning_controller.accept()

@app.route('/api/planning', methods=['GET'])
def planning_plans():
    return planning_controller.plans()

@app.route('/api/planning/<int:pid>', methods=['GET'])
def planning_plan_get(pid):
    return planning_controller.plan_get(pid=pid)

@app.route('/api/planning/<int:pid>/cancel', methods=['POST'])
def planning_plan_cancel(pid):
    return planning_controller.plan_cancel(pid=pid)

@app.route('/api/requisitions', methods=['GET'])
def planning_req_list():
    return planning_controller.req_list()

@app.route('/api/requisitions/<int:rid>', methods=['GET'])
def planning_req_get(rid):
    return planning_controller.req_get(rid=rid)

@app.route('/api/requisitions/suggest', methods=['GET'])
def planning_req_suggest():
    return planning_controller.req_suggest()

@app.route('/api/requisitions', methods=['POST'])
def planning_req_create():
    return planning_controller.req_create()

@app.route('/api/requisitions/<int:rid>', methods=['PUT'])
def planning_req_update(rid):
    return planning_controller.req_update(rid=rid)

@app.route('/api/requisitions/<int:rid>/close', methods=['POST'])
def planning_req_close(rid):
    return planning_controller.req_close(rid=rid)

@app.route('/api/requisitions/<int:rid>', methods=['DELETE'])
def planning_req_delete(rid):
    return planning_controller.req_delete(rid=rid)

@app.route('/api/pricing/resolve', methods=['GET'])
def pricing_resolve_():
    return pricing_controller.resolve_()

@app.route('/api/pricing/pricelist-rate', methods=['GET'])
def pricing_pricelist_rate():
    return pricing_controller.pricelist_rate()

@app.route('/api/stock/scan/<code>', methods=['GET'])
def stock_scan(code):
    return stock_controller.scan(code=code)

@app.route('/api/stock/report', methods=['GET'])
def stock_report():
    return stock_controller.report()

@app.route('/api/vouchers/<doc>', methods=['GET'])
def vouchers_list_(doc):
    return vouchers_controller.list_(doc=doc)

@app.route('/api/vouchers/<doc>/<int:hid>', methods=['GET'])
def vouchers_get_(doc, hid):
    return vouchers_controller.get_(doc=doc, hid=hid)

@app.route('/api/vouchers/<doc>/next-number', methods=['GET'])
def vouchers_preview_number(doc):
    return vouchers_controller.preview_number(doc=doc)

@app.route('/api/vouchers/<doc>', methods=['POST'])
def vouchers_create(doc):
    return vouchers_controller.create(doc=doc)

@app.route('/api/vouchers/<doc>/<int:hid>', methods=['PUT'])
def vouchers_update_(doc, hid):
    return vouchers_controller.update_(doc=doc, hid=hid)

@app.route('/api/vouchers/<doc>/<int:hid>', methods=['DELETE'])
def vouchers_delete_(doc, hid):
    return vouchers_controller.delete_(doc=doc, hid=hid)

@app.route('/api/vouchers/<doc>/<int:hid>/<action>', methods=['POST'])
def vouchers_action_(doc, hid, action):
    return vouchers_controller.action_(doc=doc, hid=hid, action=action)

@app.route('/api/vouchers/<doc>/pending-source', methods=['GET'])
def vouchers_pending_(doc):
    return vouchers_controller.pending_(doc=doc)

@app.route('/api/vouchers/<doc>/gst-suggest', methods=['POST'])
def vouchers_gst_(doc):
    return vouchers_controller.gst_(doc=doc)

@app.route('/api/vouchers/<doc>/ref-vouchers', methods=['GET'])
def vouchers_ref_vouchers(doc):
    return vouchers_controller.ref_vouchers(doc=doc)

@app.route('/api/logistics', methods=['GET'])
def vouchers_logistics_list():
    return vouchers_controller.logistics_list()

@app.route('/api/logistics/<int:hid>', methods=['GET'])
def vouchers_logistics_get(hid):
    return vouchers_controller.logistics_get(hid=hid)

@app.route('/api/logistics/<int:hid>', methods=['PUT'])
def vouchers_logistics_save(hid):
    return vouchers_controller.logistics_save(hid=hid)



from app.registry import MASTER_GROUPS, LEDGER_TYPES, public_registry
from app.vouchers_cfg import RATE_IN, RATE_ON, public_vouchers

@app.get("/api/meta")
@auth_controller.login_required
def meta():
    return jsonify({"masters": public_registry(), "master_groups": MASTER_GROUPS, "vouchers": public_vouchers(),
                    "item_cols": __import__("app.vouchers_cfg", fromlist=["ITEM_COLS"]).ITEM_COLS,
                    "rate_in": RATE_IN, "rate_on": [{"value": v, "label": l} for v, l in RATE_ON],
                    "ledger_types": LEDGER_TYPES, "actions": auth_controller.ACTIONS})

@app.get("/api/health")
def health():
    with db.tx() as c:
        db.scalar(c, "SELECT 1")
    return jsonify({"ok": True})

@app.errorhandler(ApiError)
def api_error(e):
    return jsonify({"error": e.message, "field": e.field}), e.status

@app.errorhandler(DBAPIError)
def db_error(e):
    app.logger.exception("database error")
    msg = str(e.orig)
    if "Duplicate" in msg:
        return jsonify({"error": "That already exists (duplicate value)."}), 409
    return jsonify({"error": "The database rejected this change. Check the values and try again."}), 400

@app.errorhandler(404)
def nf(e):
    return jsonify({"error": "Not found"}), 404

@app.errorhandler(Exception)
def boom(e):
    from werkzeug.exceptions import HTTPException
    if isinstance(e, HTTPException):
        return jsonify({"error": e.description}), e.code
    app.logger.exception("unhandled error")
    return jsonify({"error": "Something went wrong on the server. It has been logged."}), 500

# Optional: serve the built React app from Flask (nginx normally does this in production)
import os
from flask import send_from_directory
dist = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "frontend", "dist"))
if os.path.isdir(dist) and os.getenv("SERVE_FRONTEND", "1") == "1":
    @app.get("/", defaults={"path": ""})
    @app.get("/<path:path>")
    def spa(path):
        if path.startswith("api/"):
            return jsonify({"error": "Not found"}), 404
        full = os.path.join(dist, path)
        return send_from_directory(dist, path if path and os.path.isfile(full) else "index.html")

from app.cli import register_cli
register_cli(app)

if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=5000,
        debug=True
    )
