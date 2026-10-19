"""认证与安全：密码哈希（PBKDF2）+ HMAC 签名令牌（无状态，无需额外集合）。"""
import base64
import hashlib
import hmac
import json
import secrets
import time
from functools import wraps

from flask import g, request

from app import config
from app.errors import ApiError


# ---------------- 密码 ----------------

def hash_password(password):
    salt = secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt), 100_000)
    return f"pbkdf2${salt}${dk.hex()}"


def verify_password(password, stored):
    try:
        _, salt, expected = stored.split("$")
        dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt), 100_000)
        return hmac.compare_digest(dk.hex(), expected)
    except Exception:
        return False


# ---------------- 令牌（body.signature 形式的轻量签名 token） ----------------

def _b64(raw):
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _unb64(s):
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def make_token(user_id, username):
    payload = {
        "uid": str(user_id),
        "username": username,
        "exp": int(time.time()) + config.TOKEN_TTL,
    }
    body = _b64(json.dumps(payload, ensure_ascii=False).encode("utf-8"))
    sig = _b64(hmac.new(config.SECRET.encode("utf-8"), body.encode("ascii"), hashlib.sha256).digest())
    return f"{body}.{sig}"


def parse_token(token):
    try:
        body, sig = token.split(".")
        expect = _b64(hmac.new(config.SECRET.encode("utf-8"), body.encode("ascii"), hashlib.sha256).digest())
        if not hmac.compare_digest(sig, expect):
            return None
        payload = json.loads(_unb64(body))
        if payload.get("exp", 0) < time.time():
            return None
        return payload
    except Exception:
        return None


# ---------------- Flask 集成 ----------------

def load_user():
    """从 Authorization: Bearer <token> 解析当前用户，写入 g.user（可能为 None）。"""
    g.user = None
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        payload = parse_token(auth[7:].strip())
        if payload:
            g.user = {"id": payload["uid"], "username": payload.get("username", "")}


def login_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not g.get("user"):
            raise ApiError("请先登录", 401)
        return fn(*args, **kwargs)
    return wrapper
