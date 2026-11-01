/* 血缘影响面查询与分层环形图渲染 —— B2 */
async function queryImpact() {
  const key = $("#lin-key").value.trim();
  if (!key) { toast("请输入测点编号", "err"); return; }
  const direction = $("#lin-dir").value;
  try {
    const result = await api(
      `/api/lineage/impact?node_type=point&node_key=${encodeURIComponent(key)}&direction=${direction}`,
    );
    showImpactResult(result, direction);
    const nodes = result.root ? [result.root, ...(result.nodes || [])] : (result.nodes || []);
    drawGraph({ nodes, edges: result.edges || [] }, result.root);
  } catch (error) {
    toast("查询失败：" + error.message, "err");
  }
}

function showImpactResult(result, direction = "downstream") {
  const summary = result.summary || {};
  $("#lin-summary").innerHTML = `
    <span class="pill blue">根节点 ${esc(result.root?.node_key || "-")}</span>
    <span class="pill ${result.total > 0 ? "warn" : "ok"}">影响 ${result.total} 个对象</span>
    ${Object.entries(summary).map(([key, value]) => `<span class="pill">${key}: ${value}</span>`).join("")}
    ${result.truncated ? '<span class="pill err">结果已截断</span>' : ""}
    ${result.cycles?.length ? '<span class="pill err">检测到循环依赖</span>' : ""}`;
  const nodes = result.nodes || [];
  if (!nodes.length) {
    const emptyMessage = direction === "upstream"
      ? "该测点没有上游依赖"
      : "该测点没有下游依赖（安全改名）";
    $("#lin-body").innerHTML = `<tr><td colspan="6" class="empty">${emptyMessage}</td></tr>`;
    return;
  }
  $("#lin-body").innerHTML = nodes.map((node) => `
    <tr>
      <td class="mono">L${node.depth}</td>
      <td><span class="pill ${node.node_type === "point" ? "blue" : node.node_type === "report" ? "warn" : "ok"}">${esc(node.node_type)}</span></td>
      <td class="mono">${esc(node.node_key)}</td>
      <td>${esc(node.node_name)}</td>
      <td>${esc(node.owner || "-")}</td>
      <td class="mono muted" style="font-size:11px">${esc((node.path || []).join(" → "))}</td>
    </tr>`).join("");
}

async function loadGraph() {
  try {
    const graph = await api("/api/lineage/graph?limit=300");
    if (!graph.nodes.length) { toast("血缘图为空，请先注册节点与边", "err"); return; }
    drawGraph(graph, null);
    const counts = {};
    graph.nodes.forEach((node) => { counts[node.type] = (counts[node.type] || 0) + 1; });
    $("#lin-summary").innerHTML = `
      <span class="pill blue">全图 ${graph.nodes.length} 节点 / ${graph.edges.length} 条边</span>
      ${Object.entries(counts).map(([key, value]) => `<span class="pill">${key}: ${value}</span>`).join("")}`;
    $("#lin-body").innerHTML =
      '<tr><td colspan="6" class="empty">全图模式只展示结构；输入测点编号可查看该测点的影响清单</td></tr>';
  } catch (error) {
    toast("加载失败：" + error.message, "err");
  }
}

function normalizeLineageNode(node) {
  return {
    ...node,
    type: node.type || node.node_type,
    key: node.key || node.node_key,
    name: node.name || node.node_name,
  };
}

