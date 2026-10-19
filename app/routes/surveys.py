"""问卷管理：创建、我的列表、详情、修改元信息、发布、关闭。"""
import secrets

from bson import ObjectId
from flask import Blueprint, g, jsonify, request
from pymongo.errors import DuplicateKeyError

from app.db import get_db
from app.errors import ApiError
from app.security import login_required
from app.utils import now, parse_datetime, to_json

bp = Blueprint("surveys", __name__)

# 状态机：draft -> published <=> paused -> closed（closed 为终态）
STATUS_LABELS = {"draft": "草稿", "published": "收集中", "paused": "已暂停", "closed": "已关闭"}

# 去掉易混淆字符（0O1lI）的 base62 字母表
ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789"


def _gen_slug():
    return "".join(secrets.choice(ALPHABET) for _ in range(8))


def get_owned_survey(sid):
    if not ObjectId.is_valid(sid):
        raise ApiError("问卷不存在", 404)
    s = get_db().surveys.find_one({"_id": ObjectId(sid)})
    if not s:
        raise ApiError("问卷不存在", 404)
    if str(s["ownerId"]) != g.user["id"]:
        raise ApiError("无权访问该问卷", 403)
    return s


def _check_jump_graph(qs):
    """发布前检查：跳转目标存在 + 跳转图无环（防止填写时死循环）。"""
    ids = [str(q["_id"]) for q in qs]
    idset = set(ids)
    edges = {i: set() for i in ids}
    for idx, q in enumerate(qs):
        i = ids[idx]
        if idx + 1 < len(ids):
            edges[i].add(ids[idx + 1])
        for r in q.get("jumpRules") or []:
            t = r.get("goto")
            if t == "end":
                continue
            if t not in idset:
                raise ApiError(f"题目“{q['title']}”的跳转目标不存在，无法发布", 409)
            edges[i].add(t)
    color = {i: 0 for i in ids}

    def dfs(u):
        color[u] = 1
        for v in edges[u]:
            if color[v] == 1:
                raise ApiError("跳转规则存在循环，无法发布", 409)
            if color[v] == 0:
                dfs(v)
        color[u] = 2

    for i in ids:
        if color[i] == 0:
            dfs(i)


@bp.post("/api/surveys")
@login_required
def create_survey():
    data = request.get_json(silent=True) or {}
    title = str(data.get("title") or "").strip()
    if not (1 <= len(title) <= 100):
        raise ApiError("问卷标题长度需在 1~100 之间")
    doc = {
        "ownerId": ObjectId(g.user["id"]),
        "title": title,
        "description": str(data.get("description") or "").strip()[:2000],
        "slug": _gen_slug(),
        "status": "draft",
        "allowAnonymous": bool(data.get("allowAnonymous", False)),
        "allowMultipleSubmissions": bool(data.get("allowMultipleSubmissions", False)),
        "deadline": parse_datetime(data.get("deadline")),
        "settings": {},
        "schemaVersion": 1,
        "createdAt": now(),
        "updatedAt": now(),
    }
    db = get_db()
    for _ in range(5):
        try:
            db.surveys.insert_one(doc)
            break
        except DuplicateKeyError:
            doc["slug"] = _gen_slug()
    else:
        raise ApiError("分享链接生成失败，请重试", 500)
    out = to_json(doc)
    out["shareUrl"] = f"/survey/{doc['slug']}"
    return jsonify({"survey": out}), 201


@bp.get("/api/my/surveys")
@login_required
def my_surveys():
    db = get_db()
    docs = list(db.surveys.find({"ownerId": ObjectId(g.user["id"])}).sort("createdAt", -1))
    for s in docs:
        s["responseCount"] = db.responses.count_documents({"surveyId": s["_id"]})
    return jsonify({"surveys": to_json(docs)})


@bp.get("/api/surveys/<sid>")
@login_required
def survey_detail(sid):
    s = get_owned_survey(sid)
    qs = list(get_db().questions.find({"surveyId": s["_id"]}).sort("order", 1))
    out = to_json(s)
    out["shareUrl"] = f"/survey/{s['slug']}" if s["status"] != "draft" else None
    return jsonify({"survey": out, "questions": to_json(qs)})


