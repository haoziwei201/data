"""第二阶段测试：题目资产化（保存复用 / 共享 / 不可变 / 版本链 / 多版本并存 /
使用反查 / 题库 / 跨问卷统计）+ v1→v2 数据迁移。"""
from bson import ObjectId

from tests.conftest import auth
from tests.test_api_flow import add_question, build_demo_survey, create_survey


def create_bank_question(client, user, **kw):
    body = {
        "type": kw.get("type", "number"),
        "title": kw.get("title", "你的年龄"),
        "required": kw.get("required", True),
        "config": kw.get("config", {"min": 0, "max": 120, "integer": True}),
    }
    if "options" in kw:
        body["options"] = kw["options"]
    r = client.post("/api/bank/questions", json=body, headers=auth(user))
    assert r.status_code == 201, r.get_json()
    return r.get_json()["question"]


def add_from_bank(client, user, sid, root_id, version=None):
    body = {"rootId": root_id}
    if version is not None:
        body["version"] = version
    r = client.post(f"/api/surveys/{sid}/questions/from-bank", json=body,
                    headers=auth(user))
    return r


def submit(client, slug, answers, user=None):
    return client.post(f"/api/fill/{slug}/submit", json={"answers": answers},
                       headers=auth(user) if user else None)


# ---------- 需求一：题目保存复用 ----------

class TestBankReuse:
    def test_add_question_not_auto_banked(self, client, user):
        """第二阶段调整：问卷内建题不再自动入库，需用户显式「存入题库」。"""
        s = create_survey(client, user)
        q = add_question(client, user, s["id"], {"type": "text", "title": "备注"})
        assert q["rootId"] is None and q["bankVersion"] is None
        r = client.get("/api/bank/questions?scope=mine", headers=auth(user))
        assert r.get_json()["questions"] == []

    def test_save_to_bank_creates_identity(self, client, user):
        """未入库题目显式存入题库：创建身份 v1，实例换绑。"""
        s = create_survey(client, user)
        q = add_question(client, user, s["id"], {"type": "text", "title": "备注"})
        r = client.post(f"/api/surveys/{s['id']}/questions/{q['id']}/save-to-bank",
                        headers=auth(user))
        assert r.status_code == 200
        assert r.get_json()["savedVersion"] == 1
        root_id = r.get_json()["rootId"]
        bank = client.get("/api/bank/questions?scope=mine", headers=auth(user)).get_json()["questions"]
        assert [b["id"] for b in bank] == [root_id]
        assert bank[0]["title"] == "备注"
        qs = client.get(f"/api/surveys/{s['id']}", headers=auth(user)).get_json()["questions"]
        assert qs[0]["rootId"] == root_id and qs[0]["bankVersion"] == 1

    def test_same_question_in_multiple_surveys(self, client, user):
        """一道题可以被多个问卷使用（需求一核心场景）。"""
        b = create_bank_question(client, user)
        s1 = create_survey(client, user, title="卷一")
        s2 = create_survey(client, user, title="卷二")
        assert add_from_bank(client, user, s1["id"], b["id"]).status_code == 201
        assert add_from_bank(client, user, s2["id"], b["id"]).status_code == 201
        for sid in (s1["id"], s2["id"]):
            r = client.get(f"/api/surveys/{sid}", headers=auth(user))
            qs = r.get_json()["questions"]
            assert len(qs) == 1 and qs[0]["rootId"] == b["id"]

    def test_duplicate_in_same_survey_rejected(self, client, user):
        b = create_bank_question(client, user)
        s = create_survey(client, user)
        assert add_from_bank(client, user, s["id"], b["id"]).status_code == 201
        r = add_from_bank(client, user, s["id"], b["id"])
        assert r.status_code == 409

    def test_bank_standalone_question(self, client, user):
        """直接在题库创建题目（不挂任何问卷）。"""
        b = create_bank_question(client, user, type="single_choice", title="你的性别",
                                 options=[{"key": "m", "text": "男"}, {"key": "f", "text": "女"}])
        assert b["type"] == "single_choice"
        r = client.get("/api/bank/questions?scope=mine", headers=auth(user))
        assert len(r.get_json()["questions"]) == 1


