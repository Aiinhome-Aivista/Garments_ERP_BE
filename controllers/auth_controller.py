"""JWT auth, permission decorator, users / roles / API-key admin endpoints."""
import datetime as dt
import hashlib
import json
import secrets
from functools import wraps

import jwt
from flask import current_app, g, jsonify, request
from werkzeug.security import check_password_hash, generate_password_hash

from app import db
from app.errors import ApiError


ACTIONS = ["view", "create", "edit", "delete", "approve"]

def make_token(user):
    exp = dt.datetime.utcnow() + dt.timedelta(hours=current_app.config["TOKEN_HOURS"])
    return jwt.encode({"uid": user["id"], "exp": exp}, current_app.config["SECRET_KEY"], algorithm="HS256")

def load_user():
    h = request.headers.get("Authorization", "")
    if not h.startswith("Bearer "):
        raise ApiError("Sign in to continue", 401)
    try:
        data = jwt.decode(h[7:], current_app.config["SECRET_KEY"], algorithms=["HS256"])
    except jwt.PyJWTError:
        raise ApiError("Your session expired. Please sign in again", 401)
    with db.tx() as c:
        u = db.one(c, "SELECT u.id,u.username,u.full_name,u.active,r.name role, r.permissions "
                      "FROM app_user u JOIN app_role r ON r.id=u.role_id WHERE u.id=:i", i=data["uid"])
    if not u or not u["active"]:
        raise ApiError("This account is disabled", 401)
    perms = u["permissions"]
    u["permissions"] = json.loads(perms) if isinstance(perms, (str, bytes)) else perms
    return u

def can(user, resource, action):
    p = user["permissions"]
    if "*" in p:
        return True
    acts = p.get(resource, [])
    return "*" in acts or action in acts

def require(resource, action="view"):
    """Decorator. `resource` may be a callable(kwargs)->str for dynamic keys (e.g. masters/<key>)."""
    def deco(fn):
        @wraps(fn)
        def wrapper(*a, **kw):
            g.user = load_user()
            res = resource(kw) if callable(resource) else resource
            if not can(g.user, res, action):
                raise ApiError(f"You don't have permission to {action} {res.replace('_', ' ')}", 403)
            return fn(*a, **kw)
        return wrapper
    return deco

def login_required(fn):
    @wraps(fn)
    def wrapper(*a, **kw):
        g.user = load_user()
        return fn(*a, **kw)
    return wrapper


# ------------------------------------------------------------------ routes
def login():
    d = request.get_json(force=True) or {}
    with db.tx() as c:
        u = db.one(c, "SELECT * FROM app_user WHERE username=:u", u=(d.get("username") or "").strip())
        pwd_match = u and ((u["password_hash"] == (d.get("password") or "")) or check_password_hash(u["password_hash"], d.get("password") or ""))
        if not u or not u["active"] or not pwd_match:
            raise ApiError("Wrong username or password", 401)
        db.run(c, "UPDATE app_user SET last_login=NOW() WHERE id=:i", i=u["id"])
    return jsonify({"token": make_token(u), "user": me_payload(u["id"])})

def me_payload(uid):
    with db.tx() as c:
        u = db.one(c, "SELECT u.id,u.username,u.full_name,r.name role,r.permissions FROM app_user u "
                      "JOIN app_role r ON r.id=u.role_id WHERE u.id=:i", i=uid)
    perms = u["permissions"]
    u["permissions"] = json.loads(perms) if isinstance(perms, (str, bytes)) else perms
    return u


@login_required
def me():
    return jsonify(me_payload(g.user["id"]))


@login_required
def change_password():
    d = request.get_json(force=True) or {}
    if len(d.get("new_password") or "") < 8:
        raise ApiError("New password needs at least 8 characters")
    with db.tx() as c:
        u = db.one(c, "SELECT * FROM app_user WHERE id=:i", i=g.user["id"])
        pwd_match = u and ((u["password_hash"] == (d.get("old_password") or "")) or check_password_hash(u["password_hash"], d.get("old_password") or ""))
        if not pwd_match:
            raise ApiError("Current password is wrong", field="old_password")
        db.run(c, "UPDATE app_user SET password_hash=:h WHERE id=:i", h=d["new_password"], i=u["id"])
    return jsonify({"ok": True})

