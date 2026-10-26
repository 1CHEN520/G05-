/* Tab 切换与启动引导 —— 负责人：C-04（CRUD 与 API） */
/* ---------------- Tab 切换（支持 URL hash 深链接） ---------------- */
function activateTab(tab) {
  const btn = document.querySelector(`nav button[data-tab="${tab}"]`);
  if (!btn) return;
  $$("nav button").forEach((b) => b.classList.remove("active"));
  $$("section").forEach((s) => s.classList.remove("active"));
  btn.classList.add("active");
  const sec = $("#" + tab);
  if (sec) sec.classList.add("active");
  if (tab === "overview") loadOverview();
  if (tab === "devices") loadDevices(1);
  if (tab === "points") loadPoints(1);
  if (tab === "audit") loadAudit();
  if (tab === "lineage") loadGraph();
}

$$("nav button").forEach((btn) => {
  btn.onclick = () => {
    const tab = btn.dataset.tab;
    if (location.hash !== "#" + tab) {
      history.replaceState(null, "", "#" + tab);
    }
    activateTab(tab);
  };
});

window.addEventListener("hashchange", () => {
  activateTab(location.hash.replace("#", "") || "overview");
});

/* ---------------- 启动 ---------------- */
checkHealth();
const startTab = location.hash.replace("#", "") || "overview";
if (startTab !== "overview" && document.querySelector(`nav button[data-tab="${startTab}"]`)) {
  activateTab(startTab);
} else {
  loadOverview();
}
