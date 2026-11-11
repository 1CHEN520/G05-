/* Tab 切换与启动引导 —— 负责人：C4（CRUD 与 API） */

/* ---------------- Tab 切换（支持 URL hash 深链接） ---------------- */
/* switchTab 只切视图不改 hash；activateTab 切视图并按需加载数据。
   分开是为了让"从设备页点进测点页"这种程序内跳转不会污染地址栏。 */
function switchTab(tab) {
  const btn = document.querySelector(`nav button[data-tab="${tab}"]`);
  if (!btn) return;
  $$("nav button").forEach((b) => b.classList.remove("active"));
  $$("section").forEach((s) => s.classList.remove("active"));
  btn.classList.add("active");
  const sec = $("#" + tab);
  if (sec) sec.classList.add("active");
}

function activateTab(tab) {
  switchTab(tab);
  if (tab === "overview") loadOverview();
  if (tab === "devices") loadDevices(1);
  if (tab === "points") loadPoints(1);
  if (tab === "audit") loadAudit();
  if (tab === "lineage") loadGraph();
}

/* 供各页面的"去导入页"按钮调用 */
function gotoTab(tab) {
  if (location.hash !== "#" + tab) {
    history.replaceState(null, "", "#" + tab);
  }
  activateTab(tab);
}

$$("nav button").forEach((btn) => {
  btn.onclick = () => gotoTab(btn.dataset.tab);
});

window.addEventListener("hashchange", () => {
  activateTab(location.hash.replace("#", "") || "overview");
});

/* ---------------- 表格操作事件委托 ----------------
   行内按钮的 id / name 放在 data-* 属性里，由这里统一分发。
   不把用户数据拼进 onclick 的 JS 字符串：esc() 只挡 HTML 注入，
   挡不住 onclick 里逃逸出来的引号（测点名含 ' 就能注入任意 JS）。 */
document.addEventListener("click", (e) => {
  const btn = e.target.closest("[data-act]");
  if (!btn) return;
  const act = btn.dataset.act;
  const id = btn.dataset.id;
  const name = btn.dataset.name || "";
  const code = btn.dataset.code || "";

  if (act === "dev-points") viewDevicePoints(Number(id));
  else if (act === "dev-rename") renameDevice(Number(id), name);
  else if (act === "pt-rename") renamePoint(Number(id), name);
  else if (act === "pt-impact") gotoImpact(code);
});

/* ---------------- 启动 ---------------- */
initOperatorInput();
checkHealth();
const startTab = location.hash.replace("#", "") || "overview";
if (startTab !== "overview" && document.querySelector(`nav button[data-tab="${startTab}"]`)) {
  activateTab(startTab);
} else {
  loadOverview();
}
