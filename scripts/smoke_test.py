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

st, r = call("POST", "/api/register", {"username": "smoke_b" + ts, "password": "pass123"})
step("注册用户B", st, 201)
token_b = r["token"]

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
# 9. 删除问卷（聚合根级联）
st, r = call("DELETE", f"/api/surveys/{sid2}", token=token_a)
step("删除问卷", st, 200)
assert r["questionsRemoved"] == 4 and r["responsesRemoved"] == 0
st, _ = call("GET", f"/api/fill/{slug2}", token=token_b)
step("删除后填写链接 404", st, 404)

print("\n全部冒烟测试通过 ✅")