# ---------- 需求二：题目共享 ----------

class TestSharing:
    def test_share_and_use(self, client, user, other_user):
        b = create_bank_question(client, user)
        r = client.post(f"/api/bank/questions/{b['id']}/share",
                        json={"username": other_user["username"]}, headers=auth(user))
        assert r.status_code == 200

        # 共享前看不到；共享后出现在“共享给我的”
        r = client.get("/api/bank/questions?scope=shared", headers=auth(other_user))
        ids = [q["id"] for q in r.get_json()["questions"]]
        assert b["id"] in ids

        # 对方可以把这道题加入自己的问卷
        s = create_survey(client, other_user)
        assert add_from_bank(client, other_user, s["id"], b["id"]).status_code == 201

        # 共享后所有者仍然可以使用同一个题目（再建一卷也用它）
        s2 = create_survey(client, user)
        assert add_from_bank(client, user, s2["id"], b["id"]).status_code == 201

    def test_unshared_user_forbidden(self, client, user, other_user):
        b = create_bank_question(client, user)
        r = client.get(f"/api/bank/questions/{b['id']}", headers=auth(other_user))
        assert r.status_code == 403
        s = create_survey(client, other_user)
        assert add_from_bank(client, other_user, s["id"], b["id"]).status_code == 403

    def test_shared_user_cannot_modify_bank(self, client, user, other_user):
        b = create_bank_question(client, user)
        client.post(f"/api/bank/questions/{b['id']}/share",
                    json={"username": other_user["username"]}, headers=auth(user))
        r = client.patch(f"/api/bank/questions/{b['id']}",
                         json={"title": "偷改"}, headers=auth(other_user))
        assert r.status_code == 403
        r = client.post(f"/api/bank/questions/{b['id']}/share",
                        json={"username": "someone"}, headers=auth(other_user))
        assert r.status_code == 403

    def test_unshare(self, client, user, other_user):
        b = create_bank_question(client, user)
        client.post(f"/api/bank/questions/{b['id']}/share",
                    json={"username": other_user["username"]}, headers=auth(user))
        r = client.post(f"/api/bank/questions/{b['id']}/unshare",
                        json={"username": other_user["username"]}, headers=auth(user))
        assert r.status_code == 200
        r = client.get("/api/bank/questions?scope=shared", headers=auth(other_user))
        assert r.get_json()["questions"] == []

    def test_share_unknown_user(self, client, user):
        b = create_bank_question(client, user)
        r = client.post(f"/api/bank/questions/{b['id']}/share",
                        json={"username": "不存在的用户"}, headers=auth(user))
        assert r.status_code == 404


# ---------- 需求三：改题不影响已发布问卷（copy-on-write） ----------

