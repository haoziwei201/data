"""题目管理：新增、编辑、软删除、排序、题库选题（第二阶段核心改造点）。

模型：questions 集合中的文档是「问卷内题目实例」，持有完整内容快照
（type/title/options/config）+ 血统字段（rootId 指向题库身份、bankVersion
表示内容源自题库第几版、basedOn 指向本卷内被替换的前一实例）。

编辑策略（需求三，copy-on-write）：
- 草稿且无答卷的问卷 → 实例原地更新（第一阶段行为）；
- 已发布过或已有答卷的问卷 → 生成分叉实例接管题位，旧实例软删退役，
  历史答卷仍指向旧实例，统计按 rootId 归并，口径不乱。
"""
from bson import ObjectId
from flask import Blueprint, g, jsonify, request

from app.db import get_db
from app.errors import ApiError
from app.question_types import validate_question_payload
from app.routes.bank import (
    append_version,
    create_identity,
    get_bank_question,
    version_content,
)
from app.routes.surveys import get_owned_survey
from app.security import login_required
from app.utils import now, to_json

bp = Blueprint("questions", __name__)

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


def _is_frozen(survey):
    """已发布过（非草稿）或已有答卷 → 实例内容冻结，修改必须走分叉。
    第一阶段允许「暂停后原地改题」，第二阶段需求三明确禁止影响旧数据，
    这是唯一的行为修正点（详见设计文档 D12）。"""
    if survey["status"] != "draft":
        return True
    return get_db().responses.count_documents({"surveyId": survey["_id"]}) > 0


def _get_instance(qid, survey_id=None):
    if not ObjectId.is_valid(qid):
        raise ApiError("题目不存在", 404)
    match = {"_id": ObjectId(qid)}
    if survey_id is not None:
        match["surveyId"] = ObjectId(survey_id)
    q = get_db().questions.find_one(match)
    if not q:
        raise ApiError("题目不存在", 404)
    return q


def _next_order(survey_id):
    last = get_db().questions.find_one({"surveyId": survey_id}, sort=[("order", -1)])
    return (last["order"] if last else 0) + 1


def _insert_instance(survey_id, fields, root_id, bank_version, order):
    doc = {
        "surveyId": survey_id,
        "rootId": root_id,
        "bankVersion": bank_version,
        "basedOn": None,
        "deleted": False,
        "schemaVersion": 2,
        "createdAt": now(),
        "updatedAt": now(),
        **fields,
    }
    if order is not None:
        doc["order"] = order
    get_db().questions.insert_one(doc)
    return doc


def _fork_instance(old, fields, bank_version=None):
    """分叉：新实例接管同题位（order 不变），旧实例软删退役；
    同卷其他题目的跳转目标从旧实例改指新实例。
    历史答卷仍指向旧实例（内容不可变），统计按 rootId 归并。"""
    db = get_db()
    doc = {
        "surveyId": old["surveyId"],
        "rootId": old["rootId"],
        "bankVersion": bank_version if bank_version is not None else old.get("bankVersion", 1),
        "basedOn": old["_id"],
        "order": old["order"],
        "deleted": False,
        "schemaVersion": 2,
        "createdAt": now(),
        "updatedAt": now(),
        **fields,
    }
    db.questions.insert_one(doc)
    db.questions.update_one(
        {"_id": old["_id"]}, {"$set": {"deleted": True, "updatedAt": now()}}
    )
    # 跳转目标重映射：旧实例 id -> 新实例 id（仅本卷活跃实例）
    for other in db.questions.find({"surveyId": old["surveyId"], "deleted": False}):
        rules = other.get("jumpRules") or []
        changed = False
        for r in rules:
            if r.get("goto") == str(old["_id"]):
                r["goto"] = str(doc["_id"])
                changed = True
        if changed:
            db.questions.update_one(
                {"_id": other["_id"]},
                {"$set": {"jumpRules": rules, "updatedAt": now()}}
            )
    return doc


