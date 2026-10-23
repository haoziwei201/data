"""统计：聚合管道出裸数字，应用层合并题目定义出带标签的报表（设计文档第六节）。

第二阶段：同一题目身份在同一问卷内可能有多个实例（copy-on-write 分叉、
软删除后重新加入），答卷按实例 id 存储。统计时按 rootId 归并：
- 行 id = 该身份在本卷的最早实例 id（锚点，对外稳定，历史答卷即存此 id）；
- 展示内容 = 最新活跃实例（无活跃实例则最新退役实例，标记 deleted）；
- 计数 = 该身份全部实例的回答合计，保证「改题不分家、删题不丢数」。
"""
from bson import ObjectId
from flask import Blueprint, jsonify, request

from app.db import get_db
from app.errors import ApiError
from app.routes.surveys import get_owned_survey
from app.security import login_required
from app.utils import is_number, to_json

bp = Blueprint("stats", __name__)


def _group_by_root(instances):
    """把问卷内全部实例（含退役/软删）归并成统计行。
    已入库题目按身份（rootId）归并——分叉/删除后重新加入都算同一道题；
    未入库题目沿 basedOn 血统链归并到本卷最初的实例（copy-on-write 分叉
    后统计仍是一行）。"""
    by_id = {i["_id"]: i for i in instances}

    def anchor(i):
        if i.get("rootId"):
            return i["rootId"]
        cur, seen = i, set()
        while cur.get("basedOn") and cur["basedOn"] in by_id and cur["_id"] not in seen:
            seen.add(cur["_id"])
            cur = by_id[cur["basedOn"]]
        return cur["_id"]

    groups = {}
    for inst in instances:
        groups.setdefault(anchor(inst), []).append(inst)
    rows = []
    for key, group in groups.items():
        group.sort(key=lambda i: (i.get("createdAt") or i["_id"], i["_id"]))
        first = group[0]
        active = [i for i in group if not i.get("deleted")]
        display = max(active or group, key=lambda i: (i.get("updatedAt") or i["_id"], i["_id"]))
        rows.append({
            "rootId": str(key),
            "anchorId": str(first["_id"]),
            "display": display,
            "deleted": not active,
            "qids": [str(i["_id"]) for i in group],
        })
    rows.sort(key=lambda r: (r["display"].get("order") or 0, r["anchorId"]))
    return rows


@bp.get("/api/surveys/<sid>/stats")
@login_required
def survey_stats(sid):
    s = get_owned_survey(sid)
    qid_filter = request.args.get("questionId")
    if qid_filter is not None and not ObjectId.is_valid(qid_filter):
        raise ApiError("questionId 不正确", 400)

    db = get_db()
    match = {"surveyId": s["_id"]}
    total = db.responses.count_documents(match)
    instances = list(db.questions.find({"surveyId": s["_id"]}).sort("order", 1))
    rows = _group_by_root(instances)
    if qid_filter:
        # 过滤条件是实例 id（锚点或任一分叉），换算成身份过滤
        target = [r for r in rows if qid_filter in r["qids"]]
        if not target:
            return jsonify({"totalResponses": total, "questions": []})
        rows = target

    all_qids = [ObjectId(q) for r in rows for q in r["qids"]]

    # A. 每个取值被选次数（多选数组经 $unwind 展开，标量原样通过）
    p_counts = [
        {"$match": match},
        {"$unwind": "$answers"},
        {"$match": {"answers.questionId": {"$in": all_qids}}},
        {"$unwind": "$answers.value"},
        {"$group": {"_id": {"q": "$answers.questionId", "v": "$answers.value"}, "n": {"$sum": 1}}},
    ]
    # B. 每题的全部填写内容（文本/数字题用）
    p_values = [
        {"$match": match},
        {"$unwind": "$answers"},
        {"$match": {"answers.questionId": {"$in": all_qids}}},
        {"$group": {"_id": "$answers.questionId", "values": {"$push": "$answers.value"}}},
    ]
    counts = {(str(r["_id"]["q"]), r["_id"]["v"]): r["n"]
              for r in db.responses.aggregate(p_counts)}
    values_map = {str(r["_id"]): r["values"] for r in db.responses.aggregate(p_values)}

    out = []
    for r in rows:
        q = r["display"]
        qids = r["qids"]
        t = q["type"]
        entry = {
            "id": r["anchorId"],
            "rootId": r["rootId"],
            "order": q.get("order"),
            "title": q["title"],
            "type": t,
            "required": q.get("required", False),
            "deleted": r["deleted"],
            "answerCount": sum(len(values_map.get(qid, [])) for qid in qids),
        }
        if t in ("single_choice", "multi_choice"):
            rows_o = [
                {"key": o["key"], "text": o["text"],
                 "count": sum(n for (qq, v), n in counts.items()
                              if qq in qids and v == o["key"])}
                for o in q.get("options") or []
            ]
            # 分叉/编辑可能删过选项：历史答卷中指向已删选项的计数归入占位行，保证总数不失真
            known = {o["key"] for o in q.get("options") or []}
            orphan = sum(n for (qq, v), n in counts.items() if qq in qids and v not in known)
            if orphan:
                rows_o.append({"key": "__removed__", "text": "（已删除的选项）", "count": orphan})
            entry["options"] = rows_o
        else:
            vals = [v for qid in qids for v in values_map.get(qid, [])]
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