class TestImmutablePublished:
    def test_edit_frozen_survey_forks_instance(self, client, user, other_user):
        s, ids = build_demo_survey(client, user)  # 已发布
        submit(client, s["slug"], {ids["q1"]: "no", ids["q3"]: ["apple"]}, other_user)

        client.post(f"/api/surveys/{s['id']}/pause", headers=auth(user))
        r = client.patch(f"/api/questions/{ids['q3']}", json={
            "title": "你喜欢的水果（修订）",
            "options": [{"key": "banana", "text": "香蕉"},
                        {"key": "melon", "text": "西瓜"}],
            "config": {"minSelect": 1, "maxSelect": 2},
        }, headers=auth(user))
        assert r.status_code == 200
        new_q = r.get_json()["question"]
        # 生成了新实例，旧实例退役（建题不自动入库，未入库题 rootId 为空）
        assert new_q["id"] != ids["q3"]
        assert new_q["basedOn"] == ids["q3"]
        assert new_q["rootId"] is None

        from app.db import get_db
        old = get_db().questions.find_one({"_id": ObjectId(ids["q3"])})
        assert old["deleted"] is True          # 旧实例软删保留
        assert old["title"] == "你喜欢的水果"   # 内容未被篡改（历史答卷可解释）

        # 其他题目的跳转目标自动改指新实例
        q1 = get_db().questions.find_one({"_id": ObjectId(ids["q1"])})
        assert q1["jumpRules"][0]["goto"] == new_q["id"]

        # 新填写者看到新内容
        client.post(f"/api/surveys/{s['id']}/resume", headers=auth(user))
        r = client.post(f"/api/fill/{s['slug']}/step", json={"answers": {}},
                        headers=auth(other_user))
        assert r.get_json()["question"]["id"] == ids["q1"]
        r = client.post(f"/api/fill/{s['slug']}/step",
                        json={"answers": {ids["q1"]: "no"}}, headers=auth(other_user))
        assert r.get_json()["question"]["id"] == new_q["id"]

    def test_stats_merge_across_fork(self, client, user, other_user):
        """分叉前后两批答卷在统计里按同一道题归并，且旧选项计入“已删除”。"""
        s, ids = build_demo_survey(client, user, allowMultipleSubmissions=True)
        submit(client, s["slug"], {ids["q1"]: "no", ids["q3"]: ["apple", "banana"]},
               other_user)

        client.post(f"/api/surveys/{s['id']}/pause", headers=auth(user))
        r = client.patch(f"/api/questions/{ids['q3']}", json={
            "options": [{"key": "banana", "text": "香蕉"},
                        {"key": "melon", "text": "西瓜"}],
            "config": {"minSelect": 1, "maxSelect": 2},
        }, headers=auth(user))
        new_q3 = r.get_json()["question"]["id"]  # 分叉后的新实例接管题位
        client.post(f"/api/surveys/{s['id']}/resume", headers=auth(user))
        r = submit(client, s["slug"],
                   {ids["q1"]: "no", new_q3: ["banana", "melon"]}, other_user)
        assert r.status_code == 201, r.get_json()

        r = client.get(f"/api/surveys/{s['id']}/stats", headers=auth(user))
        q3 = next(q for q in r.get_json()["questions"] if q["id"] == ids["q3"])
        rows = {o["text"]: o["count"] for o in q3["options"]}
        assert rows["香蕉"] == 2
        assert rows["西瓜"] == 1
        assert rows["（已删除的选项）"] == 1  # apple 只在旧版本存在
        assert q3["answerCount"] == 2
        assert q3["title"] == "你喜欢的水果"  # 行 id 为锚点实例

    def test_draft_edit_still_in_place(self, client, user):
        """草稿问卷（未发布无答卷）保持第一阶段原地编辑行为。"""
        s = create_survey(client, user)
        q = add_question(client, user, s["id"], {"type": "text", "title": "旧标题"})
        r = client.patch(f"/api/questions/{q['id']}", json={"title": "新标题"},
                         headers=auth(user))
        assert r.status_code == 200
        assert r.get_json()["question"]["id"] == q["id"]  # 不分叉
        assert r.get_json()["question"]["title"] == "新标题"

    def test_published_without_responses_also_frozen(self, client, user):
        """只要发布过（即使还没有答卷），题目内容即冻结（需求三口径从严）。"""
        s = create_survey(client, user)
        q = add_question(client, user, s["id"], {"type": "text", "title": "Q"})
        client.post(f"/api/surveys/{s['id']}/publish", headers=auth(user))
        client.post(f"/api/surveys/{s['id']}/pause", headers=auth(user))
        r = client.patch(f"/api/questions/{q['id']}", json={"title": "Q2"},
                         headers=auth(user))
        assert r.status_code == 200
        assert r.get_json()["question"]["id"] != q["id"]  # 分叉


# ---------- 需求四：题目修改历史 ----------