def _check_unique_in_survey(survey_id, root_id):
    """同一问卷内同一题目身份只能有一个活跃实例（统计按 rootId 归并的前提）。"""
    existed = get_db().questions.find_one({
        "surveyId": survey_id, "rootId": root_id, "deleted": False})
    if existed:
        raise ApiError("该题目已在问卷中（同一问卷不能重复使用同一道题）", 409)


# ---------------- 问卷内建题 / 编辑 / 删除 / 排序 ----------------

@bp.post("/api/surveys/<sid>/questions")
@login_required
def add_question(sid):
    """问卷内新建题目。第二阶段需求调整：不再自动加入题库（rootId 为空），
    由用户在编辑页显式「存入题库」后才成为可复用的题目资产。"""
    _get_writable_survey(sid)
    payload = request.get_json(silent=True) or {}
    fields = validate_question_payload(payload)
    doc = _insert_instance(ObjectId(sid), fields, None, None,
                           _next_order(ObjectId(sid)))
    return jsonify({"question": to_json(doc)}), 201


@bp.patch("/api/questions/<qid>")
@login_required
def update_question(qid):
    q = _get_instance(qid)
    s = _get_writable_survey(str(q["surveyId"]))
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
    if _is_frozen(s):
        # 需求三：已发布问卷的题目内容不可变 → 生成分叉，旧实例退役
        doc = _fork_instance(q, fields)
    else:
        get_db().questions.update_one(
            {"_id": q["_id"]}, {"$set": {**fields, "updatedAt": now()}}
        )
        doc = get_db().questions.find_one({"_id": q["_id"]})
    return jsonify({"question": to_json(doc)})


@bp.delete("/api/questions/<qid>")
@login_required
def delete_question(qid):
    q = _get_instance(qid)
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


# ---------------- 题库选题 / 存回题库 / 升级版本（第二阶段新增） ----------------

@bp.post("/api/surveys/<sid>/questions/from-bank")
@login_required
def add_from_bank(sid):
    """从题库选题入卷（需求一、二）：引用题目身份的指定版本（默认最新）。
    可用范围 = 自己的题库 + 共享给我的题目。"""
    _get_writable_survey(sid)
    data = request.get_json(silent=True) or {}
    root_id = data.get("rootId")
    if not root_id:
        raise ApiError("rootId 不能为空")
    b = get_bank_question(root_id)  # 所有者或被共享者可读
    ver = data.get("version")
    if ver is None:
        ver = b["currentVersion"]
    if not isinstance(ver, int):
        raise ApiError("version 必须是整数")
    content = version_content(b, ver)
    survey_id = ObjectId(sid)
    _check_unique_in_survey(survey_id, b["_id"])
    # 入卷实例保存内容快照；跳转规则属于问卷编排，从空开始配置
    fields = validate_question_payload({**content, "jumpRules": []})
    doc = _insert_instance(survey_id, fields, b["_id"], ver, _next_order(survey_id))
    return jsonify({"question": to_json(doc)}), 201


@bp.post("/api/surveys/<sid>/questions/<qid>/save-to-bank")
@login_required
def save_to_bank(sid, qid):
    """把问卷内题目存入题库（需求一：用户自行决定入库）。
    - 未入库题目（rootId 为空，第二阶段调整后建题的默认形态）：
      为当前用户创建题目身份，实例内容即 v1，实例换绑到该身份；
    - 已入库题目：当前内容追加为新版本（仅身份所有者可存回）。
    共享使用者对自己卷内的快照无写题库权限（403）。"""
    get_owned_survey(sid)
    q = _get_instance(qid, sid)
    db = get_db()
    payload = {k: q.get(k) for k in ("type", "title", "required", "options", "config")}

    if not q.get("rootId"):
        bank_doc = create_identity(g.user["id"], payload)
        db.questions.update_one(
            {"_id": q["_id"]},
            {"$set": {"rootId": bank_doc["_id"], "bankVersion": 1,
                      "updatedAt": now()}}
        )
        return jsonify({"savedVersion": 1, "rootId": str(bank_doc["_id"])})

    b = db.question_bank.find_one({"_id": q["rootId"]})
    if not b or b.get("deleted"):
        raise ApiError("题目身份不存在（题库数据缺失）", 404)
    if b["ownerId"] != ObjectId(g.user["id"]):
        raise ApiError("只有题目所有者可以存回题库；共享使用者可在自己的卷内修改快照", 403)
    ver = append_version(b, payload, g.user["id"])
    # 实例内容与题库新版本一致，更新其来源标记
    db.questions.update_one(
        {"_id": q["_id"]}, {"$set": {"bankVersion": ver, "updatedAt": now()}}
    )
    return jsonify({"savedVersion": ver, "rootId": str(b["_id"])})


