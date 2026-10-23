"""端到端冒烟测试：对运行中的服务（默认 http://127.0.0.1:5000）走完整业务流程。
用法：先启动 python run.py，再另开终端执行 python scripts/smoke_test.py
"""
import json
import time
import urllib.request

BASE = "http://127.0.0.1:5000"


def call(method, path, body=None, token=None):
    req = urllib.request.Request(BASE + path, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", "Bearer " + token)
    data = json.dumps(body).encode() if body is not None else None
    try:
        with urllib.request.urlopen(req, data=data) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode())


def step(name, status, expect, data=None):
    mark = "PASS" if status == expect else "FAIL"
    print(f"[{mark}] {name}: {status} (期望 {expect})" + (f" -> {data}" if mark == "FAIL" else ""))
    assert status == expect, data


ts = str(int(time.time()))

# 1. 注册并登录
st, r = call("POST", "/api/register", {"username": "smoke_a" + ts, "password": "pass123"})
step("注册用户A", st, 201)
token_a = r["token"]
name_a = "smoke_a" + ts

st, r = call("POST", "/api/register", {"username": "smoke_b" + ts, "password": "pass123"})
step("注册用户B", st, 201)
token_b = r["token"]
name_b = "smoke_b" + ts

# 2. 创建问卷
st, r = call("POST", "/api/surveys", {
    "title": "冒烟测试问卷",
    "description": "用于端到端验证",
    "allowAnonymous": True,
}, token_a)
step("创建问卷", st, 201)
sid, slug = r["survey"]["id"], r["survey"]["slug"]
print("      分享链接: /survey/" + slug)

# 3. 添加题目（Q1 单选带跳转、Q2 数字、Q3 多选、Q4 文本）
st, r = call("POST", f"/api/surveys/{sid}/questions", {
    "type": "single_choice", "title": "你是否年满18岁", "required": True,
    "options": [{"key": "yes", "text": "是"}, {"key": "no", "text": "否"}],
}, token_a)
step("添加Q1", st, 201)
q1 = r["question"]["id"]

st, r = call("POST", f"/api/surveys/{sid}/questions", {
    "type": "number", "title": "你的年龄", "required": True,
    "config": {"min": 0, "max": 120, "integer": True},
}, token_a)
step("添加Q2", st, 201)
q2 = r["question"]["id"]

st, r = call("POST", f"/api/surveys/{sid}/questions", {
    "type": "multi_choice", "title": "你喜欢的水果", "required": True,
    "options": [{"key": "apple", "text": "苹果"}, {"key": "banana", "text": "香蕉"},
                {"key": "melon", "text": "西瓜"}, {"key": "grape", "text": "葡萄"}],
    "config": {"minSelect": 1, "maxSelect": 2},
}, token_a)
step("添加Q3", st, 201)
q3 = r["question"]["id"]

st, r = call("POST", f"/api/surveys/{sid}/questions", {
    "type": "text", "title": "留言", "required": False,
    "config": {"minLength": 2, "maxLength": 100},
}, token_a)
step("添加Q4", st, 201)

# Q1 选"否" -> 跳到 Q3
st, _ = call("PATCH", f"/api/questions/{q1}", {
    "jumpRules": [{"match": {"kind": "option_any", "optionKeys": ["no"]}, "goto": q3}],
}, token_a)
step("配置跳转", st, 200)

# 4. 发布
st, r = call("POST", f"/api/surveys/{sid}/publish", token=token_a)
step("发布问卷", st, 200)

# 5. 填写（用户B，走"否"路线验证跳转）
st, r = call("GET", f"/api/fill/{slug}", token=token_b)
step("打开问卷", st, 200)

st, r = call("POST", f"/api/fill/{slug}/step", {"answers": {}}, token_b)
step("第一题是Q1", st, 200)
assert r["question"]["id"] == q1

st, r = call("POST", f"/api/fill/{slug}/step", {"answers": {q1: "no"}}, token_b)
step("Q1选否→跳到Q3(跳过Q2)", st, 200)
assert r["question"]["id"] == q3