class TestVersionHistory:
    def test_edit_appends_version_with_lineage(self, client, user):
        b = create_bank_question(client, user, title="你的年龄")
        r = client.patch(f"/api/bank/questions/{b['id']}",
                         json={"title": "你的真实年龄"}, headers=auth(user))
        assert r.get_json()["version"] == 2
        r = client.get(f"/api/bank/questions/{b['id']}", headers=auth(user))
        detail = r.get_json()["question"]
        assert [v["version"] for v in detail["versions"]] == [1, 2]
        assert detail["versions"][1]["basedOn"] == 1
        assert detail["versions"][1]["content"]["title"] == "你的真实年龄"
        assert detail["versions"][0]["content"]["title"] == "你的年龄"  # 旧版仍在
        assert detail["currentVersion"] == 2

    def test_restore_old_version(self, client, user):
        """恢复旧版本 = 以旧内容创建新版本，历史不改写。"""
        b = create_bank_question(client, user, title="v1标题")
        client.patch(f"/api/bank/questions/{b['id']}", json={"title": "v2标题"},
                     headers=auth(user))
        r = client.post(f"/api/bank/questions/{b['id']}/restore",
                        json={"version": 1}, headers=auth(user))
        assert r.status_code == 200
        assert r.get_json() == {"restoredFrom": 1, "version": 3}
        r = client.get(f"/api/bank/questions/{b['id']}", headers=auth(user))
        detail = r.get_json()["question"]
        assert detail["currentVersion"] == 3
        assert detail["versions"][2]["basedOn"] == 1
        assert detail["versions"][2]["content"]["title"] == "v1标题"
        assert detail["versions"][1]["content"]["title"] == "v2标题"  # v2 未被抹掉

    def test_restore_missing_version(self, client, user):
        b = create_bank_question(client, user)
        r = client.post(f"/api/bank/questions/{b['id']}/restore",
                        json={"version": 99}, headers=auth(user))
        assert r.status_code == 404

    def test_cannot_change_type(self, client, user):
        b = create_bank_question(client, user)
        r = client.patch(f"/api/bank/questions/{b['id']}",
                         json={"type": "text"}, headers=auth(user))
        assert r.status_code == 409


# ---------- 需求五：多版本并存 ----------

