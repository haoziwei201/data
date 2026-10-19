"""API 集成测试：覆盖注册登录、问卷生命周期、题目校验、跳转填写、统计。"""
from tests.conftest import auth


# ---------- 公共构造工具 ----------

def create_survey(client, user, **kw):
    body = {
        "title": kw.get("title", "测试问卷"),
        "description": kw.get("description", ""),
        "allowAnonymous": kw.get("allowAnonymous", False),
        "allowMultipleSubmissions": kw.get("allowMultipleSubmissions", False),
        "deadline": kw.get("deadline"),
    }
    r = client.post("/api/surveys", json=body, headers=auth(user))
    assert r.status_code == 201, r.get_json()
    return r.get_json()["survey"]


def add_question(client, user, sid, body):
    r = client.post(f"/api/surveys/{sid}/questions", json=body, headers=auth(user))
    assert r.status_code == 201, r.get_json()
    return r.get_json()["question"]


def build_demo_survey(client, user, **survey_kw):
    """构造带跳转的演示问卷：
    Q1 单选(是否满18) --no--> Q3 ；yes 走默认顺序到 Q2 数字(年龄)；
    Q3 多选(水果, 1~2 项)；Q4 文本留言(选填, >=2 字)。"""
    s = create_survey(client, user, **survey_kw)
    sid = s["id"]
    q1 = add_question(client, user, sid, {
        "type": "single_choice", "title": "你是否年满18岁", "required": True,
        "options": [{"key": "yes", "text": "是"}, {"key": "no", "text": "否"}],
        "jumpRules": [],  # 占位，创建 Q3 后再回填跳转
    })
    q2 = add_question(client, user, sid, {
        "type": "number", "title": "你的年龄", "required": True,
        "config": {"min": 0, "max": 120, "integer": True},
    })
    q3 = add_question(client, user, sid, {
        "type": "multi_choice", "title": "你喜欢的水果", "required": True,
        "options": [{"key": "apple", "text": "苹果"}, {"key": "banana", "text": "香蕉"},
                    {"key": "melon", "text": "西瓜"}, {"key": "grape", "text": "葡萄"}],
        "config": {"minSelect": 1, "maxSelect": 2},
    })
    q4 = add_question(client, user, sid, {
        "type": "text", "title": "留言", "required": False,
        "config": {"minLength": 2, "maxLength": 100},
    })
    # Q1 选"否" -> 跳到 Q3（跳过年龄题）
    r = client.patch(f"/api/questions/{q1['id']}", json={
        "jumpRules": [{"match": {"kind": "option_any", "optionKeys": ["no"]}, "goto": q3["id"]}],
    }, headers=auth(user))
    assert r.status_code == 200, r.get_json()
    r = client.post(f"/api/surveys/{sid}/publish", headers=auth(user))
    assert r.status_code == 200, r.get_json()
    return s, {"q1": q1["id"], "q2": q2["id"], "q3": q3["id"], "q4": q4["id"]}


# ---------- 用户账号 ----------

class TestAuth:
    def test_register_login_me(self, client):
        r = client.post("/api/register", json={"username": "alice", "password": "secret1"})
        assert r.status_code == 201
        token = r.get_json()["token"]

        r = client.post("/api/login", json={"username": "alice", "password": "wrong!"})
        assert r.status_code == 401

        r = client.post("/api/login", json={"username": "alice", "password": "secret1"})
        assert r.status_code == 200

        r = client.get("/api/me", headers={"Authorization": "Bearer " + token})
        assert r.status_code == 200
        assert r.get_json()["user"]["username"] == "alice"

    def test_duplicate_username(self, client, user):
        r = client.post("/api/register", json={"username": user["username"], "password": "pass123"})
        assert r.status_code == 409

    def test_short_password_rejected(self, client):
        r = client.post("/api/register", json={"username": "bob", "password": "123"})
        assert r.status_code == 400

    def test_my_surveys_requires_login(self, client):
        assert client.get("/api/my/surveys").status_code == 401


# ---------- 问卷管理与权限 ----------

