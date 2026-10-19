const TYPE_LABELS = {
  single_choice: "单选题",
  multi_choice: "多选题",
  text: "文本填空",
  number: "数字填空",
};

async function api(path, { method = "GET", body } = {}) {
  const headers = { "Content-Type": "application/json" };
  const token = localStorage.getItem("token");
  if (token) headers["Authorization"] = "Bearer " + token;
  const res = await fetch(path, {
    method,
    headers,
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  let data = null;
  try { data = await res.json(); } catch (e) { /* 无 JSON 体 */ }
  if (!res.ok) {
    const msg = (data && data.error) || ("请求失败（HTTP " + res.status + "）");
    if (res.status === 401 && token) {
      // 本地令牌已失效：清掉并回到登录页，登录后跳回原页面
      localStorage.removeItem("token");
      localStorage.removeItem("username");
      localStorage.setItem("next", location.href);
      location = "/";
      throw new Error(msg);
    }
    const err = new Error(msg);
    err.status = res.status;
    throw err;
  }
  return data;
}

function esc(s) {
  return String(s == null ? "" : s)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
}

function showMsg(elId, text, ok = false) {
  const el = document.getElementById(elId);
  el.textContent = text;
  el.className = ok ? "ok-box" : "error-box";
  el.style.display = "block";
}

function logout() {
  localStorage.removeItem("token");
  localStorage.removeItem("username");
  location = "/";
}

function fmtTime(iso) {
  if (!iso) return "—";
  return String(iso).replace("T", " ").slice(0, 16);
}
