/* 基础工具与 HTTP 封装 —— 负责人：C4（CRUD 与 API） */
const $ = (s) => document.querySelector(s);
const $$ = (s) => Array.from(document.querySelectorAll(s));

/* ---------------- 操作人 ---------------- */
/* 审计表里一屏 anonymous 等于没有审计，所以操作人必须能填、且要持久化。
   存 localStorage：刷新页面不丢，换台机器演示时填一次就够。 */
const OPERATOR_KEY = "g05.operator";

function currentOperator() {
  try {
    return (localStorage.getItem(OPERATOR_KEY) || "").trim();
  } catch {
    return "";
  }
}
function setOperator(name) {
  const v = (name || "").trim().slice(0, 64);
  try {
    if (v) localStorage.setItem(OPERATOR_KEY, v);
    else localStorage.removeItem(OPERATOR_KEY);
  } catch {
    /* 隐私模式下 localStorage 可能不可用：忽略即可，退回 anonymous */
  }
  syncOperatorUI();
}

/* ---------------- 基础工具 ---------------- */
function toast(msg, type = "") {
  const el = document.createElement("div");
  el.className = "toast " + type;
  el.textContent = msg;
  $("#toast").appendChild(el);
  setTimeout(() => el.remove(), 3200);
}

/* 把后端的统一错误体转成一条人能看懂的提示。
   后端错误体固定是 {code, message, detail}，所以 message 一定在最外层 ——
   之前这里写的是 data.detail || data.message，detail 是对象时会拼出
   "[object Object]"，页面上看不到真正的原因。 */
function describeError(data, status) {
  const message = data && data.message ? String(data.message) : "";
  const code = data && data.code ? String(data.code) : "";
  const detail = data && data.detail ? data.detail : null;

  let extra = "";
  if (detail && typeof detail === "object") {
    if (Array.isArray(detail.fields) && detail.fields.length) {
      extra = detail.fields
        .map((f) => `${f.field || "?"}：${f.reason || "不合法"}`)
        .join("；");
    } else if (detail.trace_id) {
      extra = `trace_id=${detail.trace_id}`;
    }
  } else if (typeof detail === "string" && detail) {
    extra = detail;
  }

  const head = code ? `[${code}] ${message || `HTTP ${status}`}` : message || `HTTP ${status}`;
  return extra ? `${head}（${extra}）` : head;
}

/* 统一的 fetch 封装。
   每个写请求都自动带上 X-Operator —— 服务端靠它把操作人写进审计表。 */
async function api(path, opts = {}) {
  const headers = Object.assign({}, opts.headers || {});
  const operator = currentOperator();
  if (operator) headers["X-Operator"] = operator;

  let res;
  try {
    res = await fetch(path, Object.assign({}, opts, { headers }));
  } catch {
    // 网络层失败（断网 / 服务没起）：给一句能指导下一步的话，而不是 "Failed to fetch"
    throw new Error("网络请求失败，请检查服务是否启动或网络是否连通");
  }

  const text = await res.text();
  let data;
  try { data = text ? JSON.parse(text) : {}; } catch { data = { raw: text }; }
  if (!res.ok) {
    throw new Error(describeError(data, res.status));
  }
  return data;
}

/* esc() 是 XSS 的唯一防线。
   设备名来自外部 Excel，<img onerror=...> 这种输入完全可能存在，漏一处就是漏洞。 */
function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
function fmtTime(t) {
  if (!t) return "-";
  try { return new Date(t).toLocaleString("zh-CN", { hour12: false }); } catch { return t; }
}

/* ---------------- 三态渲染 ----------------
   每页都要有 loading / 空态 / 错误态。只写成功态的页面，
   演示时网络一抖就是一片空白，老师会以为系统崩了。 */

/* loading 态：骨架屏，不留空白 */
function renderLoading(tbody, colspan, text = "加载中…") {
  const rows = Array.from({ length: 3 }, () =>
    `<tr><td colspan="${colspan}"><div class="skeleton"></div></td></tr>`).join("");
  tbody.innerHTML = `${rows}<tr><td colspan="${colspan}" class="muted center">${esc(text)}</td></tr>`;
}

/* 空态：说清为什么空 + 给一条出路（去导入页） */
function renderEmpty(tbody, colspan, message, actionHtml = "") {
  tbody.innerHTML = `<tr><td colspan="${colspan}">
    <div class="empty">
      <div class="empty-msg">${esc(message)}</div>
      ${actionHtml}
    </div></td></tr>`;
}

/* 错误态：红提示 + 错误信息 + 重试按钮 */
function renderError(tbody, colspan, message, retryFn) {
  const retry = retryFn
    ? `<button class="btn ghost" style="margin-top:10px" data-retry="1">重试</button>`
    : "";
  tbody.innerHTML = `<tr><td colspan="${colspan}">
    <div class="error-state">
      <div class="error-title">加载失败</div>
      <div class="error-msg">${esc(message)}</div>
      ${retry}
    </div></td></tr>`;
  const btn = tbody.querySelector("[data-retry]");
  if (btn && retryFn) btn.addEventListener("click", retryFn);
}

/* 统一的错误处理：渲染错误态 + 弹 toast */
function handleListError(tbody, colspan, e, retryFn) {
  renderError(tbody, colspan, e && e.message ? e.message : String(e), retryFn);
  toast("加载失败：" + (e && e.message ? e.message : String(e)), "err");
}