class TestSurveyLifecycle:
    def test_isolation_between_users(self, client, user, other_user):
        create_survey(client, user, title="A的问卷")
        r = client.get("/api/my/surveys", headers=auth(user))
        titles = [s["title"] for s in r.get_json()["surveys"]]
        assert titles == ["A的问卷"]
        r = client.get("/api/my/surveys", headers=auth(other_user))
        assert r.get_json()["surveys"] == []

    def test_publish_requires_question(self, client, user):
        s = create_survey(client, user)
        r = client.post(f"/api/surveys/{s['id']}/publish", headers=auth(user))
        assert r.status_code == 409
        assert "题目" in r.get_json()["error"]

    def test_full_lifecycle(self, client, user):
        s, ids = build_demo_survey(client, user)
        assert s["status"] == "draft"
        r = client.get(f"/api/surveys/{s['id']}", headers=auth(user))
        assert r.status_code == 200
        assert len(r.get_json()["questions"]) == 4

        # 发布后不能再改题
        r = client.post(f"/api/surveys/{s['id']}/questions", headers=auth(user),
                        json={"type": "text", "title": "新增"})
        assert r.status_code == 409
        # 但基本信息仍可改
        r = client.patch(f"/api/surveys/{s['id']}", json={"title": "改名问卷"}, headers=auth(user))
        assert r.status_code == 200
        # 关闭
        r = client.post(f"/api/surveys/{s['id']}/close", headers=auth(user))
        assert r.status_code == 200
        r = client.get(f"/api/fill/{s['slug']}", headers=auth(user))
        assert r.status_code == 403

    def test_owner_guard(self, client, user, other_user):
        s = create_survey(client, user)
        r = client.get(f"/api/surveys/{s['id']}", headers=auth(other_user))
        assert r.status_code == 403
        r = client.post(f"/api/surveys/{s['id']}/publish", headers=auth(other_user))
        assert r.status_code == 403

    def test_reorder(self, client, user):
        s = create_survey(client, user)
        a = add_question(client, user, s["id"], {"type": "text", "title": "A"})
        b = add_question(client, user, s["id"], {"type": "text", "title": "B"})
        r = client.post(f"/api/surveys/{s['id']}/questions/order",
                        json={"questionIds": [b["id"], a["id"]]}, headers=auth(user))
        assert r.status_code == 200
        r = client.get(f"/api/surveys/{s['id']}", headers=auth(user))
        titles = [q["title"] for q in r.get_json()["questions"]]
        assert titles == ["B", "A"]


# ---------- 填写与跳转 ----------

