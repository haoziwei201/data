# API 说明

- 基础地址：`http://127.0.0.1:5000`
- 数据格式：请求与响应均为 JSON（`Content-Type: application/json`）
- 认证方式：登录后携带请求头 `Authorization: Bearer <token>`
- 错误响应统一为 `{"error": "中文错误信息"}`，HTTP 状态码语义：
  400 参数/校验错误，401 未登录，403 无权限/问卷不可填，404 不存在，409 冲突（重复注册、重复提交、非法状态流转），503 数据库不可用

## 1. 认证

### POST /api/register — 注册

```json
{ "username": "alice", "password": "secret1" }
```

成功（201）：`{ "user": { "id": "...", "username": "alice", "createdAt": "..." }, "token": "..." }`
失败：400（用户名 2~32 位 / 密码至少 6 位）、409（用户名已存在）

### POST /api/login — 登录

```json
{ "username": "alice", "password": "secret1" }
```

成功（200）：同注册返回结构。失败：401（用户名或密码错误）

### GET /api/me — 当前用户（需登录）

成功：`{ "user": { "id": "...", "username": "alice" } }`

## 2. 问卷管理（均需登录，仅限本人资源）

### 问卷状态机

```
draft（草稿）──发布──▶ published（收集中）◀──继续收集──▶ paused（已暂停）
                          │  ▲                      │
                          └──┼──────────────────────┘ 均可关闭
                             ▼
                        closed（已关闭，终态）
```

- **draft / paused**：可编辑题目（增删改、排序、导入模板）
- **published**：可正常填写；题目冻结，需先暂停
- **closed**：终态，不可再发布/暂停/继续，题目不可改

### POST /api/surveys — 创建问卷

```json
{
  "title": "大学生生活习惯调查",
  "description": "请如实填写",
  "allowAnonymous": true,
  "allowMultipleSubmissions": false,
  "deadline": "2026-11-01T23:59"
}
```

成功（201）：`{ "survey": { "id": "...", "slug": "aB3xK9", "status": "draft", ... } }`
`deadline` 可空；`slug` 用于构造分享链接 `/survey/<slug>`。

### GET /api/my/surveys — 我的问卷列表

成功：`{ "surveys": [ { ..., "responseCount": 3 } ] }`（按创建时间倒序）

### GET /api/surveys/{id} — 问卷详情（含全部题目）

成功：`{ "survey": { ... }, "questions": [ ... ] }`

### PATCH /api/surveys/{id} — 修改基本信息

body 中出现哪个字段就改哪个：`title` / `description` / `allowAnonymous` /
`allowMultipleSubmissions` / `deadline`（传 null 清除）。任何状态均可修改。

### POST /api/surveys/{id}/publish — 发布（draft → published）

前置：草稿状态、至少一道未删除题目、跳转图合法（目标存在且无环）。
成功：`{ "published": true, "shareUrl": "/survey/aB3xK9" }`
失败：409（无题目 / 跳转目标不存在 / 跳转存在循环 / 状态不对）

### POST /api/surveys/{id}/pause — 暂停收集（published → paused）

填写端立即返回 403"已暂停收集"；进入可编辑题目状态。成功：`{ "paused": true }`

### POST /api/surveys/{id}/resume — 继续收集（paused → published）

题目可能在暂停期被改过，恢复前重新做发布级校验（至少一题、跳转图合法），
不合法返回 409 并说明原因。成功：`{ "resumed": true, "shareUrl": "..." }`

### POST /api/surveys/{id}/close — 关闭（published/paused → closed）

终态，不可重开；已有答卷与统计保留。成功：`{ "closed": true }`

### DELETE /api/surveys/{id} — 删除问卷（级联，不可恢复）

任何状态均可删除；同时删除该问卷的全部题目与答卷（聚合根级联，不会产生悬空引用）。
前端会弹确认框提示将删除的题目数与答卷数。

成功：`{ "deleted": true, "questionsRemoved": 4, "responsesRemoved": 3 }`

## 3. 题目管理（需登录 + 仅问卷所有者 + 草稿或已暂停状态）

收集中（published）修改题目返回 409，提示先暂停；已关闭不可修改。

### POST /api/surveys/{sid}/questions — 添加题目

单选题：

```json
{
  "type": "single_choice",
  "title": "你的性别",
  "required": true,
  "options": [
    { "key": "opt_male", "text": "男" },
    { "key": "opt_female", "text": "女" },
    { "key": "opt_other", "text": "其他" }
  ],
  "jumpRules": []
}
```

多选题（数量限制）：

```json
{
  "type": "multi_choice",
  "title": "你喜欢的水果",
  "required": true,
  "options": [ { "key": "apple", "text": "苹果" }, { "key": "banana", "text": "香蕉" } ],
  "config": { "minSelect": 1, "maxSelect": 2 }
}
```

文本填空（字数限制）：

```json
{ "type": "text", "title": "留言", "required": false,
  "config": { "minLength": 2, "maxLength": 100 } }
```

数字填空（范围/整数限制）：

