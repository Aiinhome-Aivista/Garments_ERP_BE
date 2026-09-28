import datetime as dt
from decimal import Decimal

from flask import Flask, jsonify
from flask.json.provider import DefaultJSONProvider
from sqlalchemy.exc import DBAPIError

from . import db
from .config import Config
from .errors import ApiError


class Json(DefaultJSONProvider):
    sort_keys = False

    @staticmethod
    def default(o):
        if isinstance(o, Decimal):
            return float(o)
        if isinstance(o, (dt.datetime, dt.date)):
            return o.isoformat()
        return DefaultJSONProvider.default(o)


def create_app():
    app = Flask(__name__)
    app.config.from_object(Config)
    app.json = Json(app)
    db.init_engine(app.config["DATABASE_URL"])

    from . import auth, dashboard, integrations, masters, planning, pricing, stock, vouchers
    from .registry import MASTER_GROUPS, LEDGER_TYPES, public_registry
    from .vouchers_cfg import RATE_IN, RATE_ON, public_vouchers
    for m in (auth, masters, vouchers, planning, stock, pricing, integrations, dashboard):
        app.register_blueprint(m.bp)

    @app.get("/api/meta")
    @auth.login_required
    def meta():
        return jsonify({"masters": public_registry(), "master_groups": MASTER_GROUPS, "vouchers": public_vouchers(),
                        "item_cols": __import__("app.vouchers_cfg", fromlist=["ITEM_COLS"]).ITEM_COLS,
                        "rate_in": RATE_IN, "rate_on": [{"value": v, "label": l} for v, l in RATE_ON],
                        "ledger_types": LEDGER_TYPES, "actions": auth.ACTIONS})

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

    from .cli import register_cli
    register_cli(app)
    return app