class TestFillFlow:
    def test_step_navigation_follows_jump(self, client, user, other_user):
        s, ids = build_demo_survey(client, user)
        slug = s["slug"]

        def step(answers):
            return client.post(f"/api/fill/{slug}/step", json={"answers": answers},
                               headers=auth(other_user))

        r = step({})
        assert r.get_json()["question"]["id"] == ids["q1"]

        # 选"否" -> 跳过年龄题，直接到 Q3
        r = step({ids["q1"]: "no"})
        assert r.get_json()["question"]["id"] == ids["q3"]

        # 选"是" -> 默认顺序到 Q2
        r = step({ids["q1"]: "yes"})
        assert r.get_json()["question"]["id"] == ids["q2"]

    def test_submit_with_jump_skip(self, client, user, other_user):
        s, ids = build_demo_survey(client, user)
        slug = s["slug"]
        answers = {ids["q1"]: "no", ids["q3"]: ["apple", "grape"], ids["q4"]: "谢谢"}
        r = client.post(f"/api/fill/{slug}/submit", json={"answers": answers},
                        headers=auth(other_user))
        assert r.status_code == 201, r.get_json()
        # 被跳过的必答题 Q2 不应报错，且不应被存储
        from app.db import get_db
        from bson import ObjectId
        doc = get_db().responses.find_one({"surveyId": ObjectId(s["id"])})
        assert doc is not None
        assert doc["respondent"]["anonymous"] is False
        assert doc["respondent"]["userId"] == ObjectId(other_user["id"])
        assert [a["questionId"] for a in doc["answers"]] == [ObjectId(ids["q1"]), ObjectId(ids["q3"]), ObjectId(ids["q4"])]
        assert doc["answers"][0]["value"] == "no"

    def test_duplicate_submission_rejected(self, client, user, other_user):
        s, ids = build_demo_survey(client, user)
        slug = s["slug"]
        answers = {ids["q1"]: "no", ids["q3"]: ["apple"]}
        r1 = client.post(f"/api/fill/{slug}/submit", json={"answers": answers},
                         headers=auth(other_user))
        assert r1.status_code == 201
        r2 = client.post(f"/api/fill/{slug}/submit", json={"answers": answers},
                         headers=auth(other_user))
        assert r2.status_code == 409

    def test_allow_multiple_submissions(self, client, user, other_user):
        s, ids = build_demo_survey(client, user, allowMultipleSubmissions=True)
        slug = s["slug"]
        answers = {ids["q1"]: "yes", ids["q2"]: 20, ids["q3"]: ["apple"]}
        for _ in range(2):
            r = client.post(f"/api/fill/{slug}/submit", json={"answers": answers},
                            headers=auth(other_user))
            assert r.status_code == 201

    def test_required_on_path_rejected(self, client, user, other_user):
        s, ids = build_demo_survey(client, user)
        slug = s["slug"]
        # q1 走 yes -> q2 必答但缺失
        r = client.post(f"/api/fill/{slug}/submit",
                        json={"answers": {ids["q1"]: "yes", ids["q3"]: ["apple"]}},
                        headers=auth(other_user))
        assert r.status_code == 400
        assert "必答" in r.get_json()["error"]

    def test_validation_errors(self, client, user, other_user):
        s, ids = build_demo_survey(client, user)
        slug = s["slug"]

        # 多选超过上限
        r = client.post(f"/api/fill/{slug}/submit",
                        json={"answers": {ids["q1"]: "no",
                                          ids["q3"]: ["apple", "banana", "melon"]}},
                        headers=auth(other_user))
        assert r.status_code == 400 and "最多" in r.get_json()["error"]

        # 数字非整数
        r = client.post(f"/api/fill/{slug}/submit",
                        json={"answers": {ids["q1"]: "yes", ids["q2"]: 12.5,
                                          ids["q3"]: ["apple"]}},
                        headers=auth(other_user))
        assert r.status_code == 400 and "整数" in r.get_json()["error"]

        # 数字超范围
        r = client.post(f"/api/fill/{slug}/submit",
                        json={"answers": {ids["q1"]: "yes", ids["q2"]: 200,
                                          ids["q3"]: ["apple"]}},
                        headers=auth(other_user))
        assert r.status_code == 400 and "大于" in r.get_json()["error"]

        # 文本太短
        r = client.post(f"/api/fill/{slug}/submit",
                        json={"answers": {ids["q1"]: "no", ids["q3"]: ["apple"],
                                          ids["q4"]: "x"}},
                        headers=auth(other_user))
        assert r.status_code == 400 and "至少" in r.get_json()["error"]

        # 无效选项 key
        r = client.post(f"/api/fill/{slug}/submit",
                        json={"answers": {ids["q1"]: "unknown", ids["q3"]: ["apple"]}},
                        headers=auth(other_user))
        assert r.status_code == 400

        # 不属于本问卷的题目
        r = client.post(f"/api/fill/{slug}/submit",
                        json={"answers": {"0123456789abcdef01234567": "x"}},
                        headers=auth(other_user))
        assert r.status_code == 400


class TestJumpGraphValidation:
    def test_missing_target_rejected(self, client, user):
        s = create_survey(client, user)
        add_question(client, user, s["id"], {
            "type": "single_choice", "title": "Q1", "required": False,
            "options": [{"key": "a", "text": "A"}, {"key": "b", "text": "B"}],
            "jumpRules": [{"match": {"kind": "always"},
                           "goto": "0123456789abcdef01234567"}],
        })
        r = client.post(f"/api/surveys/{s['id']}/publish", headers=auth(user))
        assert r.status_code == 409 and "不存在" in r.get_json()["error"]

    def test_cycle_rejected(self, client, user):
        s = create_survey(client, user)
        q1 = add_question(client, user, s["id"], {
            "type": "single_choice", "title": "Q1", "required": False,
            "options": [{"key": "a", "text": "A"}, {"key": "b", "text": "B"}],
        })
        q2 = add_question(client, user, s["id"], {"type": "text", "title": "Q2"})
        # Q1 -> Q2, Q2 -> Q1 成环
        client.patch(f"/api/questions/{q1['id']}",
                     json={"jumpRules": [{"match": {"kind": "always"}, "goto": q2["id"]}]},
                     headers=auth(user))
        client.patch(f"/api/questions/{q2['id']}",
                     json={"jumpRules": [{"match": {"kind": "always"}, "goto": q1['id']}]},
                     headers=auth(user))
        r = client.post(f"/api/surveys/{s['id']}/publish", headers=auth(user))
        assert r.status_code == 409 and "循环" in r.get_json()["error"]

    def test_valid_forward_jump_publishes(self, client, user):
        s = create_survey(client, user)
        q1 = add_question(client, user, s["id"], {
            "type": "single_choice", "title": "Q1", "required": False,
            "options": [{"key": "a", "text": "A"}, {"key": "b", "text": "B"}],
        })
        add_question(client, user, s["id"], {"type": "text", "title": "Q2"})
        client.patch(f"/api/questions/{q1['id']}",
                     json={"jumpRules": [{"match": {"kind": "always"}, "goto": "end"}]},
                     headers=auth(user))
        r = client.post(f"/api/surveys/{s['id']}/publish", headers=auth(user))
        assert r.status_code == 200