st, r = call("POST", f"/api/fill/{slug}/submit", {
    "answers": {q1: "no", q3: ["apple", "grape"], "4": None}
}, token_b)
step("非法题目被拒绝", st, 400)

st, r = call("POST", f"/api/fill/{slug}/submit", {
    "answers": {q1: "no", q3: ["apple", "grape"]},
}, token_b)
step("用户B提交答卷", st, 201)

# 匿名提交一份（"是"路线，年龄 20）
st, r = call("POST", f"/api/fill/{slug}/submit", {
    "answers": {q1: "yes", q2: 20, q3: ["banana"]},
})
step("匿名提交答卷", st, 201)

# 重复提交（匿名不受限制）
st, r = call("POST", f"/api/fill/{slug}/submit", {"answers": {q1: "no", q3: ["melon"]}})
step("匿名再次提交", st, 201)

# 6. 暂停 → 改题 → 继续收集（状态机验证）
st, _ = call("POST", f"/api/surveys/{sid}/pause", token=token_a)
step("暂停收集", st, 200)
st, _ = call("GET", f"/api/fill/{slug}", token=token_b)
step("暂停期间不可填写", st, 403)
st, r = call("POST", f"/api/surveys/{sid}/questions",
             {"type": "text", "title": "暂停期新增题"}, token_a)
step("暂停期间可加题", st, 201)
st, _ = call("POST", f"/api/surveys/{sid}/resume", token=token_a)
step("继续收集", st, 200)
st, _ = call("GET", f"/api/fill/{slug}", token=token_b)
step("恢复后可填写", st, 200)

# 7. 导入测试模板到新问卷并发布（模板导入验证）
st, r = call("POST", "/api/surveys", {"title": "模板导入验证"}, token_a)
sid2, slug2 = r["survey"]["id"], r["survey"]["slug"]
st, r = call("POST", f"/api/surveys/{sid2}/questions/import", {}, token_a)
step("导入内置测试模板", st, 201)
assert r["imported"] == 4
st, _ = call("POST", f"/api/surveys/{sid2}/publish", token=token_a)
step("导入后可直接发布", st, 200)
st, r = call("POST", f"/api/fill/{slug2}/step", {"answers": {}}, token=token_b)
q1b = r["question"]["id"]
st, r = call("POST", f"/api/fill/{slug2}/step", {"answers": {q1b: "no"}}, token=token_b)
step("导入问卷的跳转生效（否→第3题）", st, 200)
assert r["question"]["type"] == "multi_choice"

# 8. 统计
st, r = call("GET", f"/api/surveys/{sid}/stats", token=token_a)
step("查看统计", st, 200)
print(f"      总答卷数: {r['totalResponses']}")
for q in r["questions"]:
    if q.get("options"):
        opts = ", ".join(f"{o['text']}={o['count']}" for o in q["options"])
        print(f"      第{q['order']}题 {q['title']}: {opts} (回答{q['answerCount']}人)")
    elif q["type"] == "number":
        print(f"      第{q['order']}题 {q['title']}: 平均={q.get('average')} 最小={q.get('min')} 最大={q.get('max')}")
    else:
        print(f"      第{q['order']}题 {q['title']}: {q['values']}")

assert r["totalResponses"] == 3
# 9. 删除问卷（聚合根级联；题目身份保留在题库，见步骤 11）
st, r = call("DELETE", f"/api/surveys/{sid2}", token=token_a)
step("删除问卷", st, 200)
assert r["questionsRemoved"] == 4 and r["responsesRemoved"] == 0
st, _ = call("GET", f"/api/fill/{slug2}", token=token_b)
step("删除后填写链接 404", st, 404)

# ============================================================
# 第二阶段：题目资产化（题库 / 共享 / 版本链 / 分叉 / 跨卷统计）
# ============================================================

# 10. A 在题库直接创建"你的性别"
st, r = call("POST", "/api/bank/questions", {
    "type": "single_choice", "title": "你的性别", "required": True,
    "options": [{"key": "m", "text": "男"}, {"key": "f", "text": "女"}],
}, token_a)
step("题库直接创建题目", st, 201)
root = r["question"]["id"]

