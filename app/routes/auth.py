"""用户注册 / 登录 / 当前用户信息。"""
from flask import Blueprint, g, jsonify, request
from pymongo.errors import DuplicateKeyError

from app.db import get_db
from app.errors import ApiError
from app.security import hash_password, login_required, make_token, verify_password
from app.utils import now, to_json

bp = Blueprint("auth", __name__)


@bp.post("/api/register")
def register():
    data = request.get_json(silent=True) or {}
    username = str(data.get("username") or "").strip()
    password = str(data.get("password") or "")
    if not (2 <= len(username) <= 32):
        raise ApiError("用户名长度需在 2~32 之间")
    if len(password) < 6:
        raise ApiError("密码至少需要 6 位")
    doc = {
        "username": username,
        "passwordHash": hash_password(password),
        "createdAt": now(),
        "status": "active",
        "profile": {},
    }
    try:
        get_db().users.insert_one(doc)
    except DuplicateKeyError:
        raise ApiError("用户名已存在", 409)
    return jsonify({
        "user": to_json(doc),
        "token": make_token(doc["_id"], username),
    }), 201


@bp.post("/api/login")
def login():
    data = request.get_json(silent=True) or {}
    username = str(data.get("username") or "").strip()
    password = str(data.get("password") or "")
    user = get_db().users.find_one({"username": username})
    if not user or not verify_password(password, user["passwordHash"]):
        raise ApiError("用户名或密码错误", 401)
    return jsonify({
        "user": to_json(user),
        "token": make_token(user["_id"], user["username"]),
    })


@bp.get("/api/me")
@login_required
def me():
    return jsonify({"user": g.user})
