"""填写端：打开问卷、逐题导航（服务端解释跳转）、提交答卷。"""
from bson import ObjectId
from flask import Blueprint, g, jsonify, request

from app.db import get_db
from app.errors import ApiError
from app.jump import next_destination
from app.question_types import get_handler
from app.utils import now, question_public, to_json

bp = Blueprint("fill", __name__)


def _load_for_fill(slug):
    s = get_db().surveys.find_one({"slug": slug})
    if not s:
        raise ApiError("问卷不存在", 404)
    if s["status"] == "paused":
        raise ApiError("问卷已暂停收集，暂时无法填写", 403)
    if s["status"] != "published":
        raise ApiError("问卷未发布或已关闭", 403)
    if s.get("deadline") and s["deadline"] < now():
        raise ApiError("问卷已过截止时间，无法填写", 403)
    return s


def _load_questions(sid):
    return list(get_db().questions.find({"surveyId": sid, "deleted": False}).sort("order", 1))


def _normalize_answers(data):
    """兼容 {qid: value} 与 [{questionId, value}] 两种形态，统一成 dict。"""
    raw = data.get("answers")
    out = {}
    if isinstance(raw, dict):
        items = raw.items()
    elif isinstance(raw, list):
        items = ((a.get("questionId"), a.get("value")) for a in raw if isinstance(a, dict))
    else:
        raise ApiError("answers 格式不正确")
    for qid, v in items:
        if not isinstance(qid, str) or not ObjectId.is_valid(qid):
            raise ApiError("题目 ID 不正确")
        out[qid] = v
    return out


def _validate_answers(qs, answers):
    qmap = {str(q["_id"]): q for q in qs}
    for qid, v in answers.items():
        q = qmap.get(qid)
        if q is None:
            raise ApiError("包含了不属于本问卷的题目", 400)
        err = get_handler(q["type"]).check_answer(q, v)
        if err:
            raise ApiError(f"第{q['order']}题（{q['title']}）：{err}", 400)


def _walk(qs, answers, stop_at_unanswered):
    """按跳转规则回放答题路径。

    stop_at_unanswered=True：返回 (第一个未答的题目|None, 途经题目ID列表)，
        供逐题导航使用；
    stop_at_unanswered=False：走完整个路径并校验必答，
        返回 (有序题目ID列表, 是否提前到达 end)。
    """
    pos = {str(q["_id"]): i for i, q in enumerate(qs)}
    seen, path_ids = set(), []
    i, ended = 0, False
    while 0 <= i < len(qs):
        q = qs[i]
        qid = str(q["_id"])
        if qid in seen:  # 防御：发布时已查环，正常不会走到
            ended = True
            break
        seen.add(qid)
        path_ids.append(qid)
        if stop_at_unanswered and qid not in answers:
            return q, path_ids
        if qid not in answers:
            if q.get("required"):
                raise ApiError(f"第{q['order']}题（{q['title']}）为必答题", 400)
            value = None
        else:
            value = answers[qid]
        dest = next_destination(q, value)
        if dest == "end":
            ended = True
            break
        if dest is None:
            i += 1
        else:
            j = pos.get(dest)
            i = j if j is not None else len(qs)
    return (None, path_ids) if stop_at_unanswered else (path_ids, ended)


@bp.get("/api/fill/<slug>")
def open_survey(slug):
    s = _load_for_fill(slug)
    if not s["allowAnonymous"] and not g.get("user"):
        raise ApiError("该问卷需要登录后填写", 401)
    qcount = get_db().questions.count_documents({"surveyId": s["_id"], "deleted": False})
    return jsonify({
        "survey": {
            "id": str(s["_id"]),
            "title": s["title"],
            "description": s.get("description", ""),
            "deadline": to_json(s.get("deadline")),
            "allowAnonymous": s["allowAnonymous"],
            "allowMultipleSubmissions": s["allowMultipleSubmissions"],
        },
        "questionCount": qcount,
        "loggedIn": bool(g.get("user")),
    })


@bp.post("/api/fill/<slug>/step")
def step(slug):
    """逐题导航：提交当前已答的答案，服务端回放路径并返回下一道应答的题。"""
    s = _load_for_fill(slug)
    if not s["allowAnonymous"] and not g.get("user"):
        raise ApiError("该问卷需要登录后填写", 401)
    answers = _normalize_answers(request.get_json(silent=True) or {})
    qs = _load_questions(s["_id"])
    if not qs:
        raise ApiError("问卷没有题目", 400)
    _validate_answers(qs, answers)
    frontier, _ = _walk(qs, answers, stop_at_unanswered=True)
    return jsonify({
        "done": frontier is None,
        "question": question_public(frontier) if frontier is not None else None,
    })


@bp.post("/api/fill/<slug>/submit")
def submit(slug):
    s = _load_for_fill(slug)
    if not s["allowAnonymous"] and not g.get("user"):
        raise ApiError("该问卷需要登录后填写", 401)
    answers = _normalize_answers(request.get_json(silent=True) or {})
    qs = _load_questions(s["_id"])
    if not qs:
        raise ApiError("问卷没有题目", 400)
    _validate_answers(qs, answers)
    path_ids, _ = _walk(qs, answers, stop_at_unanswered=False)

    db = get_db()
    if g.get("user") and not s["allowMultipleSubmissions"]:
        existed = db.responses.find_one({
            "surveyId": s["_id"],
            "respondent.userId": ObjectId(g.user["id"]),
        })
        if existed:
            raise ApiError("您已填写过该问卷，不能重复提交", 409)

    qmap = {str(q["_id"]): q for q in qs}
    stored = [
        {
            "questionId": ObjectId(qid),
            "value": get_handler(qmap[qid]["type"]).normalize_value(qmap[qid], answers[qid]),
        }
        for qid in path_ids
        if qid in answers
    ]
    doc = {
        "surveyId": s["_id"],
        "respondent": {
            "userId": ObjectId(g.user["id"]) if g.get("user") else None,
            "anonymous": not g.get("user"),
        },
        "submittedAt": now(),
        "answers": stored,
    }
    result = db.responses.insert_one(doc)
    return jsonify({"responseId": str(result.inserted_id),
                    "submittedAt": to_json(doc["submittedAt"])}), 201