@bp.patch("/api/surveys/<sid>")
@login_required
def update_survey(sid):
    s = get_owned_survey(sid)
    data = request.get_json(silent=True) or {}
    update = {"updatedAt": now()}
    if "title" in data:
        title = str(data.get("title") or "").strip()
        if not (1 <= len(title) <= 100):
            raise ApiError("问卷标题长度需在 1~100 之间")
        update["title"] = title
    if "description" in data:
        update["description"] = str(data.get("description") or "").strip()[:2000]
    if "allowAnonymous" in data:
        update["allowAnonymous"] = bool(data.get("allowAnonymous"))
    if "allowMultipleSubmissions" in data:
        update["allowMultipleSubmissions"] = bool(data.get("allowMultipleSubmissions"))
    if "deadline" in data:
        update["deadline"] = parse_datetime(data.get("deadline"))
    get_db().surveys.update_one({"_id": s["_id"]}, {"$set": update})
    return jsonify({"updated": True})


def _validate_ready_to_collect(sid):
    """发布 / 继续收集前的共同校验：至少一道未删除题目 + 跳转图合法。"""
    qs = list(get_db().questions.find({"surveyId": sid, "deleted": False}).sort("order", 1))
    if not qs:
        raise ApiError("问卷至少需要一道题目才能开始收集", 409)
    _check_jump_graph(qs)


@bp.post("/api/surveys/<sid>/publish")
@login_required
def publish_survey(sid):
    s = get_owned_survey(sid)
    if s["status"] != "draft":
        raise ApiError(
            f"只有草稿状态的问卷才能发布（当前：{STATUS_LABELS.get(s['status'], s['status'])}）", 409
        )
    _validate_ready_to_collect(s["_id"])
    get_db().surveys.update_one(
        {"_id": s["_id"]}, {"$set": {"status": "published", "updatedAt": now()}}
    )
    return jsonify({"published": True, "shareUrl": f"/survey/{s['slug']}"})


@bp.post("/api/surveys/<sid>/pause")
@login_required
def pause_survey(sid):
    """暂停收集：填写端立即关闭，进入可编辑题目状态。"""
    s = get_owned_survey(sid)
    if s["status"] != "published":
        raise ApiError(
            f"只有收集中的问卷才能暂停（当前：{STATUS_LABELS.get(s['status'], s['status'])}）", 409
        )
    get_db().surveys.update_one(
        {"_id": s["_id"]}, {"$set": {"status": "paused", "updatedAt": now()}}
    )
    return jsonify({"paused": True})


@bp.post("/api/surveys/<sid>/resume")
@login_required
def resume_survey(sid):
    """继续收集：题目可能被改过，重新做发布级校验（题目数、跳转图）。"""
    s = get_owned_survey(sid)
    if s["status"] != "paused":
        raise ApiError(
            f"只有已暂停的问卷才能继续收集（当前：{STATUS_LABELS.get(s['status'], s['status'])}）", 409
        )
    _validate_ready_to_collect(s["_id"])
    get_db().surveys.update_one(
        {"_id": s["_id"]}, {"$set": {"status": "published", "updatedAt": now()}}
    )
    return jsonify({"resumed": True, "shareUrl": f"/survey/{s['slug']}"})


@bp.post("/api/surveys/<sid>/close")
@login_required
def close_survey(sid):
    s = get_owned_survey(sid)
    if s["status"] not in ("published", "paused"):
        raise ApiError("只有收集中的问卷才能关闭", 409)
    get_db().surveys.update_one(
        {"_id": s["_id"]}, {"$set": {"status": "closed", "updatedAt": now()}}
    )
    return jsonify({"closed": True})


@bp.delete("/api/surveys/<sid>")
@login_required
def delete_survey(sid):
    """删除问卷（聚合根级联硬删除）。

    题目与答卷只被所属问卷引用，整卷删除不会产生悬空引用；
    与"单题软删除"并不矛盾——那是保护仍在收集的问卷的历史，
    这里是问卷整体下线、统计数据也不再需要的场景。任何状态均可删除。
    """
    s = get_owned_survey(sid)
    db = get_db()
    q_n = db.questions.delete_many({"surveyId": s["_id"]}).deleted_count
    r_n = db.responses.delete_many({"surveyId": s["_id"]}).deleted_count
    db.surveys.delete_one({"_id": s["_id"]})
    return jsonify({
        "deleted": True,
        "questionsRemoved": q_n,
        "responsesRemoved": r_n,
    })
