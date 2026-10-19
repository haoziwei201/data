"""统计：聚合管道出裸数字，应用层合并题目定义出带标签的报表（设计文档第六节）。"""
from bson import ObjectId
from flask import Blueprint, jsonify, request

from app.db import get_db
from app.routes.surveys import get_owned_survey
from app.security import login_required
from app.utils import is_number, to_json

bp = Blueprint("stats", __name__)


@bp.get("/api/surveys/<sid>/stats")
@login_required
def survey_stats(sid):
    s = get_owned_survey(sid)
    qid_filter = request.args.get("questionId")
    if qid_filter is not None and not ObjectId.is_valid(qid_filter):
        raise ApiError("questionId 不正确", 400)

    db = get_db()
    match = {"surveyId": s["_id"]}

    # A. 每个取值被选次数（多选数组经 $unwind 展开，标量原样通过）
    p_counts = [
        {"$match": match},
        {"$unwind": "$answers"},
        {"$unwind": "$answers.value"},
        {"$group": {"_id": {"q": "$answers.questionId", "v": "$answers.value"}, "n": {"$sum": 1}}},
    ]
    # B. 每题的全部填写内容（文本/数字题用）
    p_values = [
        {"$match": match},
        {"$unwind": "$answers"},
        {"$group": {"_id": "$answers.questionId", "values": {"$push": "$answers.value"}}},
    ]
    # C. 每题的回答人数
    p_answered = [
        {"$match": match},
        {"$unwind": "$answers"},
        {"$group": {"_id": "$answers.questionId", "n": {"$sum": 1}}},
    ]
    if qid_filter:
        for p in (p_counts, p_values, p_answered):
            p.append({"$match": {"answers.questionId": ObjectId(qid_filter)}})

    counts = {(str(r["_id"]["q"]), r["_id"]["v"]): r["n"] for r in db.responses.aggregate(p_counts)}
    values_map = {str(r["_id"]): r["values"] for r in db.responses.aggregate(p_values)}
    answer_counts = {str(r["_id"]): r["n"] for r in db.responses.aggregate(p_answered)}
    total = db.responses.count_documents(match)

    out = []
    for q in db.questions.find({"surveyId": s["_id"]}).sort("order", 1):
        qid = str(q["_id"])
        if qid_filter and qid != qid_filter:
            continue
        t = q["type"]
        entry = {
            "id": qid,
            "order": q.get("order"),
            "title": q["title"],
            "type": t,
            "required": q.get("required", False),
            "deleted": q.get("deleted", False),
            "answerCount": answer_counts.get(qid, 0),
        }
        if t in ("single_choice", "multi_choice"):
            known = {o["key"] for o in q.get("options") or []}
            rows = [
                {"key": o["key"], "text": o["text"], "count": counts.get((qid, o["key"]), 0)}
                for o in q.get("options") or []
            ]
            # 暂停期可能删过选项：历史答卷中指向已删选项的计数归入占位行，保证总数不失真
            orphan = sum(n for (qq, v), n in counts.items() if qq == qid and v not in known)
            if orphan:
                rows.append({"key": "__removed__", "text": "（已删除的选项）", "count": orphan})
            entry["options"] = rows
        else:
            vals = values_map.get(qid, [])
            entry["values"] = vals
            if t == "number":
                nums = [v for v in vals if is_number(v)]
                if nums:
                    avg = round(sum(nums) / len(nums), 2)
                    entry["average"] = int(avg) if avg == int(avg) else avg
                    entry["min"] = min(nums)
                    entry["max"] = max(nums)
        out.append(entry)

    return jsonify({"totalResponses": total, "questions": to_json(out)})