class TestCoexistingVersions:
    def test_different_surveys_use_different_versions(self, client, user):
        b = create_bank_question(client, user, title="你的年龄")
        s_old = create_survey(client, user, title="用v1的卷")
        assert add_from_bank(client, user, s_old["id"], b["id"], version=1).status_code == 201

        client.patch(f"/api/bank/questions/{b['id']}",
                     json={"config": {"min": 0, "max": 60, "integer": True},
                           "title": "你的年龄（60岁以内）"}, headers=auth(user))

        s_new = create_survey(client, user, title="用v2的卷")
        assert add_from_bank(client, user, s_new["id"], b["id"]).status_code == 201  # 默认最新

        qs_old = client.get(f"/api/surveys/{s_old['id']}", headers=auth(user)).get_json()["questions"]
        qs_new = client.get(f"/api/surveys/{s_new['id']}", headers=auth(user)).get_json()["questions"]
        assert qs_old[0]["bankVersion"] == 1 and qs_old[0]["title"] == "你的年龄"
        assert qs_new[0]["bankVersion"] == 2 and qs_new[0]["title"] == "你的年龄（60岁以内）"

    def test_upgrade_draft_survey_to_latest(self, client, user):
        """题库更新后，草稿问卷可一键升级到最新版本（原地更新）。"""
        b = create_bank_question(client, user, title="你的年龄")
        s = create_survey(client, user)
        r = add_from_bank(client, user, s["id"], b["id"])
        q = r.get_json()["question"]
        client.patch(f"/api/bank/questions/{b['id']}", json={"title": "你的年龄v2"},
                     headers=auth(user))
        r = client.post(f"/api/surveys/{s['id']}/questions/{q['id']}/upgrade",
                        headers=auth(user))
        assert r.status_code == 200
        assert r.get_json()["question"]["title"] == "你的年龄v2"
        assert r.get_json()["question"]["bankVersion"] == 2
        # 已在最新版本再升级 → 409
        r = client.post(f"/api/surveys/{s['id']}/questions/{q['id']}/upgrade",
                        headers=auth(user))
        assert r.status_code == 409

    def test_pick_and_switch_arbitrary_version(self, client, user):
        """多版本共存（需求五强化）：加入问卷可选任意版本，卷内可任意切换。
        场景：v1/v2/v3 并存 → 新卷选 v2 加入 → 切回 v1 → 已在用版本 409。"""
        b = create_bank_question(client, user, title="v1标题")
        client.patch(f"/api/bank/questions/{b['id']}", json={"title": "v2标题"},
                     headers=auth(user))
        client.patch(f"/api/bank/questions/{b['id']}", json={"title": "v3标题"},
                     headers=auth(user))

        # 加入问卷时明确选择 v2（而非默认最新 v3）
        s = create_survey(client, user, title="选版本的卷")
        r = add_from_bank(client, user, s["id"], b["id"], version=2)
        q = r.get_json()["question"]
        assert q["bankVersion"] == 2 and q["title"] == "v2标题"

        # 卷内切换到 v1（草稿原地更新）
        r = client.post(f"/api/surveys/{s['id']}/questions/{q['id']}/upgrade",
                        json={"version": 1}, headers=auth(user))
        assert r.status_code == 200 and r.get_json()["upgradedTo"] == 1
        qs = client.get(f"/api/surveys/{s['id']}", headers=auth(user)).get_json()["questions"]
        assert qs[0]["bankVersion"] == 1 and qs[0]["title"] == "v1标题"
        assert qs[0]["bankCurrentVersion"] == 3 and qs[0]["bankTotalVersions"] == 3

        # 已在使用的版本再切换 → 409
        r = client.post(f"/api/surveys/{s['id']}/questions/{q['id']}/upgrade",
                        json={"version": 1}, headers=auth(user))
        assert r.status_code == 409
        # 不存在的版本 → 404
        r = client.post(f"/api/surveys/{s['id']}/questions/{q['id']}/upgrade",
                        json={"version": 99}, headers=auth(user))
        assert r.status_code == 404

    def test_upgrade_unbanked_question_rejected(self, client, user):
        """未入库题目不能切换版本，提示先存入题库。"""
        s = create_survey(client, user)
        q = add_question(client, user, s["id"], {"type": "text", "title": "未入库"})
        r = client.post(f"/api/surveys/{s['id']}/questions/{q['id']}/upgrade",
                        headers=auth(user))
        assert r.status_code == 409 and "题库" in r.get_json()["error"]

    def test_upgrade_frozen_survey_forks(self, client, user, other_user):
        b = create_bank_question(client, user, title="你的年龄")
        s = create_survey(client, user)
        q = add_from_bank(client, user, s["id"], b["id"]).get_json()["question"]
        client.post(f"/api/surveys/{s['id']}/publish", headers=auth(user))
        submit(client, s["slug"], {q["id"]: 20}, other_user)
        client.patch(f"/api/bank/questions/{b['id']}", json={"title": "你的年龄v2"},
                     headers=auth(user))
        client.post(f"/api/surveys/{s['id']}/pause", headers=auth(user))
        r = client.post(f"/api/surveys/{s['id']}/questions/{q['id']}/upgrade",
                        headers=auth(user))
        assert r.status_code == 200
        assert r.get_json()["question"]["id"] != q["id"]  # 冻结卷升级走分叉

    def test_save_to_bank_appends_version(self, client, user):
        """卷内改好的题目存回题库成为新版本；首次存入创建身份 v1。"""
        s = create_survey(client, user)
        q = add_question(client, user, s["id"], {"type": "text", "title": "初版"})
        # 首次存入：创建身份 v1
        r = client.post(f"/api/surveys/{s['id']}/questions/{q['id']}/save-to-bank",
                        headers=auth(user))
        assert r.get_json()["savedVersion"] == 1
        root_id = r.get_json()["rootId"]
        # 卷内继续修改（草稿原地改），再存回：追加 v2
        client.patch(f"/api/questions/{q['id']}", json={"title": "问卷里改过"},
                     headers=auth(user))
        r = client.post(f"/api/surveys/{s['id']}/questions/{q['id']}/save-to-bank",
                        headers=auth(user))
        assert r.get_json()["savedVersion"] == 2
        detail = client.get(f"/api/bank/questions/{root_id}",
                            headers=auth(user)).get_json()["question"]
        assert detail["versions"][0]["content"]["title"] == "初版"
        assert detail["versions"][1]["content"]["title"] == "问卷里改过"

    def test_save_to_bank_owner_only(self, client, user, other_user):
        b = create_bank_question(client, user)
        client.post(f"/api/bank/questions/{b['id']}/share",
                    json={"username": other_user["username"]}, headers=auth(user))
        s = create_survey(client, other_user)
        q = add_from_bank(client, other_user, s["id"], b["id"]).get_json()["question"]
        r = client.post(f"/api/surveys/{s['id']}/questions/{q['id']}/save-to-bank",
                        json={"title": "想存回去"}, headers=auth(other_user))
        assert r.status_code == 403


