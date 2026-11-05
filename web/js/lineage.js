/* 血缘影响面查询与分层环形图渲染 —— 负责人：B2（血缘引擎） */
/* ---------------- 血缘 ---------------- */
async function queryImpact() {
  const key = $("#lin-key").value.trim();
  if (!key) { toast("请输入测点编号", "err"); return; }
  const dir = $("#lin-dir").value;
  try {
    const r = await api(`/api/lineage/impact?node_type=point&node_key=${encodeURIComponent(key)}&direction=${dir}`);
    showImpactResult(r);
    drawGraph({ nodes: r.nodes, edges: [] }, r.root);
  } catch (e) { toast("查询失败：" + e.message, "err"); }
}
function showImpactResult(r) {
  const sum = r.summary || {};
  $("#lin-summary").innerHTML = `
    <span class="pill blue">根节点 ${esc(r.root?.node_key || "-")}</span>
    <span class="pill ${r.total > 0 ? "warn" : "ok"}">影响 ${r.total} 个对象</span>
    ${Object.entries(sum).map(([k, v]) => `<span class="pill">${k}: ${v}</span>`).join("")}
    ${r.truncated ? '<span class="pill err">结果已截断</span>' : ""}
    ${r.cycles?.length ? '<span class="pill err">检测到循环依赖</span>' : ""}`;
  const nodes = r.nodes || [];
  if (!nodes.length) {
    $("#lin-body").innerHTML = '<tr><td colspan="6" class="empty">该测点没有下游依赖（安全改名）</td></tr>';
    return;
  }
  $("#lin-body").innerHTML = nodes.map((n) => `
    <tr>
      <td class="mono">L${n.depth}</td>
      <td><span class="pill ${n.node_type === "point" ? "blue" : n.node_type === "report" ? "warn" : "ok"}">${esc(n.node_type)}</span></td>
      <td class="mono">${esc(n.node_key)}</td>
      <td>${esc(n.node_name)}</td>
      <td>${esc(n.owner || "-")}</td>
      <td class="mono muted" style="font-size:11px">${esc((n.path || []).join(" → "))}</td>
    </tr>`).join("");
}
async function loadGraph() {
  try {
    const g = await api("/api/lineage/graph?limit=300");
    if (!g.nodes.length) { toast("血缘图为空，请先注册节点与边", "err"); return; }
    drawGraph(g, null);
    const counts = {};
    g.nodes.forEach((n) => { counts[n.type] = (counts[n.type] || 0) + 1; });
    $("#lin-summary").innerHTML = `
      <span class="pill blue">全图 ${g.nodes.length} 节点 / ${g.edges.length} 条边</span>
      ${Object.entries(counts).map(([k, v]) => `<span class="pill">${k}: ${v}</span>`).join("")}`;
    $("#lin-body").innerHTML =
      '<tr><td colspan="6" class="empty">全图模式只展示结构；输入测点编号可查看该测点的影响清单</td></tr>';
  } catch (e) { toast("加载失败：" + e.message, "err"); }
}

/* 分层环形布局：内圈 消费端(report/task/api) → 中圈 dataset → 外圈 point
   相比力导向，层级结构一目了然，且节点多时不糊成一团 */
