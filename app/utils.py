"""通用小工具：时间、数值判断、文档转 JSON。"""
from datetime import datetime

from bson import ObjectId

from app.errors import ApiError


def now():
    """统一使用服务器本地时间（朴素 datetime），保证与截止时间比较口径一致。"""
    return datetime.now()


def is_number(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def to_json(doc):
    """把 Mongo 文档安全转成可 JSON 序列化的结构（_id -> id，时间 -> ISO 字符串）。"""
    if isinstance(doc, list):
        return [to_json(d) for d in doc]
    if isinstance(doc, dict):
        out = {}
        for k, v in doc.items():
            if k == "_id":
                out["id"] = str(v)
            else:
                out[k] = to_json(v)
        return out
    if isinstance(doc, ObjectId):
        return str(doc)
    if isinstance(doc, datetime):
        return doc.isoformat()
    return doc


def parse_datetime(value):
    """解析前端传来的 ISO 时间字符串；空值返回 None；非法值报 400。"""
    if value in (None, ""):
        return None
    try:
        s = str(value).strip().replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is not None:
            dt = dt.astimezone().replace(tzinfo=None)
        return dt
    except ValueError:
        raise ApiError("时间格式不正确，应类似 2026-11-01T23:59")


def question_public(q):
    """填写端题目视图：不暴露 jumpRules（导航逻辑只由服务端解释）。"""
    d = to_json(q)
    d.pop("jumpRules", None)
    return d