# ---------- 暂停收集 / 继续收集（状态机扩展） ----------

class TestPauseResume:
    def test_pause_edit_resume(self, client, user, other_user):
        s, ids = build_demo_survey(client, user)
        slug = s["slug"]
        # 先收一份答卷
        r = client.post(f"/api/fill/{slug}/submit",
                        json={"answers": {ids["q1"]: "no", ids["q3"]: ["apple"]}},
                        headers=auth(other_user))
        assert r.status_code == 201

        # 暂停：填写端立即关闭
        r = client.post(f"/api/surveys/{s['id']}/pause", headers=auth(user))
        assert r.status_code == 200
        r = client.get(f"/api/fill/{slug}", headers=auth(other_user))
        assert r.status_code == 403 and "暂停" in r.get_json()["error"]

        # 暂停期间可以加题、删题
        r = client.post(f"/api/surveys/{s['id']}/questions",
                        json={"type": "text", "title": "暂停期新增题"}, headers=auth(user))
        assert r.status_code == 201
        r = client.delete(f"/api/questions/{ids['q4']}", headers=auth(user))
        assert r.status_code == 200

        # 继续收集：恢复填写
        r = client.post(f"/api/surveys/{s['id']}/resume", headers=auth(user))
        assert r.status_code == 200
        r = client.get(f"/api/fill/{slug}", headers=auth(other_user))
        assert r.status_code == 200

        # 暂停前提交过答卷的用户，恢复后仍受"禁止重复提交"约束
        r = client.post(f"/api/fill/{slug}/submit",
                        json={"answers": {ids["q1"]: "yes", ids["q2"]: 20, ids["q3"]: ["banana"]}},
                        headers=auth(other_user))
        assert r.status_code == 409

        # 暂停状态也可以直接关闭（终态）
        client.post(f"/api/surveys/{s['id']}/pause", headers=auth(user))
        r = client.post(f"/api/surveys/{s['id']}/close", headers=auth(user))
        assert r.status_code == 200

    def test_resume_validates_jump_graph(self, client, user):
        s, ids = build_demo_survey(client, user)
        client.post(f"/api/surveys/{s['id']}/pause", headers=auth(user))
        # 暂停期间删掉 Q1 的跳转目标 Q3（软删除），恢复收集时应被拦截
        r = client.delete(f"/api/questions/{ids['q3']}", headers=auth(user))
        assert r.status_code == 200
        r = client.post(f"/api/surveys/{s['id']}/resume", headers=auth(user))
        assert r.status_code == 409 and "不存在" in r.get_json()["error"]

    def test_status_transitions_guarded(self, client, user):
        s = create_survey(client, user)
        # 草稿不能暂停
        assert client.post(f"/api/surveys/{s['id']}/pause", headers=auth(user)).status_code == 409
        # 草稿不能"继续收集"
        assert client.post(f"/api/surveys/{s['id']}/resume", headers=auth(user)).status_code == 409
        add_question(client, user, s["id"], {"type": "text", "title": "Q1"})
        client.post(f"/api/surveys/{s['id']}/publish", headers=auth(user))
        client.post(f"/api/surveys/{s['id']}/pause", headers=auth(user))
        # 已暂停不能再次暂停
        assert client.post(f"/api/surveys/{s['id']}/pause", headers=auth(user)).status_code == 409
        # 已发布状态不能"继续收集"
        client.post(f"/api/surveys/{s['id']}/resume", headers=auth(user))
        assert client.post(f"/api/surveys/{s['id']}/resume", headers=auth(user)).status_code == 409
        # 关闭是终态：不能再发布/暂停/继续
        client.post(f"/api/surveys/{s['id']}/close", headers=auth(user))
        for path in ("publish", "pause", "resume"):
            assert client.post(f"/api/surveys/{s['id']}/{path}", headers=auth(user)).status_code == 409

    def test_collecting_edit_rejected_with_hint(self, client, user):
        s, _ = build_demo_survey(client, user)  # 已发布（收集中）
        r = client.post(f"/api/surveys/{s['id']}/questions",
                        json={"type": "text", "title": "新题"}, headers=auth(user))
        assert r.status_code == 409 and "暂停" in r.get_json()["error"]