# ---------- 需求六：查看题目被哪些问卷使用 ----------

class TestUsage:
    def test_usage_across_surveys_and_users(self, client, user, other_user):
        b = create_bank_question(client, user, title="你的年龄")
        client.post(f"/api/bank/questions/{b['id']}/share",
                    json={"username": other_user["username"]}, headers=auth(user))
        s1 = create_survey(client, user, title="我的卷")
        add_from_bank(client, user, s1["id"], b["id"])
        s2 = create_survey(client, other_user, title="同事的卷")
        add_from_bank(client, other_user, s2["id"], b["id"])
        client.patch(f"/api/bank/questions/{b['id']}", json={"title": "年龄v2"},
                     headers=auth(user))
        s3 = create_survey(client, user, title="我的新卷")
        add_from_bank(client, user, s3["id"], b["id"])  # 用 v2

        r = client.get(f"/api/bank/questions/{b['id']}/usage", headers=auth(user))
        usage = {(u["surveyTitle"], u["surveyOwner"]): u for u in r.get_json()["usage"]}
        assert usage[("我的卷", user["username"])]["versionsInUse"] == [1]
        assert usage[("我的新卷", user["username"])]["versionsInUse"] == [2]
        assert usage[("同事的卷", other_user["username"])]["versionsInUse"] == [1]
        assert all(u["active"] for u in usage.values())

        # 删除问卷后使用情况随之消失（题目资产仍在）
        client.delete(f"/api/surveys/{s1['id']}", headers=auth(user))
        r = client.get(f"/api/bank/questions/{b['id']}/usage", headers=auth(user))
        titles = [u["surveyTitle"] for u in r.get_json()["usage"]]
        assert "我的卷" not in titles

    def test_usage_shows_deleted_question_as_inactive(self, client, user):
        b = create_bank_question(client, user)
        s = create_survey(client, user)
        q = add_from_bank(client, user, s["id"], b["id"]).get_json()["question"]
        client.delete(f"/api/questions/{q['id']}", headers=auth(user))
        r = client.get(f"/api/bank/questions/{b['id']}/usage", headers=auth(user))
        u = r.get_json()["usage"][0]
        assert u["active"] is False
        assert u["allVersions"] == [1]

    def test_usage_permission(self, client, user, other_user):
        b = create_bank_question(client, user)
        assert client.get(f"/api/bank/questions/{b['id']}/usage",
                          headers=auth(other_user)).status_code == 403


# ---------- 需求七：题库管理 ----------

