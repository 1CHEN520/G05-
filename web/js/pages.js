/* 总览 / 设备 / 测点 / 审计 / 导入 页面逻辑 —— 负责人：C4（CRUD 与 API） */

/* ---------------- 操作人 ---------------- */
/* 页头输入框与 localStorage 双向同步：填了就带上，刷新不丢。 */
function syncOperatorUI() {
  const input = $("#op-input");
  if (!input) return;
  const saved = currentOperator();
  if (document.activeElement !== input) input.value = saved;
  const hint = $("#op-hint");
  if (hint) {
    hint.textContent = saved ? `当前操作人：${saved}` : "未填写操作人，写操作将记为 anonymous";
    hint.className = saved ? "muted" : "muted warn-text";
  }
}
function initOperatorInput() {
  const input = $("#op-input");
  if (!input) return;
  syncOperatorUI();
  input.addEventListener("change", () => setOperator(input.value));
  input.addEventListener("blur", () => setOperator(input.value));
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter") { setOperator(input.value); input.blur(); }
  });
}

/* ---------------- 健康检查 ---------------- */
async function checkHealth() {
  try {
    const h = await api("/healthz");
    $("#hdot").className = "dot " + (h.ok ? "on" : "off");
    $("#htext").textContent = h.ok ? "运行正常" : "异常";
    $("#k-db").textContent = (h.database.journal_mode || "-").toUpperCase();
    $("#health-pre").textContent = JSON.stringify(h, null, 2);
    return h;
  } catch (e) {
    $("#hdot").className = "dot off";
    $("#htext").textContent = "不可用";
    $("#health-pre").textContent = "健康检查失败：" + e.message;
    return null;
  }
}

async function loadOverview() {
  await checkHealth();
  try {
    const m = await api("/metrics");
    $("#k-devices").textContent = m.devices;
    $("#k-points").textContent = m.points;
    $("#k-audit").textContent = m.audit_entries;
  } catch (e) { /* 指标失败不影响健康状态显示 */ }
}

/* ---------------- 设备 ---------------- */
let devPage = 1;
async function loadDevices(page) {
  if (page < 1) return;
  devPage = page;
  const tbody = $("#dev-body");
  const kw = encodeURIComponent($("#dev-kw").value.trim());
  const type = encodeURIComponent($("#dev-type").value);
  renderLoading(tbody, 8);
  try {
    const d = await api(`/api/devices?page=${page}&size=20&keyword=${kw}&device_type=${type}`);
    $("#dev-count").textContent = `共 ${d.total} 条`;
    $("#dev-page-label").textContent = `第 ${d.page} 页`;
    $("#dev-prev").disabled = page <= 1;
    $("#dev-next").disabled = page * d.size >= d.total;
    if (!d.items.length) {
      // 空态要说清为什么空，并给出路
      const filtered = kw || type;
      renderEmpty(
        tbody, 8,
        filtered ? "没有匹配的设备，换个关键字或筛选条件试试" : "还没有设备，先去导入页上传一份 Excel",
        filtered ? "" : '<button class="btn" style="margin-top:10px" onclick="gotoTab(\'import\')">去导入页</button>',
      );
      return;
    }
    // 用户数据全部走 data-* 属性 + 事件委托，不拼进 onclick 的 JS 字符串 ——
    // esc() 只挡 HTML 注入，挡不住 onclick 里逃逸的引号。
    tbody.innerHTML = d.items.map((x) => `
      <tr>
        <td class="mono">${esc(x.id)}</td>
        <td class="mono">${esc(x.device_code)}</td>
        <td>${esc(x.device_name)}</td>
        <td><span class="pill blue">${esc(x.device_type)}</span></td>
        <td>${esc(x.location)}</td>
        <td><span class="pill ${x.status === "运行" ? "ok" : x.status === "停机" ? "warn" : ""}">${esc(x.status)}</span></td>
        <td class="mono">v${esc(x.version)}</td>
        <td>
          <button class="btn ghost row-btn" data-act="dev-points" data-id="${esc(x.id)}">测点</button>
          <button class="btn ghost row-btn" data-act="dev-rename" data-id="${esc(x.id)}"
                  data-name="${esc(x.device_name)}">改名</button>
        </td>
      </tr>`).join("");
  } catch (e) {
    handleListError(tbody, 8, e, () => loadDevices(page));
  }
}

function viewDevicePoints(id) {
  switchTab("points");
  loadPoints(1, id);
}