```json
{ "type": "number", "title": "你的年龄", "required": true,
  "config": { "min": 0, "max": 120, "integer": true } }
```

`config` 内所有字段均可省略（= 不限制）。成功（201）：`{ "question": { "id": "...", "order": 1, ... } }`

### PATCH /api/questions/{qid} — 编辑题目

可改 `title` / `required` / `options` / `config` / `jumpRules`（未传的字段保持不变）。
不可改 `type`（409）。修改会整体重新校验。

### DELETE /api/questions/{qid} — 删除题目（软删除）

历史答卷与统计仍可解释该题；新填写者不再看到。

### POST /api/surveys/{sid}/questions/order — 题目排序

```json
{ "questionIds": ["id2", "id1", "id3"] }
```

必须与当前未删除题目集合完全一致，否则 400。

### POST /api/surveys/{sid}/questions/import — 导入题目模板

body 为空或 `{"template": "demo"}`：导入内置测试模板（4 道题：单选跳转 + 数字 + 多选 + 文本）；
传来源问卷 id 则复制该问卷的未删除题目（仅限本人的问卷，否则 403）：

```json
{ "sourceSurveyId": "6ad3..." }
```

- 题目**追加**到当前问卷末尾（order 续接），不覆盖已有题目；
- 跳转规则的目标题目 id 自动**重映射**到新导入的题目（"end" 保持不变）；
- 仅草稿/已暂停状态可用（收集中返回 409）。

成功（201）：`{ "imported": 4, "questions": [ ...新题目... ] }`

## 4. 跳转规则（jumpRules 字段格式）

数组按序匹配，命中第一条即执行其 `goto`；都不命中则进入 order 下一题。
`goto` 为目标题目 id 或 `"end"`（提前结束问卷）。

```json
[
  { "match": { "kind": "option_any", "optionKeys": ["no"] }, "goto": "<qid>" },
  { "match": { "kind": "option_all", "optionKeys": ["a", "b"] }, "goto": "<qid>" },
  { "match": { "kind": "number", "op": "between", "min": 0, "max": 18 }, "goto": "<qid>" },
  { "match": { "kind": "number", "op": "gte", "value": 18 }, "goto": "end" },
  { "match": { "kind": "always" }, "goto": "end" }
]
```

- `option_any`：选中任一列出的选项（单选值是 key，多选值是 key 数组，均适用）
- `option_all`：同时选中全部列出选项（用于多选）
- `number`：`op` ∈ eq/ne/gt/gte/lt/lte/between（between 用 min/max，其余用 value）
- `always`：无条件

## 5. 填写（发布中的问卷；未开匿名则需登录）

### GET /api/fill/{slug} — 打开问卷

成功：`{ "survey": { "title", "description", "deadline", "allowAnonymous", ... },
"questionCount": 4, "loggedIn": true }`
失败：404 不存在 / 403 未发布或已关闭或已过截止 / 401 需要登录。

### POST /api/fill/{slug}/step — 逐题导航

```json
{ "answers": { "<qid>": "yes" } }
```

`answers` 为到目前为止的全部已答内容（单选传 key 字符串，多选传 key 数组，
文本传字符串，数字传数值）。成功：

```json
{ "done": false, "question": { "id": "...", "order": 3, "type": "multi_choice", "title": "...",
  "required": true, "options": [...], "config": {...} } }
```

`done=true` 表示按当前答案路径已走到尽头（可提交）。答案不合法返回 400（错误信息
形如"第3题（你喜欢的水果）：最多只能选择 2 项"）。

### POST /api/fill/{slug}/submit — 提交答卷

body 同 step。服务端回放路径：路径上未答的必问题报 400；被跳转跳过的题不校验、
不入库。成功（201）：`{ "responseId": "...", "submittedAt": "..." }`
409：该用户已填写过且问卷不允许重复提交。

## 6. 统计（需登录 + 仅问卷所有者）

### GET /api/surveys/{id}/stats — 整卷统计（?questionId= 只看单题）

```json
{
  "totalResponses": 2,
  "questions": [
    { "id": "...", "order": 1, "title": "你是否年满18岁", "type": "single_choice",
      "required": true, "deleted": false, "answerCount": 2,
      "options": [ { "key": "yes", "text": "是", "count": 1 },
                   { "key": "no", "text": "否", "count": 1 } ] },
    { "id": "...", "order": 2, "title": "你的年龄", "type": "number",
      "answerCount": 1, "values": [20], "average": 20, "min": 20, "max": 120 },
    { "id": "...", "order": 4, "title": "留言", "type": "text",
      "answerCount": 1, "values": ["谢谢"] }
  ]
}
```

- 选择题：`options[].count` 为被选次数；`answerCount` 为回答人数
- 数字题：`average/min/max`（无答案时省略）+ `values` 全量
- 文本题：`values` 全量列出

## 7. 其他

- `GET /api/health`：健康检查，含数据库连通性
- `GET /survey/{slug}`：作业要求的短链形式，302 跳转到填写页 `/fill.html?slug=...`