function drawGraph(graph, root) {
  const box = $("#graph");
  const W = box.clientWidth || 900, H = box.clientHeight || 520;
  const colors = { point: "#2563eb", dataset: "#0f9d58", report: "#d97706", task: "#7c3aed", api: "#dc2626" };
  const typeLabel = { point: "测点", dataset: "数据集", report: "报表", task: "任务", api: "接口" };

  // 分层：0=最内圈
  const ringOf = { report: 0, task: 0, api: 0, dataset: 1, point: 2 };
  const groups = { 0: [], 1: [], 2: [] };
  graph.nodes.forEach((n) => groups[ringOf[n.type] ?? 2].push(n));

  const cx = W / 2, cy = H / 2;
  const rings = [
    Math.min(W, H) * 0.22,
    Math.min(W, H) * 0.34,
    Math.min(W, H) * 0.46,
  ];

  const nodes = [];
  Object.keys(groups).forEach((r) => {
    const list = groups[r];
    const radius = rings[r];
    list.forEach((n, i) => {
      // 每圈起始角错开，避免同一条半径上堆叠
      const offset = r === 2 ? Math.PI / 3 : r === 1 ? Math.PI / 6 : 0;
      const angle = (i / Math.max(list.length, 1)) * Math.PI * 2 + offset;
      // 测点多时轻微抖动，减少完全重叠
      const jitter = r === 2 && list.length > 60 ? (i % 3) * 7 : 0;
      nodes.push({
        ...n,
        x: cx + Math.cos(angle) * (radius - jitter),
        y: cy + Math.sin(angle) * (radius - jitter),
      });
    });
  });

  const byId = new Map(nodes.map((n) => [n.id, n]));
  const edges = (graph.edges || []).filter((e) => byId.has(e.source) && byId.has(e.target));
  const showAllLabels = nodes.length <= 45;

  const svgNS = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(svgNS, "svg");
  svg.setAttribute("width", W); svg.setAttribute("height", H);
  svg.style.display = "block";

  const defs = document.createElementNS(svgNS, "defs");
  defs.innerHTML = `<marker id="arrow" viewBox="0 0 10 10" refX="24" refY="5"
      markerWidth="5" markerHeight="5" orient="auto-start-reverse">
      <path d="M 0 0 L 10 5 L 0 10 z" fill="#c8d0da"/></marker>`;
  svg.appendChild(defs);

  // 边：先画，压在节点下方
  edges.forEach((e) => {
    const a = byId.get(e.source), b = byId.get(e.target);
    const line = document.createElementNS(svgNS, "line");
    line.setAttribute("x1", a.x); line.setAttribute("y1", a.y);
    line.setAttribute("x2", b.x); line.setAttribute("y2", b.y);
    line.setAttribute("stroke", "#e2e8f0");
    line.setAttribute("stroke-width", "0.9");
    line.setAttribute("marker-end", "url(#arrow)");
    svg.appendChild(line);
  });

  // 同心圆参考线
  rings.forEach((r, i) => {
    const c = document.createElementNS(svgNS, "circle");
    c.setAttribute("cx", cx); c.setAttribute("cy", cy); c.setAttribute("r", r);
    c.setAttribute("fill", "none");
    c.setAttribute("stroke", "#f0f2f5");
    c.setAttribute("stroke-dasharray", "3 4");
    svg.appendChild(c);
  });

  nodes.forEach((n) => {
    const g = document.createElementNS(svgNS, "g");
    const isRoot = root && n.node_key === root.node_key;
    const isHub = n.type !== "point";
    const circle = document.createElementNS(svgNS, "circle");
    circle.setAttribute("cx", n.x); circle.setAttribute("cy", n.y);
    circle.setAttribute("r", isRoot ? 10 : isHub ? 6.5 : 3.6);
    circle.setAttribute("fill", colors[n.type] || "#94a3b8");
    circle.setAttribute("stroke", isRoot ? "#1a1d21" : "#fff");
    circle.setAttribute("stroke-width", isRoot ? 2.5 : isHub ? 1.4 : 0.8);
    g.appendChild(circle);

    // 标签策略：节点少 → 全显示；节点多 → 只显示非测点（关键节点）
    if (showAllLabels || isHub || isRoot) {
      // 沿径向向外偏移，避免同圈标签互相压住
      const rdx = n.x - cx, rdy = n.y - cy;
      const rdist = Math.hypot(rdx, rdy) || 1;
      const pad = isHub ? 15 : 12;
      const lx = n.x + (rdx / rdist) * pad;
      const ly = n.y + (rdy / rdist) * pad;

      const label = document.createElementNS(svgNS, "text");
      const anchor = rdx > 12 ? "start" : rdx < -12 ? "end" : "middle";
      label.setAttribute("x", lx);
      label.setAttribute("y", ly + 3.5);
      label.setAttribute("text-anchor", anchor);
      label.setAttribute("font-size", isHub ? "11" : "10");
      label.setAttribute("fill", isHub ? "#3d4650" : "#5b6470");
      label.setAttribute("font-weight", isHub ? "500" : "400");
      label.textContent = n.name || n.key;
      g.appendChild(label);
    }

    const tip = document.createElementNS(svgNS, "title");
    tip.textContent = `${typeLabel[n.type] || n.type} · ${n.key}\n${n.name}${n.owner ? "\n负责人：" + n.owner : ""}`;
    g.appendChild(tip);

    svg.appendChild(g);
  });

  box.innerHTML = "";
  box.appendChild(svg);
}
