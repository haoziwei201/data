"""题库（题目资产）：保存复用、共享、版本链、使用反查、跨问卷统计。

第二阶段核心模型：题目 =「身份（question_bank 文档）+ 内嵌版本链」；
问卷通过 questions 集合中的实例引用某个版本的内容快照。
题库文档内嵌 versions 数组：版本链是题库页/版本历史页的一次性读取单位，
且版本数由人工编辑产生、天然有界（与"答卷不可嵌入"的无界数组不同）。
"""
from bson import ObjectId
from flask import Blueprint, g, jsonify, request

from app.db import get_db
from app.errors import ApiError
from app.question_types import validate_question_payload
from app.security import login_required
from app.utils import is_number, now, to_json

bp = Blueprint("bank", __name__)

STATUS_LABELS = {"draft": "草稿", "published": "收集中", "paused": "已暂停", "closed": "已关闭"}


# ---------------- 身份与版本的基础操作（供 questions 路由复用） ----------------

def create_identity(owner_id, payload):
    """创建题目身份（题库条目），v1 即初始内容。跳转规则属于问卷编排，不入题库。"""
    fields = validate_question_payload({**payload, "jumpRules": []})
    content = {k: v for k, v in fields.items() if k != "jumpRules"}
    doc = {
        "ownerId": ObjectId(owner_id),
        "sharedWith": [],
        "versions": [{
            "version": 1, "basedOn": None,
            "editorId": ObjectId(owner_id), "createdAt": now(),
            "content": content,
        }],
        "currentVersion": 1,
        "deleted": False,
        "createdAt": now(),
        "updatedAt": now(),
    }
    get_db().question_bank.insert_one(doc)
    return doc


def version_content(bank_doc, version):
    """取指定版本的内容；不存在则抛 404。"""
    for v in bank_doc["versions"]:
        if v["version"] == version:
            return v["content"]
    raise ApiError(f"版本 v{version} 不存在", 404)


def append_version(bank_doc, payload, editor_id, based_on=None):
    """追加新版本（编辑 / 恢复 / 存入题库共用）。历史版本永不改写（需求四）。"""
    if based_on is None:
        based_on = bank_doc["currentVersion"]
    base = version_content(bank_doc, based_on)
    if "type" in payload and payload["type"] != base["type"]:
        raise ApiError("不能修改题目类型（会影响已有数据），请创建新题", 409)
    merged = dict(base)
    for k in ("type", "title", "required", "options", "config"):
        if k in payload and payload[k] is not None:
            merged[k] = payload[k]
    fields = validate_question_payload({**merged, "jumpRules": []})
    content = {k: v for k, v in fields.items() if k != "jumpRules"}
    ver = bank_doc["currentVersion"] + 1
    get_db().question_bank.update_one(
        {"_id": bank_doc["_id"]},
        {"$push": {"versions": {
            "version": ver, "basedOn": based_on,
            "editorId": ObjectId(editor_id), "createdAt": now(),
            "content": content,
        }},
         "$set": {"currentVersion": ver, "updatedAt": now()}}
    )
    return ver


def get_bank_question(root_id, *, writable=False):
    """按身份 id 取题库条目。所有者与被共享者可读；写操作仅所有者。"""
    if not ObjectId.is_valid(root_id):
        raise ApiError("题目不存在", 404)
    b = get_db().question_bank.find_one({"_id": ObjectId(root_id)})
    if not b or b.get("deleted"):
        raise ApiError("题目不存在", 404)
    uid = ObjectId(g.user["id"])
    if b["ownerId"] != uid:
        if writable or uid not in b.get("sharedWith", []):
            raise ApiError("无权访问该题目" if not writable else "只有题目所有者可以执行此操作", 403)
    return b


def build_usage(root_id):
    """题目被哪些问卷使用（需求六）：按问卷归并，标注各自使用的版本。"""
    db = get_db()
    by_survey = {}
    for inst in db.questions.find({"rootId": root_id}):
        by_survey.setdefault(inst["surveyId"], []).append(inst)
    out = []
    for sid, group in by_survey.items():
        s = db.surveys.find_one({"_id": sid}, {"title": 1, "status": 1, "ownerId": 1})
        if not s:
            continue
        owner = db.users.find_one({"_id": s["ownerId"]}, {"username": 1})
        active = [i for i in group if not i.get("deleted")]
        out.append({
            "surveyId": str(sid),
            "surveyTitle": s["title"],
            "surveyStatus": s.get("status"),
            "surveyStatusLabel": STATUS_LABELS.get(s.get("status"), s.get("status")),
            "surveyOwner": owner["username"] if owner else "（已注销）",
            "active": bool(active),
            "versionsInUse": sorted({i.get("bankVersion") or 1 for i in active}) if active else [],
            "allVersions": sorted({i.get("bankVersion") or 1 for i in group}),
        })
    out.sort(key=lambda x: x["surveyTitle"])
    return out


