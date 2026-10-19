"""题目管理：新增、编辑、软删除、排序（仅草稿状态可改题目）。"""
from bson import ObjectId
from flask import Blueprint, jsonify, request

from app.db import get_db
from app.errors import ApiError
from app.question_types import get_handler, validate_question_payload
from app.routes.surveys import get_owned_survey
from app.security import login_required
from app.utils import now, to_json

bp = Blueprint("questions", __name__)

EDITABLE_FIELDS = ("title", "required", "options", "config", "jumpRules")

# 草稿和已暂停状态可以编辑题目；收集中冻结（保证统计口径），已关闭终态不可改
EDITABLE_STATUSES = ("draft", "paused")

# 内置测试模板：4 道题覆盖全部题型与跳转（跳转目标用 @Tn 占位，导入时重映射）
DEMO_TEMPLATE = [
    {"type": "single_choice", "title": "你是否年满18岁", "required": True,
     "options": [{"key": "yes", "text": "是"}, {"key": "no", "text": "否"}],
     "jumpRules": [{"match": {"kind": "option_any", "optionKeys": ["no"]}, "goto": "@T3"}]},
    {"type": "number", "title": "你的年龄", "required": True,
     "config": {"min": 0, "max": 120, "integer": True}},
    {"type": "multi_choice", "title": "你喜欢的水果", "required": True,
     "options": [{"key": "apple", "text": "苹果"}, {"key": "banana", "text": "香蕉"},
                 {"key": "melon", "text": "西瓜"}, {"key": "grape", "text": "葡萄"}],
     "config": {"minSelect": 1, "maxSelect": 2}},
    {"type": "text", "title": "留言（选填）", "required": False,
     "config": {"minLength": 2, "maxLength": 100}},
]


def _get_writable_survey(sid):
    """收集中冻结题目（保证统计口径稳定）；暂停或草稿状态可编辑。"""
    s = get_owned_survey(sid)
    if s["status"] not in EDITABLE_STATUSES:
        raise ApiError("问卷正在收集中，请先「暂停收集」再修改题目", 409)
    return s


@bp.post("/api/surveys/<sid>/questions")
@login_required
def add_question(sid):
    _get_writable_survey(sid)
    payload = request.get_json(silent=True) or {}
    fields = validate_question_payload(payload)
    db = get_db()
    last = db.questions.find_one({"surveyId": ObjectId(sid)}, sort=[("order", -1)])
    order = (last["order"] if last else 0) + 1
    doc = {
        "surveyId": ObjectId(sid),
        "order": order,
        "deleted": False,
        "schemaVersion": 1,
        "createdAt": now(),
        "updatedAt": now(),
        **fields,
    }
    db.questions.insert_one(doc)
    return jsonify({"question": to_json(doc)}), 201


@bp.patch("/api/questions/<qid>")
@login_required
def update_question(qid):
    if not ObjectId.is_valid(qid):
        raise ApiError("题目不存在", 404)
    q = get_db().questions.find_one({"_id": ObjectId(qid)})
    if not q:
        raise ApiError("题目不存在", 404)
    _get_writable_survey(str(q["surveyId"]))
    payload = request.get_json(silent=True) or {}
    if "type" in payload and payload["type"] != q["type"]:
        raise ApiError("不能修改题目类型（会影响已有数据），请删除后新建", 409)
    merged = {
        "type": q["type"],
        "title": payload.get("title", q["title"]),
        "required": payload.get("required", q.get("required", False)),
        "options": payload.get("options", q.get("options")),
        "config": payload.get("config", q.get("config") or {}),
        "jumpRules": payload.get("jumpRules", q.get("jumpRules") or []),
    }
    fields = validate_question_payload(merged)
    get_db().questions.update_one(
        {"_id": q["_id"]}, {"$set": {**fields, "updatedAt": now()}}
    )
    updated = get_db().questions.find_one({"_id": q["_id"]})
    return jsonify({"question": to_json(updated)})