# ---------- 模板导入 ----------

class TestImportTemplate:
    def test_import_demo_template(self, client, user):
        s = create_survey(client, user)
        r = client.post(f"/api/surveys/{s['id']}/questions/import", json={}, headers=auth(user))
        assert r.status_code == 201, r.get_json()
        data = r.get_json()
        assert data["imported"] == 4

        r = client.get(f"/api/surveys/{s['id']}", headers=auth(user))
        qs = r.get_json()["questions"]
        assert [q["order"] for q in qs] == [1, 2, 3, 4]
        assert [q["type"] for q in qs] == ["single_choice", "number", "multi_choice", "text"]
        # 跳转规则应重映射到新题目 id（Q1 的"否" -> 新 Q3）
        assert qs[0]["jumpRules"][0]["goto"] == qs[2]["id"]

    def test_import_appends_after_existing(self, client, user):
        s = create_survey(client, user)
        add_question(client, user, s["id"], {"type": "text", "title": "已有题目"})
        r = client.post(f"/api/surveys/{s['id']}/questions/import", json={}, headers=auth(user))
        assert r.get_json()["imported"] == 4
        r = client.get(f"/api/surveys/{s['id']}", headers=auth(user))
        assert [q["order"] for q in r.get_json()["questions"]] == [1, 2, 3, 4, 5]

    def test_import_from_own_survey_remaps_jumps(self, client, user):
        src, _ = build_demo_survey(client, user)  # 4 题，Q1 跳转到 Q3
        dst = create_survey(client, user, title="导入目标")
        r = client.post(f"/api/surveys/{dst['id']}/questions/import",
                        json={"sourceSurveyId": src["id"]}, headers=auth(user))
        assert r.status_code == 201 and r.get_json()["imported"] == 4
        r = client.get(f"/api/surveys/{dst['id']}", headers=auth(user))
        qs = r.get_json()["questions"]
        # 重映射：目标卷里 Q1' 的跳转指向目标卷自己的 Q3'，而非来源卷的题目
        assert qs[0]["jumpRules"][0]["goto"] == qs[2]["id"]
        assert qs[0]["jumpRules"][0]["goto"] != client.get(
            f"/api/surveys/{src['id']}", headers=auth(user)).get_json()["questions"][2]["id"]

    def test_import_from_other_users_survey_forbidden(self, client, user, other_user):
        src, _ = build_demo_survey(client, user)
        dst = create_survey(client, other_user)
        r = client.post(f"/api/surveys/{dst['id']}/questions/import",
                        json={"sourceSurveyId": src["id"]}, headers=auth(other_user))
        assert r.status_code == 403

    def test_import_rejected_while_collecting(self, client, user):
        s, _ = build_demo_survey(client, user)  # 已发布
        r = client.post(f"/api/surveys/{s['id']}/questions/import", json={}, headers=auth(user))
        assert r.status_code == 409

    def test_imported_demo_works_end_to_end(self, client, user, other_user):
        s = create_survey(client, user)
        client.post(f"/api/surveys/{s['id']}/questions/import", json={}, headers=auth(user))
        r = client.post(f"/api/surveys/{s['id']}/publish", headers=auth(user))
        assert r.status_code == 200
        slug = s["slug"]
        r = client.post(f"/api/fill/{slug}/step", json={"answers": {}}, headers=auth(other_user))
        q1 = r.get_json()["question"]
        assert q1["type"] == "single_choice"
        # 走"否"路线验证导入后的跳转确实生效
        r = client.post(f"/api/fill/{slug}/step",
                        json={"answers": {q1["id"]: "no"}}, headers=auth(other_user))
        assert r.get_json()["question"]["type"] == "multi_choice"


