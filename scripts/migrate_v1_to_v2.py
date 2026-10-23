"""一次性数据迁移：v1（题目问卷私有）→ v2（题目资产化：身份 + 版本 + 实例）。

对每个尚未迁移的 v1 题目文档（无 rootId 字段）：
1. 在 question_bank 中创建题目身份，v1 内容即版本链的第 1 版，
   身份所有者 = 所属问卷的创建者（老用户的历史题目自动进入其题库）；
2. 在原题目文档上补血统字段：rootId / bankVersion=1 / basedOn=null。

不动的部分（旧数据零迁移）：
- responses 一行不改：answers.questionId 存的实例 id（= v1 题目 _id），
  迁移后仍是有效的内容快照引用；
- surveys / users 不动；
- 跳转规则不动：goto 指向的实例 id 在迁移前后不变。

用法：
    python scripts/migrate_v1_to_v2.py           # 执行迁移
    python scripts/migrate_v1_to_v2.py --dry     # 只统计，不写入

脚本幂等：已迁移（带 rootId）的题目自动跳过，可安全重复执行。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.db import ensure_indexes, get_db  # noqa: E402
from app.utils import now  # noqa: E402

CONTENT_FIELDS = ("type", "title", "required", "options", "config")


def migrate(dry=False):
    db = get_db()
    ensure_indexes()

    legacy = list(db.questions.find({"rootId": {"$exists": False}}))
    if not legacy:
        print("没有需要迁移的数据（所有题目均已带 rootId），直接退出。")
        return

    # 问卷创建者缓存：身份归属 = 问卷 owner
    survey_owner = {}
    missing_owner = 0
    created = 0
    for q in legacy:
        sid = q.get("surveyId")
        if sid not in survey_owner:
            s = db.surveys.find_one({"_id": sid}, {"ownerId": 1})
            survey_owner[sid] = s["ownerId"] if s else None
        owner_id = survey_owner[sid]
        if owner_id is None:
            # 极端情况：问卷已不存在（v1 删除问卷会级联删题，正常不会出现）
            missing_owner += 1
            continue

        content = {k: q.get(k) for k in CONTENT_FIELDS if k in q}
        bank_doc = {
            "ownerId": owner_id,
            "sharedWith": [],
            "versions": [{
                "version": 1, "basedOn": None,
                "editorId": owner_id, "createdAt": q.get("createdAt") or now(),
                "content": content,
            }],
            "currentVersion": 1,
            "deleted": False,
            "createdAt": q.get("createdAt") or now(),
            "updatedAt": now(),
        }
        if not dry:
            result = db.question_bank.insert_one(bank_doc)
            db.questions.update_one(
                {"_id": q["_id"]},
                {"$set": {
                    "rootId": result.inserted_id,
                    "bankVersion": 1,
                    "basedOn": None,
                    "schemaVersion": 2,
                }},
            )
        created += 1

    print(f"迁移完成：{created} 道题目升级为 v2（题目身份 + 实例血统）"
          f"{'（dry 模式，未写入）' if dry else ''}。")
    if missing_owner:
        print(f"警告：{missing_owner} 道题目找不到所属问卷，未迁移。")


if __name__ == "__main__":
    migrate(dry="--dry" in sys.argv)