# 11. 建题不自动入库（第二阶段调整：题库只含显式创建/存入的题目）
st, r = call("GET", "/api/bank/questions?scope=mine", token=token_a)
step("A 的题库列表", st, 200)
assert len(r["questions"]) == 1, f"期望仅显式创建的 1 道，实际 {len(r['questions'])}"
print("      问卷里建的题不再自动入库；题库仅含显式创建/存入的题目")

# 11b. 问卷题目显式存入题库
st, r = call("POST", f"/api/surveys/{sid}/questions/{q2}/save-to-bank", token=token_a)
step("卷内题目存入题库", st, 200)
assert r["savedVersion"] == 1
st, r = call("GET", "/api/bank/questions?scope=mine", token=token_a)
assert len(r["questions"]) == 2
q2_root = r["questions"][0]["id"] if r["questions"][0]["title"] == "你的年龄" else r["questions"][1]["id"]

# 12. 共享给 B；B 在"共享给我的"可见
st, _ = call("POST", f"/api/bank/questions/{root}/share", {"username": name_b}, token_a)
step("共享给B", st, 200)
st, r = call("GET", "/api/bank/questions?scope=shared", token=token_b)
step("B 看到「共享给我的」", st, 200)
assert any(q["id"] == root for q in r["questions"])
# B 无权修改题库题
st, _ = call("PATCH", f"/api/bank/questions/{root}", {"title": "偷改"}, token_b)
step("被共享者不能改题库", st, 403)

# 13. B 建问卷 → 从题库选题入卷 → 发布 → 收答卷
st, r = call("POST", "/api/surveys", {"title": "B的问卷", "allowAnonymous": True}, token_b)
step("B 创建问卷", st, 201)
sid_b, slug_b = r["survey"]["id"], r["survey"]["slug"]
st, r = call("POST", f"/api/surveys/{sid_b}/questions/from-bank", {"rootId": root}, token_b)
step("B 从题库选题入卷", st, 201)
qb = r["question"]["id"]
# 同卷重复入卷被拒
st, _ = call("POST", f"/api/surveys/{sid_b}/questions/from-bank", {"rootId": root}, token_b)
step("同卷重复用同一题 409", st, 409)
st, _ = call("POST", f"/api/surveys/{sid_b}/publish", token=token_b)
step("B 问卷发布", st, 200)
st, _ = call("POST", f"/api/fill/{slug_b}/submit", {"answers": {qb: "m"}})
step("B 卷匿名提交（男）", st, 201)

# 14. A 也复用同一题建第二份问卷（一题多卷）→ 发布 → 收答卷
st, r = call("POST", "/api/surveys", {"title": "A的复用卷", "allowAnonymous": True}, token_a)
step("A 创建复用卷", st, 201)
sid_a2, slug_a2 = r["survey"]["id"], r["survey"]["slug"]
st, r = call("POST", f"/api/surveys/{sid_a2}/questions/from-bank", {"rootId": root}, token_a)
step("A 复用同一题入卷", st, 201)
qa = r["question"]["id"]
st, _ = call("POST", f"/api/surveys/{sid_a2}/publish", token=token_a)
step("A 复用卷发布", st, 200)
st, _ = call("POST", f"/api/fill/{slug_a2}/submit", {"answers": {qa: "f"}})
step("A 卷匿名提交（女）", st, 201)

# 15. 版本链：编辑生成 v2 → 恢复 v1 生成 v3
st, r = call("PATCH", f"/api/bank/questions/{root}", {"title": "你的性别v2"}, token_a)
step("题库编辑生成 v2", st, 200)
assert r["version"] == 2
st, r = call("POST", f"/api/bank/questions/{root}/restore", {"version": 1}, token_a)
step("恢复 v1 → 新版本 v3", st, 200)
assert r["version"] == 3 and r["restoredFrom"] == 1
st, r = call("GET", f"/api/bank/questions/{root}", token=token_a)
vers = [v["version"] for v in r["question"]["versions"]]
assert vers == [1, 2, 3], vers
step("版本链 [v1→v2→v3] 完整", 200, 200)