class TestBankManagement:
    def test_bank_delete_keeps_survey_instances(self, client, user):
        """从题库移除身份：问卷中的实例（内容快照）不受影响。"""
        b = create_bank_question(client, user)
        s = create_survey(client, user)
        q = add_from_bank(client, user, s["id"], b["id"]).get_json()["question"]
        assert client.delete(f"/api/bank/questions/{b['id']}",
                             headers=auth(user)).status_code == 200
        # 题库列表中消失
        r = client.get("/api/bank/questions?scope=mine", headers=auth(user))
        assert r.get_json()["questions"] == []
        # 问卷照常使用
        r = client.get(f"/api/surveys/{s['id']}", headers=auth(user))
        assert len(r.get_json()["questions"]) == 1
        assert r.get_json()["questions"][0]["title"] == q["title"]
        client.post(f"/api/surveys/{s['id']}/publish", headers=auth(user))

    def test_import_does_not_auto_bank(self, client, user):
        """未入库来源题按普通内容复制（不自动入库），可重复导入。"""
        src, _ = build_demo_survey(client, user)
        dst = create_survey(client, user, title="导入目标")
        r = client.post(f"/api/surveys/{dst['id']}/questions/import",
                        json={"sourceSurveyId": src["id"]}, headers=auth(user))
        assert r.status_code == 201 and r.get_json()["imported"] == 4
        from app.db import get_db
        assert get_db().question_bank.count_documents({}) == 0  # 不产生身份
        # 第一阶段语义：未入库题重复导入不去重
        r = client.post(f"/api/surveys/{dst['id']}/questions/import",
                        json={"sourceSurveyId": src["id"]}, headers=auth(user))
        assert r.get_json()["imported"] == 4 and r.get_json()["skipped"] == 0

    def test_import_banked_source_reuses_identity(self, client, user):
        """来源题已入库时导入复用身份：使用情况统计跨卷可见，重复导入去重。"""
        src, ids = build_demo_survey(client, user)
        # 把 Q1 显式存入题库
        client.post(f"/api/surveys/{src['id']}/questions/{ids['q1']}/save-to-bank",
                    headers=auth(user))
        dst = create_survey(client, user, title="导入目标")
        r = client.post(f"/api/surveys/{dst['id']}/questions/import",
                        json={"sourceSurveyId": src["id"]}, headers=auth(user))
        data = r.get_json()
        assert data["imported"] == 4 and data["skipped"] == 0
        from app.db import get_db
        assert get_db().question_bank.count_documents({}) == 1  # 只复用，不新建
        dst_qs = client.get(f"/api/surveys/{dst['id']}", headers=auth(user)).get_json()["questions"]
        reused = next(q for q in dst_qs if q["rootId"])
        src_qs = client.get(f"/api/surveys/{src['id']}", headers=auth(user)).get_json()["questions"]
        assert reused["rootId"] == src_qs[0]["rootId"]
        # 再导一次：已入库的那道被跳过，其余照常
        r = client.post(f"/api/surveys/{dst['id']}/questions/import",
                        json={"sourceSurveyId": src["id"]}, headers=auth(user))
        data = r.get_json()
        assert data["imported"] == 3 and data["skipped"] == 1


# ---------- 需求八：单题跨问卷统计 ----------