# ---------- 问卷删除（聚合根级联） ----------

class TestSurveyDeletion:
    def test_delete_cascades_all_data(self, client, user, other_user):
        s, ids = build_demo_survey(client, user)
        client.post(f"/api/fill/{s['slug']}/submit",
                    json={"answers": {ids["q1"]: "no", ids["q3"]: ["apple"]}},
                    headers=auth(other_user))
        r = client.delete(f"/api/surveys/{s['id']}", headers=auth(user))
        assert r.status_code == 200
        data = r.get_json()
        assert data["deleted"] is True
        assert data["questionsRemoved"] == 4
        assert data["responsesRemoved"] == 1

        from app.db import get_db
        from bson import ObjectId
        sid = ObjectId(s["id"])
        assert get_db().surveys.count_documents({"_id": sid}) == 0
        assert get_db().questions.count_documents({"surveyId": sid}) == 0
        assert get_db().responses.count_documents({"surveyId": sid}) == 0

        # 对外全部失效：填写链接 404、详情 404、列表为空
        assert client.get(f"/api/fill/{s['slug']}", headers=auth(other_user)).status_code == 404
        assert client.get(f"/api/surveys/{s['id']}", headers=auth(user)).status_code == 404
        r = client.get("/api/my/surveys", headers=auth(user))
        assert r.get_json()["surveys"] == []

    def test_delete_only_owner(self, client, user, other_user):
        s, _ = build_demo_survey(client, user)
        assert client.delete(f"/api/surveys/{s['id']}", headers=auth(other_user)).status_code == 403
        assert client.get(f"/api/surveys/{s['id']}", headers=auth(user)).status_code == 200

    def test_delete_does_not_touch_other_surveys(self, client, user):
        s1, _ = build_demo_survey(client, user)
        s2, _ = build_demo_survey(client, user, title="另一份问卷")
        r = client.delete(f"/api/surveys/{s1['id']}", headers=auth(user))
        assert r.status_code == 200
        r = client.get(f"/api/surveys/{s2['id']}", headers=auth(user))
        assert r.status_code == 200
        assert len(r.get_json()["questions"]) == 4
        r = client.get("/api/my/surveys", headers=auth(user))
        titles = [s["title"] for s in r.get_json()["surveys"]]
        assert titles == ["另一份问卷"]

    def test_delete_works_in_any_status(self, client, user):
        # 草稿可直接删；收集中、已暂停、已关闭也可删
        s1 = create_survey(client, user, title="草稿删除")
        assert client.delete(f"/api/surveys/{s1['id']}", headers=auth(user)).status_code == 200
        s2, _ = build_demo_survey(client, user, title="发布删除")
        client.post(f"/api/surveys/{s2['id']}/pause", headers=auth(user))
        client.post(f"/api/surveys/{s2['id']}/close", headers=auth(user))
        assert client.delete(f"/api/surveys/{s2['id']}", headers=auth(user)).status_code == 200


# ---------- 匿名 / 截止时间 ----------

class TestAccessControl:
    def test_need_login_by_default(self, client, user):
        s, ids = build_demo_survey(client, user)
        r = client.get(f"/api/fill/{s['slug']}")
        assert r.status_code == 401
        assert "登录" in r.get_json()["error"]

    def test_anonymous_allowed(self, client, user):
        s, ids = build_demo_survey(client, user, allowAnonymous=True)
        r = client.get(f"/api/fill/{s['slug']}")
        assert r.status_code == 200
        assert r.get_json()["survey"]["allowAnonymous"] is True
        r = client.post(f"/api/fill/{s['slug']}/submit",
                        json={"answers": {ids["q1"]: "no", ids["q3"]: ["apple"]}})
        assert r.status_code == 201
        from app.db import get_db
        from bson import ObjectId
        doc = get_db().responses.find_one({"surveyId": ObjectId(s["id"])})
        assert doc["respondent"]["anonymous"] is True
        assert doc["respondent"]["userId"] is None

    def test_expired_deadline(self, client, user):
        s, ids = build_demo_survey(client, user, deadline="2020-01-01T00:00")
        r = client.get(f"/api/fill/{s['slug']}", headers=auth(user))
        assert r.status_code == 403
        assert "截止" in r.get_json()["error"]

    def test_fill_link_redirect(self, client, user):
        s, _ = build_demo_survey(client, user)
        r = client.get(f"/survey/{s['slug']}", headers=auth(user))
        assert r.status_code == 302
        assert s["slug"] in r.headers["Location"]