/* 分层环形布局：内圈消费端 → 中圈数据集 → 外圈测点。 */
function drawGraph(graph, root) {
  const box = $("#graph");
  const width = box.clientWidth || 900;
  const height = box.clientHeight || 520;
  const colors = {
    point: "#2563eb",
    dataset: "#0f9d58",
    report: "#d97706",
    task: "#7c3aed",
    api: "#dc2626",
  };
  const typeLabels = { point: "测点", dataset: "数据集", report: "报表", task: "任务", api: "接口" };
  const ringOf = { report: 0, task: 0, api: 0, dataset: 1, point: 2 };
  const groups = { 0: [], 1: [], 2: [] };
  (graph.nodes || []).map(normalizeLineageNode).forEach((node) => {
    groups[ringOf[node.type] ?? 2].push(node);
  });

  const centerX = width / 2;
  const centerY = height / 2;
  const minSide = Math.min(width, height);
  const rings = [minSide * 0.22, minSide * 0.34, minSide * 0.46];
  const nodes = [];
  Object.keys(groups).forEach((ring) => {
    const list = groups[ring];
    const radius = rings[ring];
    list.forEach((node, index) => {
      const offset = ring === "2" ? Math.PI / 3 : ring === "1" ? Math.PI / 6 : 0;
      const angle = (index / Math.max(list.length, 1)) * Math.PI * 2 + offset;
      const jitter = ring === "2" && list.length > 60 ? (index % 3) * 7 : 0;
      nodes.push({
        ...node,
        x: centerX + Math.cos(angle) * (radius - jitter),
        y: centerY + Math.sin(angle) * (radius - jitter),
      });
    });
  });

  const byId = new Map(nodes.map((node) => [node.id, node]));
  const edges = (graph.edges || []).filter(
    (edge) => byId.has(edge.source) && byId.has(edge.target),
  );
  const showAllLabels = nodes.length <= 45;
  const svgNamespace = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(svgNamespace, "svg");
  svg.setAttribute("width", width);
  svg.setAttribute("height", height);
  svg.style.display = "block";

  const definitions = document.createElementNS(svgNamespace, "defs");
  definitions.innerHTML = `<marker id="lineage-arrow" viewBox="0 0 10 10" refX="24" refY="5"
      markerWidth="5" markerHeight="5" orient="auto-start-reverse">
      <path d="M 0 0 L 10 5 L 0 10 z" fill="#c8d0da"/></marker>`;
  svg.appendChild(definitions);

  edges.forEach((edge) => {
    const source = byId.get(edge.source);
    const target = byId.get(edge.target);
    const line = document.createElementNS(svgNamespace, "line");
    line.setAttribute("x1", source.x);
    line.setAttribute("y1", source.y);
    line.setAttribute("x2", target.x);
    line.setAttribute("y2", target.y);
    line.setAttribute("stroke", "#e2e8f0");
    line.setAttribute("stroke-width", "0.9");
    line.setAttribute("marker-end", "url(#lineage-arrow)");
    svg.appendChild(line);
  });

  rings.forEach((radius) => {
    const circle = document.createElementNS(svgNamespace, "circle");
    circle.setAttribute("cx", centerX);
    circle.setAttribute("cy", centerY);
    circle.setAttribute("r", radius);
    circle.setAttribute("fill", "none");
    circle.setAttribute("stroke", "#f0f2f5");
    circle.setAttribute("stroke-dasharray", "3 4");
    svg.appendChild(circle);
  });

  nodes.forEach((node) => {
    const group = document.createElementNS(svgNamespace, "g");
    const isRoot = Boolean(root && node.id === root.id);
    const isHub = node.type !== "point";
    const circle = document.createElementNS(svgNamespace, "circle");
    circle.setAttribute("cx", node.x);
    circle.setAttribute("cy", node.y);
    circle.setAttribute("r", isRoot ? 10 : isHub ? 6.5 : 3.6);
    circle.setAttribute("fill", colors[node.type] || "#94a3b8");
    circle.setAttribute("stroke", isRoot ? "#1a1d21" : "#fff");
    circle.setAttribute("stroke-width", isRoot ? 2.5 : isHub ? 1.4 : 0.8);
    group.appendChild(circle);

    if (showAllLabels || isHub || isRoot) {
      const radialX = node.x - centerX;
      const radialY = node.y - centerY;
      const radialDistance = Math.hypot(radialX, radialY) || 1;
      const padding = isHub ? 15 : 12;
      const labelX = node.x + (radialX / radialDistance) * padding;
      const labelY = node.y + (radialY / radialDistance) * padding;
      const label = document.createElementNS(svgNamespace, "text");
      const anchor = radialX > 12 ? "start" : radialX < -12 ? "end" : "middle";
      label.setAttribute("x", labelX);
      label.setAttribute("y", labelY + 3.5);
      label.setAttribute("text-anchor", anchor);
      label.setAttribute("font-size", isHub ? "11" : "10");
      label.setAttribute("fill", isHub ? "#3d4650" : "#5b6470");
      label.setAttribute("font-weight", isHub ? "500" : "400");
      label.textContent = node.name || node.key;
      group.appendChild(label);
    }

    const tooltip = document.createElementNS(svgNamespace, "title");
    tooltip.textContent = `${typeLabels[node.type] || node.type} · ${node.key}\n${node.name}${
      node.owner ? "\n负责人：" + node.owner : ""
    }`;
    group.appendChild(tooltip);
    svg.appendChild(group);
  });

  box.innerHTML = "";
  box.appendChild(svg);
}