def _bank_summary(b, uid):
    """题库列表行：身份 + 当前版本内容摘要 + 使用问卷数。"""
    cur = version_content(b, b["currentVersion"])
    return {
        "id": str(b["_id"]),
        "title": cur["title"],
        "type": cur["type"],
        "required": cur.get("required", False),
        "options": cur.get("options"),
        "config": cur.get("config"),
        "currentVersion": b["currentVersion"],
        "totalVersions": len(b["versions"]),
        "sharedWithCount": len(b.get("sharedWith", [])),
        "mine": b["ownerId"] == uid,
        "ownerName": None,  # 列表接口按 mine 分组填充
        "createdAt": b.get("createdAt"),
        "updatedAt": b.get("updatedAt"),
    }


# ---------------- 题库管理（需求一 / 七） ----------------

@bp.post("/api/bank/questions")
@login_required
def create_bank_question():
    """在题库中直接创建题目（不挂任何问卷）。"""
    payload = request.get_json(silent=True) or {}
    doc = create_identity(g.user["id"], payload)
    return jsonify({"question": _bank_summary(doc, ObjectId(g.user["id"]))}), 201


@bp.get("/api/bank/questions")
@login_required
def list_bank_questions():
    """scope=mine：我的题库；scope=shared：共享给我的。"""
    scope = request.args.get("scope", "mine")
    uid = ObjectId(g.user["id"])
    if scope == "shared":
        docs = list(get_db().question_bank.find(
            {"sharedWith": uid, "deleted": False}).sort("updatedAt", -1))
    else:
        docs = list(get_db().question_bank.find(
            {"ownerId": uid, "deleted": False}).sort("updatedAt", -1))
    return jsonify({"questions": [
        {**_bank_summary(b, uid), "ownerName": _owner_name(b)} for b in docs
    ]})


def _owner_name(b):
    u = get_db().users.find_one({"_id": b["ownerId"]}, {"username": 1})
    return u["username"] if u else "（已注销）"


@bp.get("/api/bank/questions/<root_id>")
@login_required
def bank_question_detail(root_id):
    """题目详情：版本链 + 使用情况（所有者与被共享者可见）。"""
    b = get_bank_question(root_id)
    out = _bank_summary(b, ObjectId(g.user["id"]))
    out["ownerName"] = _owner_name(b)
    out["sharedWith"] = [
        {"id": str(uid), "username": _username(uid)} for uid in b.get("sharedWith", [])
    ]
    out["versions"] = [{
        "version": v["version"],
        "basedOn": v.get("basedOn"),
        "createdAt": v.get("createdAt"),
        "content": to_json(v["content"]),
    } for v in b["versions"]]
    out["usage"] = build_usage(b["_id"])
    return jsonify({"question": to_json(out)})


def _username(uid):
    u = get_db().users.find_one({"_id": uid}, {"username": 1})
    return u["username"] if u else "（已注销）"


@bp.get("/api/bank/questions/<root_id>/usage")
@login_required
def bank_question_usage(root_id):
    """题目被哪些问卷使用（需求六）：所有者与被共享者可查。"""
    b = get_bank_question(root_id)
    return jsonify({"usage": build_usage(b["_id"])})


@bp.patch("/api/bank/questions/<root_id>")
@login_required
def edit_bank_question(root_id):
    """编辑题库题目：基于当前版本合并修改，追加为新版本（需求四、五）。"""
    b = get_bank_question(root_id, writable=True)
    payload = request.get_json(silent=True) or {}
    if "type" in payload and payload["type"] != version_content(b, b["currentVersion"])["type"]:
        raise ApiError("不能修改题目类型（会影响已有数据），请创建新题", 409)
    ver = append_version(b, payload, g.user["id"])
    return jsonify({"version": ver})


@bp.post("/api/bank/questions/<root_id>/restore")
@login_required
def restore_version(root_id):
    """恢复旧版本：以旧版内容创建新版本，历史保持完整（需求四）。"""
    b = get_bank_question(root_id, writable=True)
    data = request.get_json(silent=True) or {}
    ver = data.get("version")
    if not isinstance(ver, int):
        raise ApiError("version 必须是整数")
    old = version_content(b, ver)  # 校验版本存在
    new_ver = append_version(b, old, g.user["id"], based_on=ver)
    return jsonify({"restoredFrom": ver, "version": new_ver})


@bp.delete("/api/bank/questions/<root_id>")
@login_required
def delete_bank_question(root_id):
    """从题库移除（身份软删）。问卷中的实例保存了内容快照，不受影响。"""
    b = get_bank_question(root_id, writable=True)
    get_db().question_bank.update_one(
        {"_id": b["_id"]}, {"$set": {"deleted": True, "updatedAt": now()}}
    )
    return jsonify({"deleted": True})


# ---------------- 共享（需求二） ----------------