/* 设备改名：走 PATCH + exclude_unset，服务端只改传了的字段 */
async function renameDevice(id, oldName) {
  const newName = prompt(`设备 #${id} 改名\n当前名称：${oldName}\n\n请输入新名称：`);
  if (!newName || newName.trim() === oldName) return;
  try {
    await api(`/api/devices/${id}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ device_name: newName.trim() }),
    });
    toast("设备改名成功", "ok");
    loadDevices(devPage);
  } catch (e) {
    toast("改名失败：" + e.message, "err");
  }
}

/* ---------------- 测点 ---------------- */
let ptPage = 1;
async function loadPoints(page, deviceId = null) {
  if (page < 1) return;
  ptPage = page;
  const tbody = $("#pt-body");
  const kw = encodeURIComponent($("#pt-kw").value.trim());
  const type = encodeURIComponent($("#pt-type").value);
  const dev = deviceId ? `&device_id=${deviceId}` : "";
  renderLoading(tbody, 8);
  try {
    const d = await api(`/api/points?page=${page}&size=20&keyword=${kw}&point_type=${type}${dev}`);
    $("#pt-count").textContent = `共 ${d.total} 条`;
    $("#pt-page-label").textContent = `第 ${d.page} 页`;
    $("#pt-prev").disabled = page <= 1;
    $("#pt-next").disabled = page * d.size >= d.total;
    if (!d.items.length) {
      const filtered = kw || type || deviceId;
      renderEmpty(
        tbody, 8,
        filtered ? "没有匹配的测点，换个关键字或筛选条件试试" : "还没有测点，先去导入页上传一份 Excel",
        filtered ? "" : '<button class="btn" style="margin-top:10px" onclick="gotoTab(\'import\')">去导入页</button>',
      );
      return;
    }
    tbody.innerHTML = d.items.map((x) => `
      <tr>
        <td class="mono">${esc(x.id)}</td>
        <td class="mono">${esc(x.point_code)}</td>
        <td>${esc(x.point_name)}${x.renamed_from ? ` <span class="pill">原:${esc(x.renamed_from)}</span>` : ""}</td>
        <td><span class="pill blue">${esc(x.point_type)}</span></td>
        <td class="mono">${esc(x.unit)}</td>
        <td class="mono">${esc(x.device_code || x.device_id)}<div class="muted" style="font-size:11px">${esc(x.device_name || "")}</div></td>
        <td class="mono">v${esc(x.version)}</td>
        <td>
          <button class="btn ghost row-btn" data-act="pt-rename" data-id="${esc(x.id)}"
                  data-name="${esc(x.point_name)}">改名</button>
          <button class="btn ghost row-btn" data-act="pt-impact" data-code="${esc(x.point_code)}">血缘</button>
        </td>
      </tr>`).join("");
  } catch (e) {
    handleListError(tbody, 8, e, () => loadPoints(page, deviceId));
  }
}

async function renamePoint(id, oldName) {
  const newName = prompt(`测点 #${id} 改名\n当前名称：${oldName}\n\n请输入新名称：`);
  if (!newName || newName === oldName) return;
  const reason = prompt("变更原因（写入审计，可留空）：") || "测点改名";
  try {
    const r = await api(`/api/points/${id}/rename`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ new_name: newName, reason }),
    });
    const n = r.affected?.total ?? 0;
    // 空影响面也是有效信息：显示"0 个受影响"比什么都不显示更让人放心
    toast(`改名成功，版本 v${r.new_version}，影响下游 ${n} 个对象`, "ok");
    loadPoints(ptPage);
    if (n > 0) showImpactResult(r.affected);
  } catch (e) { toast("改名失败：" + e.message, "err"); }
}

function gotoImpact(code) {
  switchTab("lineage");
  $("#lin-key").value = code;
  queryImpact();
}

/* ---------------- 审计 ---------------- */
async function loadAudit() {
  const tbody = $("#au-body");
  const table = encodeURIComponent($("#au-table").value);
  const op = encodeURIComponent($("#au-op").value.trim());
  renderLoading(tbody, 8);
  try {
    const d = await api(`/api/audit?table_name=${table}&operator=${op}&limit=200`);
    if (!d.items.length) {
      renderEmpty(tbody, 8, "暂无审计记录，产生变更后会自动留痕");
      return;
    }
    const actionPill = (a) =>
      a === "INSERT" ? "ok" : a === "RENAME" ? "blue" : a.includes("DELETE") ? "err" : "warn";
    tbody.innerHTML = d.items.map((e) => `
      <tr>
        <td class="mono">${esc(e.id)}</td>
        <td class="mono" style="font-size:11px">${esc(fmtTime(e.changed_at))}</td>
        <td class="mono">${esc(e.table_name)}</td>
        <td class="mono">${esc(e.business_key || "-")}</td>
        <td><span class="pill ${actionPill(e.action)}">${esc(e.action)}</span></td>
        <td>${esc(e.operator)}</td>
        <td class="muted">${esc(e.reason || "-")}</td>
        <td class="mono" style="font-size:11px;max-width:320px;overflow:hidden;text-overflow:ellipsis"
            title="${esc(e.new_value || "")}">${esc((e.new_value || "").slice(0, 90))}</td>
      </tr>`).join("");
  } catch (e) {
    handleListError(tbody, 8, e, () => loadAudit());
  }
}