# ---------- 统计 ----------

class TestStats:
    def test_stats_correctness(self, client, user, other_user):
        s, ids = build_demo_survey(client, user, allowAnonymous=True)
        slug = s["slug"]
        # 提交两份答卷：一份匿名（no 路线），一份登录（yes 路线，年龄 20）
        client.post(f"/api/fill/{slug}/submit",
                    json={"answers": {ids["q1"]: "no", ids["q3"]: ["apple", "grape"],
                                      ids["q4"]: "谢谢"}})
        client.post(f"/api/fill/{slug}/submit",
                    json={"answers": {ids["q1"]: "yes", ids["q2"]: 20,
                                      ids["q3"]: ["banana"]}},
                    headers=auth(other_user))

        r = client.get(f"/api/surveys/{s['id']}/stats", headers=auth(user))
        assert r.status_code == 200
        data = r.get_json()
        assert data["totalResponses"] == 2

        by_id = {qq["id"]: qq for qq in data["questions"]}
        q1 = by_id[ids["q1"]]
        assert {o["key"]: o["count"] for o in q1["options"]} == {"yes": 1, "no": 1}
        assert q1["answerCount"] == 2

        q2 = by_id[ids["q2"]]  # 数字题：只有一份答卷答了 20
        assert q2["answerCount"] == 1
        assert q2["average"] == 20 and q2["min"] == 20 and q2["max"] == 20

        q3 = by_id[ids["q3"]]  # 多选：apple1 grape1 banana1
        counts = {o["key"]: o["count"] for o in q3["options"]}
        assert counts == {"apple": 1, "banana": 1, "melon": 0, "grape": 1}
        assert q3["answerCount"] == 2  # 每份答卷对该题各贡献一次回答

        q4 = by_id[ids["q4"]]  # 文本题：列出全部内容
        assert q4["values"] == ["谢谢"]

    def test_stats_single_question_filter(self, client, user, other_user):
        s, ids = build_demo_survey(client, user)
        client.post(f"/api/fill/{s['slug']}/submit",
                    json={"answers": {ids["q1"]: "no", ids["q3"]: ["apple"]}},
                    headers=auth(other_user))
        r = client.get(f"/api/surveys/{s['id']}/stats?questionId={ids['q3']}",
                       headers=auth(user))
        data = r.get_json()
        assert len(data["questions"]) == 1
        assert data["questions"][0]["id"] == ids["q3"]

    def test_stats_owner_only(self, client, user, other_user):
        s, _ = build_demo_survey(client, user)
        r = client.get(f"/api/surveys/{s['id']}/stats", headers=auth(other_user))
        assert r.status_code == 403

    def test_removed_option_still_counted(self, client, user, other_user):
        """暂停期删掉选项后，历史答卷中该选项的计数归入"（已删除的选项）"行。"""
        s, ids = build_demo_survey(client, user)
        slug = s["slug"]
        client.post(f"/api/fill/{slug}/submit",
                    json={"answers": {ids["q1"]: "no", ids["q3"]: ["apple", "banana"]}},
                    headers=auth(other_user))
        # 暂停后删除 apple 选项
        client.post(f"/api/surveys/{s['id']}/pause", headers=auth(user))
        r = client.patch(f"/api/questions/{ids['q3']}", json={
            "options": [{"key": "banana", "text": "香蕉"},
                        {"key": "melon", "text": "西瓜"},
                        {"key": "grape", "text": "葡萄"}],
        }, headers=auth(user))
        assert r.status_code == 200

        r = client.get(f"/api/surveys/{s['id']}/stats", headers=auth(user))
        q3 = next(q for q in r.get_json()["questions"] if q["id"] == ids["q3"])
        rows = {o["text"]: o["count"] for o in q3["options"]}
        assert rows["香蕉"] == 1
        assert rows["（已删除的选项）"] == 1  # apple 的历史计数
        assert q3["answerCount"] == 1