@bp.post("/api/bank/questions/<root_id>/share")
@login_required
def share_question(root_id):
    """把题目共享给指定用户（按用户名）。"""
    b = get_bank_question(root_id, writable=True)
    data = request.get_json(silent=True) or {}
    username = str(data.get("username") or "").strip()
    u = get_db().users.find_one({"username": username}, {"username": 1})
    if not u:
        raise ApiError("用户不存在", 404)
    if u["_id"] == b["ownerId"]:
        raise ApiError("不能共享给自己", 400)
    if u["_id"] not in b.get("sharedWith", []):
        get_db().question_bank.update_one(
            {"_id": b["_id"]},
            {"$addToSet": {"sharedWith": u["_id"]}, "$set": {"updatedAt": now()}}
        )
    return jsonify({"sharedWith": [_username(uid) for uid in
                                   get_db().question_bank.find_one({"_id": b["_id"]})["sharedWith"]]})


@bp.post("/api/bank/questions/<root_id>/unshare")
@login_required
def unshare_question(root_id):
    b = get_bank_question(root_id, writable=True)
    data = request.get_json(silent=True) or {}
    username = str(data.get("username") or "").strip()
    u = get_db().users.find_one({"username": username}, {"username": 1})
    if not u:
        raise ApiError("用户不存在", 404)
    get_db().question_bank.update_one(
        {"_id": b["_id"]},
        {"$pull": {"sharedWith": u["_id"]}, "$set": {"updatedAt": now()}}
    )
    return jsonify({"sharedWith": [_username(uid) for uid in
                                   get_db().question_bank.find_one({"_id": b["_id"]})["sharedWith"]]})


# ---------------- 跨问卷统计（需求八） ----------------

@bp.get("/api/bank/questions/<root_id>/stats")
@login_required
def cross_survey_stats(root_id):
    """某道题在所有问卷中的回答情况：总体分布 + 按问卷分解。"""
    b = get_bank_question(root_id)
    db = get_db()
    insts = list(db.questions.find({"rootId": b["_id"]}))
    content = version_content(b, b["currentVersion"])
    out = {
        "question": {
            "id": str(b["_id"]),
            "title": content["title"],
            "type": content["type"],
            "required": content.get("required", False),
            "options": content.get("options"),
            "config": content.get("config"),
            "currentVersion": b["currentVersion"],
        },
        "surveys": build_usage(b["_id"]),
    }
    if not insts:
        return jsonify({**out, "totalResponses": 0, "answers": None, "perSurvey": []})

    inst_ids = [i["_id"] for i in insts]
    survey_ids = list({i["surveyId"] for i in insts})
    inst_survey = {str(i["_id"]): i["surveyId"] for i in insts}
    survey_title = {}
    for sid in survey_ids:
        s = db.surveys.find_one({"_id": sid}, {"title": 1})
        survey_title[str(sid)] = s["title"] if s else "（已删除）"

    p_counts = [
        {"$match": {"surveyId": {"$in": survey_ids}}},
        {"$unwind": "$answers"},
        {"$match": {"answers.questionId": {"$in": inst_ids}}},
        {"$unwind": "$answers.value"},
        {"$group": {"_id": {"q": "$answers.questionId", "v": "$answers.value"}, "n": {"$sum": 1}}},
    ]
    p_values = [
        {"$match": {"surveyId": {"$in": survey_ids}}},
        {"$unwind": "$answers"},
        {"$match": {"answers.questionId": {"$in": inst_ids}}},
        {"$group": {"_id": "$answers.questionId", "values": {"$push": "$answers.value"}}},
    ]
    counts = {(str(r["_id"]["q"]), r["_id"]["v"]): r["n"]
              for r in db.responses.aggregate(p_counts)}
    values_map = {str(r["_id"]): r["values"] for r in db.responses.aggregate(p_values)}

    def _qids_for(sid):
        return [str(i["_id"]) for i in insts if i["surveyId"] == sid]

    def _answer_block(qids):
        block = {"answerCount": 0, "options": None, "values": None,
                 "average": None, "min": None, "max": None}
        if content["type"] in ("single_choice", "multi_choice"):
            rows = [{"key": o["key"], "text": o["text"],
                     "count": sum(n for (q, v), n in counts.items() if q in qids and v == o["key"])}
                    for o in content.get("options") or []]
            known = {o["key"] for o in content.get("options") or []}
            orphan = sum(n for (q, v), n in counts.items() if q in qids and v not in known)
            if orphan:
                rows.append({"key": "__removed__", "text": "（已删除的选项）", "count": orphan})
            block["options"] = rows
        else:
            vals = [v for qid in qids for v in values_map.get(qid, [])]
            block["values"] = vals
            if content["type"] == "number":
                nums = [v for v in vals if is_number(v)]
                if nums:
                    avg = round(sum(nums) / len(nums), 2)
                    block["average"] = int(avg) if avg == int(avg) else avg
                    block["min"] = min(nums)
                    block["max"] = max(nums)
        block["answerCount"] = sum(len(values_map.get(qid, [])) for qid in qids)
        return block

    all_qids = [str(i["_id"]) for i in insts]
    return jsonify({
        **out,
        "totalResponses": db.responses.count_documents({"surveyId": {"$in": survey_ids}}),
        "answers": _answer_block(all_qids),
        "perSurvey": [
            {"surveyId": str(sid), "surveyTitle": survey_title[str(sid)],
             "answers": _answer_block(_qids_for(sid))}
            for sid in survey_ids
        ],
    })