def register():
    d = request.get_json(force=True) or {}
    username = (d.get("username") or "").strip()
    email = (d.get("email") or "").strip()
    full_name = (d.get("full_name") or "").strip()
    password = d.get("password") or ""
    if not username or not full_name or not email or len(password) < 8:
        raise ApiError("Username, email, full name, and a password of at least 8 characters are required")
    with db.tx() as c:
        if db.scalar(c, "SELECT COUNT(*) FROM app_user WHERE username=:u OR email=:e", u=username, e=email):
            raise ApiError("That username or email is taken")
        role_name = (d.get("role") or "").strip()
        if role_name not in ["Sales & dispatch", "Stores & purchase"]:
            raise ApiError("Invalid role selected")
        role_id = db.scalar(c, "SELECT id FROM app_role WHERE name=:r", r=role_name)
        if not role_id:
            raise ApiError("Selected role does not exist in the database")
        uid = db.insert(c, "app_user", {
            "username": username,
            "email": email,
            "full_name": full_name,
            "role_id": role_id,
            "active": 1,
            "password_hash": password
        })
        u = db.one(c, "SELECT * FROM app_user WHERE id=:i", i=uid)
        db.run(c, "UPDATE app_user SET last_login=NOW() WHERE id=:i", i=uid)
    return jsonify({"token": make_token(u), "user": me_payload(u["id"])})

# ---- roles
@require("users", "view")
def roles_list():
    with db.tx() as c:
        rows = db.all_(c, "SELECT id,name,permissions FROM app_role ORDER BY name")
    for r in rows:
        if isinstance(r["permissions"], (str, bytes)):
            r["permissions"] = json.loads(r["permissions"])
    return jsonify(rows)


@require("users", "edit")
def role_save(rid=None):
    d = request.get_json(force=True) or {}
    name = (d.get("name") or "").strip()
    if not name:
        raise ApiError("Role name is required", field="name")
    perms = d.get("permissions") or {}
    with db.tx() as c:
        if rid:
            db.run(c, "UPDATE app_role SET name=:n, permissions=:p WHERE id=:i", n=name, p=json.dumps(perms), i=rid)
        else:
            rid = db.insert(c, "app_role", {"name": name, "permissions": json.dumps(perms)})
    return jsonify({"id": rid})


@require("users", "delete")
def role_delete(rid):
    with db.tx() as c:
        if db.scalar(c, "SELECT COUNT(*) FROM app_user WHERE role_id=:i", i=rid):
            raise ApiError("Users are still assigned to this role")
        db.run(c, "DELETE FROM app_role WHERE id=:i", i=rid)
    return jsonify({"ok": True})


# ---- users
@require("users", "view")
def users_list():
    with db.tx() as c:
        return jsonify(db.all_(c, "SELECT u.id,u.username,u.full_name,u.active,u.role_id,r.name role,u.last_login "
                                  "FROM app_user u JOIN app_role r ON r.id=u.role_id ORDER BY u.username"))


@require("users", "edit")
def user_save(uid=None):
    d = request.get_json(force=True) or {}
    data = {"username": (d.get("username") or "").strip(), "full_name": (d.get("full_name") or "").strip(),
            "role_id": d.get("role_id"), "active": 1 if d.get("active", True) else 0}
    if not data["username"] or not data["full_name"] or not data["role_id"]:
        raise ApiError("Username, name and role are required")
    with db.tx() as c:
        if db.scalar(c, "SELECT COUNT(*) FROM app_user WHERE username=:u AND id<>:i", u=data["username"], i=uid or 0):
            raise ApiError("That username is taken", field="username")
        if uid:
            if d.get("password"):
                data["password_hash"] = d["password"]
            db.update(c, "app_user", data, "id", uid)
        else:
            if len(d.get("password") or "") < 8:
                raise ApiError("Password needs at least 8 characters", field="password")
            data["password_hash"] = d["password"]
            uid = db.insert(c, "app_user", data)
    return jsonify({"id": uid})


@require("users", "delete")
def user_delete(uid):
    with db.tx() as c:
        if db.scalar(c, "SELECT COUNT(*) FROM app_user WHERE id=:i AND username='admin'", i=uid):
            raise ApiError("Cannot delete the default admin user")
        db.run(c, "DELETE FROM app_user WHERE id=:i", i=uid)
    return jsonify({"ok": True})


# ---- API keys (for sales-order / packing-list integrations)
@require("users", "view")
def api_clients():
    with db.tx() as c:
        return jsonify(db.all_(c, "SELECT id,name,key_prefix,active,created_at FROM api_client ORDER BY id DESC"))


@require("users", "edit")
def api_client_create():
    name = ((request.get_json(force=True) or {}).get("name") or "").strip()
    if not name:
        raise ApiError("Give the integration a name", field="name")
    key = "gk_" + secrets.token_urlsafe(30)
    with db.tx() as c:
        cid = db.insert(c, "api_client", {"name": name, "key_prefix": key[:9],
                                          "key_hash": hashlib.sha256(key.encode()).hexdigest()})
    return jsonify({"id": cid, "key": key, "note": "Copy this key now. It cannot be shown again."})


@require("users", "edit")
def api_client_toggle(cid):
    with db.tx() as c:
        db.run(c, "UPDATE api_client SET active = 1-active WHERE id=:i", i=cid)
    return jsonify({"ok": True})