@bp.delete("/api/questions/<qid>")
@login_required
def delete_question(qid):
    if not ObjectId.is_valid(qid):
        raise ApiError("题目不存在", 404)
    q = get_db().questions.find_one({"_id": ObjectId(qid)})
    if not q:
        raise ApiError("题目不存在", 404)
    _get_writable_survey(str(q["surveyId"]))
    # 软删除：保留题目定义，历史答卷与统计仍可解释（设计文档 D8）
    get_db().questions.update_one(
        {"_id": q["_id"]}, {"$set": {"deleted": True, "updatedAt": now()}}
    )
    return jsonify({"deleted": True})


@bp.post("/api/surveys/<sid>/questions/order")
@login_required
def reorder_questions(sid):
    _get_writable_survey(sid)
    data = request.get_json(silent=True) or {}
    ids = data.get("questionIds")
    if not isinstance(ids, list):
        raise ApiError("questionIds 格式不正确")
    db = get_db()
    current = [
        str(q["_id"])
        for q in db.questions.find({"surveyId": ObjectId(sid), "deleted": False})
    ]
    if sorted(ids) != sorted(current):
        raise ApiError("questionIds 与当前题目不一致")
    from pymongo import UpdateOne
    ops = [
        UpdateOne({"_id": ObjectId(qid)}, {"$set": {"order": i + 1, "updatedAt": now()}})
        for i, qid in enumerate(ids)
    ]
    if ops:
        db.questions.bulk_write(ops)
    return jsonify({"reordered": True})


@bp.post("/api/surveys/<sid>/questions/import")
@login_required
def import_questions(sid):
    """导入题目模板：默认内置测试模板；传 sourceSurveyId 则从自己的另一份问卷复制。
    题目追加到末尾，跳转规则的目标题目 id 重映射到新题目。"""
    _get_writable_survey(sid)
    data = request.get_json(silent=True) or {}
    db = get_db()
    source_survey_id = data.get("sourceSurveyId")
    if source_survey_id:
        src = get_owned_survey(source_survey_id)  # 非本人的问卷直接 403
        specs = list(db.questions.find(
            {"surveyId": src["_id"], "deleted": False}).sort("order", 1))
        if not specs:
            raise ApiError("来源问卷没有可导入的题目", 400)
    else:
        specs = DEMO_TEMPLATE

    last = db.questions.find_one({"surveyId": ObjectId(sid)}, sort=[("order", -1)])
    order = (last["order"] if last else 0) + 1

    created = []
    for spec in specs:
        # 占位跳转目标（@Tn / 来源旧 id）不能通过格式校验，先按无规则校验入库
        fields = validate_question_payload({**spec, "jumpRules": []})
        doc = {
            "surveyId": ObjectId(sid),
            "order": order,
            "deleted": False,
            "schemaVersion": 1,
            "createdAt": now(),
            "updatedAt": now(),
            **fields,
            "jumpRules": [],
        }
        db.questions.insert_one(doc)
        created.append((doc, spec.get("jumpRules") or []))
        order += 1

    # 重映射跳转目标：来源标识（@Tn 或来源题目 id）-> 新题目 id
    goto_map = {}
    for i, (doc, _) in enumerate(created):
        src_key = str(specs[i]["_id"]) if source_survey_id else f"@T{i + 1}"
        goto_map[src_key] = str(doc["_id"])

    for doc, rules in created:
        if not rules:
            continue
        remapped = []
        for r in rules:
            goto = r.get("goto")
            if goto != "end":
                # 映射不到的悬空目标原样保留，发布/继续收集校验时会明确报错
                goto = goto_map.get(goto, goto)
            remapped.append({"match": r["match"], "goto": goto})
        db.questions.update_one(
            {"_id": doc["_id"]}, {"$set": {"jumpRules": remapped, "updatedAt": now()}}
        )

    docs = [db.questions.find_one({"_id": doc["_id"]}) for doc, _ in created]
    return jsonify({"imported": len(docs), "questions": to_json(docs)}), 201