@bp.post("/api/surveys/<sid>/questions/<qid>/upgrade")
@login_required
def upgrade_question(sid, qid):
    """把问卷内题目切换到题库的指定版本（需求五：多版本共存，可选任意版本）。
    body 传 {"version": n}，缺省为题库当前最新版。
    草稿问卷原地更新；冻结问卷生成分叉（与手动编辑同一策略）。"""
    s = _get_writable_survey(sid)
    q = _get_instance(qid, sid)
    if not q.get("rootId"):
        raise ApiError("该题目尚未加入题库，请先「存入题库」", 409)
    b = get_bank_question(str(q["rootId"]))
    data = request.get_json(silent=True) or {}
    target = data.get("version")
    if target is None:
        target = b["currentVersion"]
    if not isinstance(target, int):
        raise ApiError("version 必须是整数")
    content = version_content(b, target)  # 版本不存在则 404
    if q.get("bankVersion") == target:
        raise ApiError(f"已在使用版本 v{target}", 409)
    if content["type"] != q["type"]:
        raise ApiError("目标版本的题型与问卷内不一致，无法切换", 409)
    fields = validate_question_payload(
        {**content, "jumpRules": q.get("jumpRules") or []})
    if _is_frozen(s):
        doc = _fork_instance(q, fields, bank_version=target)
    else:
        update = {k: v for k, v in fields.items() if k != "jumpRules"}
        get_db().questions.update_one(
            {"_id": q["_id"]},
            {"$set": {**update, "bankVersion": target, "updatedAt": now()}}
        )
        doc = get_db().questions.find_one({"_id": q["_id"]})
    return jsonify({"question": to_json(doc), "upgradedTo": target})


# ---------------- 模板导入（第一阶段功能保留，身份复用增强） ----------------

@bp.post("/api/surveys/<sid>/questions/import")
@login_required
def import_questions(sid):
    """导入题目模板：默认内置测试模板；传 sourceSurveyId 则从自己的另一份问卷复制。
    题目追加到末尾，跳转规则的目标题目 id 重映射到新题目。
    第二阶段：来源题目已入库时复用其题目身份（同身份去重跳过）；
    未入库的题目按普通内容复制（不自动入库，与建题口径一致）。"""
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

    order = _next_order(ObjectId(sid))
    created = []
    skipped = 0
    for spec in specs:
        root_id = spec.get("rootId") if source_survey_id else None
        if root_id:
            # 已入库的来源题：复用题目身份；同一身份在本卷已有活跃实例则跳过（去重）
            dup = db.questions.find_one({
                "surveyId": ObjectId(sid), "rootId": root_id, "deleted": False})
            if dup:
                skipped += 1
                continue
        # 占位跳转目标（@Tn / 来源旧 id）不能通过格式校验，先按无规则校验入库
        fields = validate_question_payload({**spec, "jumpRules": []})
        doc = _insert_instance(ObjectId(sid),
                               {**fields, "jumpRules": []},
                               root_id, spec.get("bankVersion", 1) if root_id else None,
                               order)
        src_key = str(spec["_id"]) if source_survey_id else f"@T{order}"
        created.append((doc, spec.get("jumpRules") or [], src_key))
        order += 1

    # 重映射跳转目标：来源标识（@Tn 或来源题目 id）-> 新题目 id
    goto_map = {src_key: str(doc["_id"]) for doc, _, src_key in created}

    for doc, rules, _ in created:
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

    docs = [db.questions.find_one({"_id": doc["_id"]}) for doc, _, _ in created]
    return jsonify({"imported": len(docs), "skipped": skipped,
                    "questions": to_json(docs)}), 201
