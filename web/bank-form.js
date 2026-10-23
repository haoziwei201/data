/* 题目表单共享组件（bank-form.js）：创建与编辑两种模式复用同一套表单。
   - 创建：mountBankForm({ onCreated }) → POST /api/bank/questions（题库页/我的问卷页）
   - 编辑：mountQuestionEditor({ initial, lockType, submitLabel, onSubmit })
           → 由页面决定提交方式（题目详情页 PATCH 生成新版本）
   用法：页面放一个 <div id="bank-form"></div>（或传 containerId），引入
   common.js 后调用上述函数。
   表单 id 一律 bf- 前缀：my.html 的建问卷表单本身占用 n-* id，避免冲突。
   选项编辑与问卷编辑页同款：逐行输入框 + 删除/添加，空行保存时过滤；
   编辑模式回填既有选项时保留其原始 key（答案映射依赖 key 稳定）。 */

const BANK_FORM_CONFIG_HTML = {
  single_choice: "",
  multi_choice: `<label class="inline">最少选择 <input type="number" id="bf-minSelect" min="1" style="width:70px;"></label>
    <label class="inline">最多选择 <input type="number" id="bf-maxSelect" min="1" style="width:70px;"></label>`,
  text: `<label class="inline">最少字数 <input type="number" id="bf-minLength" min="0" style="width:70px;"></label>
    <label class="inline">最多字数 <input type="number" id="bf-maxLength" min="0" style="width:70px;"></label>`,
  number: `<label class="inline">最小值 <input type="number" step="any" id="bf-min" style="width:80px;"></label>
    <label class="inline">最大值 <input type="number" step="any" id="bf-max" style="width:80px;"></label>
    <label class="inline"><input type="checkbox" id="bf-integer"> 必须是整数</label>`,
};

// 选项行状态（与 edit.html 的 optRows 同款交互）
let bankOptRows = [];
let bankKeySeq = 1;

function renderBankOptRows() {
  const box = document.getElementById("bf-options");
  if (!box) return;
  box.innerHTML = "";
  bankOptRows.forEach((r, i) => {
    const div = document.createElement("div");
    div.className = "opt-row";
    div.innerHTML = `<input type="text" value="${esc(r.text)}" placeholder="选项 ${i + 1} 文本" oninput="bankOptRows[${i}].text=this.value">
      <button class="small danger ghost" onclick="bankOptRows.splice(${i},1);renderBankOptRows()">✕</button>`;
    box.appendChild(div);
  });
}

function addBankOptRow() {
  bankOptRows.push({ key: "opt_" + bankKeySeq++, text: "" });
  renderBankOptRows();
}

function _bankNum(id) {
  const v = document.getElementById(id)?.value;
  return v === "" || v == null ? null : Number(v);
}

function collectBankPayload() {
  const t = document.getElementById("bf-type").value;
  const body = {
    type: t,
    title: document.getElementById("bf-title").value,
    required: document.getElementById("bf-required").checked,
    config: {},
  };
  if (t === "single_choice" || t === "multi_choice") {
    body.options = bankOptRows
      .filter(r => r.text.trim())
      .map(r => ({ key: r.key, text: r.text.trim() }));
    if (body.options.length < 2) { showMsg("err", "选择题至少需要两个选项"); return null; }
  }
  if (t === "multi_choice") { body.config.minSelect = _bankNum("bf-minSelect"); body.config.maxSelect = _bankNum("bf-maxSelect"); }
  if (t === "text") { body.config.minLength = _bankNum("bf-minLength"); body.config.maxLength = _bankNum("bf-maxLength"); }
  if (t === "number") { body.config.min = _bankNum("bf-min"); body.config.max = _bankNum("bf-max"); body.config.integer = document.getElementById("bf-integer").checked; }
  return body;
}

function _fillBankConfig(config) {
  for (const [k, v] of Object.entries(config || {})) {
    const el = document.getElementById("bf-" + k);
    if (!el || v == null) continue;
    if (el.type === "checkbox") el.checked = !!v;
    else el.value = v;
  }
}

function mountQuestionEditor(opts = {}) {
  const root = document.getElementById(opts.containerId || "bank-form");
  const initial = opts.initial || {};
  const type0 = initial.type || "number";
  root.innerHTML = `
    <label class="field">题型
      <select id="bf-type" ${opts.lockType ? "disabled" : ""}>
        <option value="single_choice" ${type0 === "single_choice" ? "selected" : ""}>单选题</option>
        <option value="multi_choice" ${type0 === "multi_choice" ? "selected" : ""}>多选题</option>
        <option value="text" ${type0 === "text" ? "selected" : ""}>文本填空</option>
        <option value="number" ${type0 === "number" ? "selected" : ""}>数字填空</option>
      </select>
    </label>
    <label class="field">题目标题 <input type="text" id="bf-title" maxlength="500" placeholder="例如：你的年龄" value="${esc(initial.title || "")}"></label>
    <label class="inline"><input type="checkbox" id="bf-required" ${initial.required ? "checked" : ""}> 必答题</label>
    <div id="bf-options-box" style="display:none;">
      <h3>选项</h3>
      <div id="bf-options"></div>
      <button class="ghost small" onclick="addBankOptRow()">＋ 添加选项</button>
    </div>
    <div id="bf-config-box"></div>
    <div style="margin-top:10px;"><button onclick="submitQuestionForm()">${esc(opts.submitLabel || "保存")}</button></div>`;

  // 编辑模式保留既有选项的原始 key；创建模式从两行空选项开始
  bankOptRows = initial.options && initial.options.length
    ? initial.options.map(o => ({ key: o.key, text: o.text }))
    : [{ key: "opt_" + bankKeySeq++, text: "" }, { key: "opt_" + bankKeySeq++, text: "" }];
  // 回填的选项可能占用 opt_N 序号：把生成器推到最大号之后，避免新增选项 key 撞号
  for (const r of bankOptRows) {
    const m = /^opt_(\d+)$/.exec(r.key);
    if (m) bankKeySeq = Math.max(bankKeySeq, Number(m[1]) + 1);
  }
  renderBankOptRows();

  const typeSel = document.getElementById("bf-type");
  typeSel.addEventListener("change", () => {
    const t = document.getElementById("bf-type").value;
    document.getElementById("bf-options-box").style.display =
      (t === "single_choice" || t === "multi_choice") ? "block" : "none";
    document.getElementById("bf-config-box").innerHTML = BANK_FORM_CONFIG_HTML[t] || "";
    _fillBankConfig(initial.config);
  });
  typeSel.dispatchEvent(new Event("change"));

  window.submitQuestionForm = async function () {
    const body = collectBankPayload();
    if (!body) return;
    try {
      await opts.onSubmit(body);
    } catch (e) {
      showMsg("err", e.message);
    }
  };
}

function mountBankForm(opts = {}) {
  mountQuestionEditor({
    initial: { required: true },  // 创建模式默认必答（沿用第一阶段习惯）
    submitLabel: "保存到题库",
    onSubmit: async (body) => {
      await api("/api/bank/questions", { method: "POST", body });
      showMsg("err", "已保存到题库。", true);
      document.getElementById("bf-title").value = "";
      bankOptRows = [
        { key: "opt_" + bankKeySeq++, text: "" },
        { key: "opt_" + bankKeySeq++, text: "" },
      ];
      renderBankOptRows();
      opts.onCreated && opts.onCreated();
    },
  });
}