class TestCrossSurveyStats:
    def test_cross_survey_aggregate(self, client, user, other_user):
        """“你的年龄”用在两份问卷里，跨卷统计给出总体分布与分卷明细。"""
        s1, ids1 = build_demo_survey(client, user, allowMultipleSubmissions=True)
        submit(client, s1["slug"], {ids1["q1"]: "yes", ids1["q2"]: 20,
                                    ids1["q3"]: ["apple"]}, other_user)
        # 把“你的年龄”（q2）显式存入题库后，加入第二份问卷收集
        r = client.post(f"/api/surveys/{s1['id']}/questions/{ids1['q2']}/save-to-bank",
                        headers=auth(user))
        assert r.status_code == 200
        root_id = r.get_json()["rootId"]
        s2 = create_survey(client, user, title="年龄专项调查", allowAnonymous=True)
        r = add_from_bank(client, user, s2["id"], root_id)
        assert r.status_code == 201
        q2b = r.get_json()["question"]
        client.post(f"/api/surveys/{s2['id']}/publish", headers=auth(user))
        submit(client, s2["slug"], {q2b["id"]: 30})           # 匿名
        submit(client, s2["slug"], {q2b["id"]: 40})           # 匿名

        r = client.get(f"/api/bank/questions/{root_id}/stats", headers=auth(user))
        data = r.get_json()
        assert data["question"]["title"] == "你的年龄"
        assert data["answers"]["answerCount"] == 3
        assert data["answers"]["average"] == 30
        assert data["answers"]["min"] == 20 and data["answers"]["max"] == 40
        per = {p["surveyTitle"]: p for p in data["perSurvey"]}
        assert per["测试问卷"]["answers"]["values"] == [20]
        assert per["年龄专项调查"]["answers"]["answerCount"] == 2

    def test_cross_survey_choice_question(self, client, user, other_user):
        b = create_bank_question(client, user, type="single_choice", title="你的性别",
                                 options=[{"key": "m", "text": "男"},
                                          {"key": "f", "text": "女"}])
        answers_by_survey = ["m", "f", "m"]  # 提交的是选项 key，不是选项文本
        for i, key in enumerate(answers_by_survey):
            s = create_survey(client, user, title=f"卷{i}", allowAnonymous=True)
            q = add_from_bank(client, user, s["id"], b["id"]).get_json()["question"]
            client.post(f"/api/surveys/{s['id']}/publish", headers=auth(user))
            submit(client, s["slug"], {q["id"]: key})

        r = client.get(f"/api/bank/questions/{b['id']}/stats", headers=auth(user))
        opts = {o["key"]: o["count"] for o in r.get_json()["answers"]["options"]}
        assert opts == {"m": 2, "f": 1}

    def test_cross_stats_permission(self, client, user, other_user):
        b = create_bank_question(client, user)
        assert client.get(f"/api/bank/questions/{b['id']}/stats",
                          headers=auth(other_user)).status_code == 403
        client.post(f"/api/bank/questions/{b['id']}/share",
                    json={"username": other_user["username"]}, headers=auth(user))
        assert client.get(f"/api/bank/questions/{b['id']}/stats",
                          headers=auth(other_user)).status_code == 200


# ---------- v1 → v2 数据迁移 ----------

class TestMigration:
    def test_migrate_legacy_data(self, client, user):
        """构造 v1 形态数据（题目无 rootId）→ 迁移 → 旧功能可用的同时资产化生效。"""
        from app.db import get_db
        from scripts.migrate_v1_to_v2 import migrate
        db = get_db()

        sid = db.surveys.insert_one({
            "ownerId": ObjectId(user["id"]), "title": "v1老问卷", "slug": "legacy1",
            "status": "closed", "allowAnonymous": True, "allowMultipleSubmissions": False,
            "deadline": None, "settings": {}, "schemaVersion": 1,
            "createdAt": now_(), "updatedAt": now_(),
        }).inserted_id
        qid = db.questions.insert_one({
            "surveyId": sid, "order": 1, "type": "number", "title": "你的年龄",
            "required": True, "config": {"min": 0, "max": 120, "integer": True},
            "jumpRules": [], "deleted": False, "schemaVersion": 1,
            "createdAt": now_(), "updatedAt": now_(),
        }).inserted_id
        db.responses.insert_one({
            "surveyId": sid,
            "respondent": {"userId": None, "anonymous": True},
            "submittedAt": now_(),
            "answers": [{"questionId": qid, "value": 22}],
        })

        migrate()
        q = db.questions.find_one({"_id": qid})
        assert q["bankVersion"] == 1 and q["basedOn"] is None
        b = db.question_bank.find_one({"_id": q["rootId"]})
        assert b is not None
        assert str(b["ownerId"]) == user["id"]           # 身份归属问卷创建者
        assert b["versions"][0]["content"]["title"] == "你的年龄"
        # 老答卷未动，且能通过身份被统计解释
        assert db.responses.count_documents({"surveyId": sid}) == 1
        # 幂等：再跑一遍无事发生
        migrate()
        assert db.question_bank.count_documents({}) == 1

        # 迁移后的老题目出现在题库中（需求一：老用户的历史题目自动入库）
        r = client.get("/api/bank/questions?scope=mine", headers=auth(user))
        assert [q0["title"] for q0 in r.get_json()["questions"]] == ["你的年龄"]


def now_():
    from app.utils import now
    return now()