# 15b. 多版本共存：加入问卷时可选任意版本（选 v2 而非最新 v3），卷内可再切换
st, r = call("POST", "/api/surveys", {"title": "B的选版本卷"}, token_b)
sid_b2 = r["survey"]["id"]
st, r = call("POST", f"/api/surveys/{sid_b2}/questions/from-bank",
             {"rootId": root, "version": 2}, token_b)
step("B 选 v2 版本入卷", st, 201)
qv2 = r["question"]
assert qv2["bankVersion"] == 2 and qv2["title"] == "你的性别v2", qv2
st, r = call("POST", f"/api/surveys/{sid_b2}/questions/{qv2['id']}/upgrade",
             {"version": 1}, token_b)
step("卷内切换回 v1", st, 200)
assert r["question"]["title"] == "你的性别" and r["upgradedTo"] == 1
st, _ = call("POST", f"/api/surveys/{sid_b2}/questions/{qv2['id']}/upgrade",
             {"version": 1}, token_b)
step("已在用版本再切换 409", st, 409)

# 16. 使用反查：两份问卷在用同一题
st, r = call("GET", f"/api/bank/questions/{root}/usage", token=token_a)
step("使用反查", st, 200)
in_use = {u["surveyTitle"] for u in r["usage"] if u["active"]}
assert "B的问卷" in in_use and "A的复用卷" in in_use, r["usage"]
print(f"      使用该题的问卷: {sorted(in_use)}")

# 17. copy-on-write：A 的复用卷（已发布有答卷）改题 → 生成分叉实例
st, _ = call("POST", f"/api/surveys/{sid_a2}/pause", token=token_a)
step("暂停 A 复用卷", st, 200)
st, r = call("PATCH", f"/api/questions/{qa}", {"title": "你的性别（修订）"}, token_a)
step("冻结卷改题走分叉", st, 200)
qa_new = r["question"]["id"]
assert qa_new != qa and r["question"]["basedOn"] == qa
st, _ = call("POST", f"/api/surveys/{sid_a2}/resume", token=token_a)
step("继续收集", st, 200)
# 分叉后再收一份答卷（新实例），统计按身份归并到同一行
st, _ = call("POST", f"/api/fill/{slug_a2}/submit", {"answers": {qa_new: "m"}})
step("分叉后新答卷（男）", st, 201)
st, r = call("GET", f"/api/surveys/{sid_a2}/stats", token=token_a)
step("A 卷统计按身份归并", st, 200)
row = next(q for q in r["questions"] if q["id"] == qa)  # 行 id 仍是锚点实例
assert row["answerCount"] == 2, row
print(f"      分叉前后两份答卷归并为一行: {row['title']} 回答{row['answerCount']}人")

# 18. 跨问卷统计：B 卷 1 男 + A 卷 1 女 1 男
st, r = call("GET", f"/api/bank/questions/{root}/stats", token=token_a)
step("跨问卷统计", st, 200)
opts = {o["key"]: o["count"] for o in r["answers"]["options"]}
assert opts == {"m": 2, "f": 1}, opts
per = {p["surveyTitle"]: p["answers"]["answerCount"] for p in r["perSurvey"]}
assert per == {"B的问卷": 1, "A的复用卷": 2, "B的选版本卷": 0}, per
print(f"      跨卷汇总: 男={opts['m']} 女={opts['f']}；分卷: {per}")

# 19. 从题库移除身份：共享列表消失，但两份问卷照常可填
st, _ = call("DELETE", f"/api/bank/questions/{root}", token=token_a)
step("从题库移除身份", st, 200)
st, _ = call("GET", f"/api/fill/{slug_b}")
step("移除后 B 卷仍可填写", st, 200)
st, r = call("GET", "/api/bank/questions?scope=shared", token=token_b)
assert not any(q["id"] == root for q in r["questions"])
step("移除后共享列表消失", st, 200)

print("\n全部冒烟测试通过 ✅")