async function verifyImmutable() {
  const box = $("#immutable-box");
  try {
    const r = await api("/api/audit/verify-immutable", { method: "POST" });
    box.innerHTML = `
      <div class="card verify-card ${r.immutable ? "verify-ok" : "verify-bad"}">
        <b>${r.immutable ? "✓ 审计表不可篡改" : "✗ 存在篡改风险"}</b>
        <div class="muted" style="margin-top:6px;font-size:12px">
          ${r.checks.map((c) => `${esc(c.operation)}: ${c.blocked ? "已拦截" : "未拦截"} — ${esc(c.detail)}`).join("<br>")}
        </div>
      </div>`;
  } catch (e) {
    box.innerHTML = `<div class="card verify-bad"><b>验证失败</b><div class="muted">${esc(e.message)}</div></div>`;
    toast("验证失败：" + e.message, "err");
  }
}

/* ---------------- 导入 ---------------- */
async function doImport() {
  const f = $("#file-input").files[0];
  if (!f) { toast("请先选择 Excel 文件", "err"); return; }
  const btn = $("#import-btn");
  btn.disabled = true; btn.textContent = "导入中…";
  $("#import-result").innerHTML =
    '<div class="card"><div class="skeleton" style="height:56px"></div>' +
    '<div class="muted center" style="margin-top:10px">正在校验并导入，请稍候…</div></div>';
  const fd = new FormData();
  fd.append("file", f);
  try {
    const r = await api("/api/import/excel", { method: "POST", body: fd });
    renderImportResult(r);
    toast(r.ok ? `导入成功，耗时 ${r.elapsed_ms}ms` : "校验未通过，整批未写入", r.ok ? "ok" : "err");
    if (r.ok) loadOverview();
  } catch (e) {
    $("#import-result").innerHTML = `<div class="card verify-bad">
      <b>导入失败</b><div class="muted">${esc(e.message)}</div>
      <button class="btn ghost" style="margin-top:10px" onclick="doImport()">重试</button></div>`;
    toast("导入失败：" + e.message, "err");
  } finally {
    btn.disabled = false; btn.textContent = "开始导入";
  }
}

function renderImportResult(r) {
  const reports = r.reports || [];
  const scoreBar = (v, label) => `
    <div style="margin-bottom:8px">
      <div style="display:flex;justify-content:space-between;font-size:12px">
        <span class="muted">${esc(label)}</span><span class="mono">${esc(Number(v).toFixed(1))}</span>
      </div>
      <div class="bar"><i style="width:${Math.min(100, Number(v))}%;background:${v >= 85 ? "var(--ok)" : v >= 60 ? "var(--warn)" : "var(--err)"}"></i></div>
    </div>`;
  $("#import-result").innerHTML = `
    <div class="card ${r.ok ? "verify-ok" : "verify-bad"}">
      <b>${r.ok ? "✓ 导入成功" : "✗ 已打回"}</b>
      <span class="muted" style="margin-left:10px">${esc(r.message)} · 耗时 ${esc(r.elapsed_ms)} ms</span>
      ${r.ok ? `<div style="margin-top:8px">${Object.entries(r.inserted || {}).map(([k, v]) => `<span class="pill ok">${esc(k)}: +${esc(v)}</span>`).join(" ")}</div>` : ""}
    </div>
    ${reports.map((rep) => `
      <div class="card">
        <h2>${esc(rep.sheet)} <small>${esc(rep.total_rows)} 行 · 通过 ${esc(rep.valid_rows)} · 问题 ${esc(rep.invalid_rows)} 行</small></h2>
        <div class="split">
          <div>
            ${scoreBar(rep.scores.completeness, "完整性 (40%)")}
            ${scoreBar(rep.scores.compliance, "合规性 (35%)")}
            ${scoreBar(rep.scores.uniqueness, "唯一性 (25%)")}
            <div style="margin-top:10px;font-size:13px">
              总分 <b class="mono score-value ${rep.passed ? "score-pass" : "score-fail"}">${esc(rep.total_score)}</b>
              / 阈值 ${esc(rep.threshold)}
            </div>
          </div>
          <div>
            ${rep.issues && rep.issues.length ? `
              <div class="table-wrap" style="max-height:220px">
                <table>
                  <thead><tr><th>行号</th><th>列</th><th>规则</th><th>说明</th></tr></thead>
                  <tbody>${rep.issues.slice(0, 60).map((i) => `
                    <tr><td class="mono">${esc(i.row)}</td><td class="mono">${esc(i.column)}</td>
                        <td><span class="pill warn">${esc(i.rule)}</span></td>
                        <td class="muted" style="font-size:12px">${esc(i.message)}</td></tr>`).join("")}
                  </tbody>
                </table>
              </div>` : '<div class="empty">无问题行</div>'}
          </div>
        </div>
      </div>`).join("")}`;
}
