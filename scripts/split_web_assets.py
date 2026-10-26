"""把单文件 web/index.html 拆成可分工维护的多文件结构（一次性重构脚本）。

拆分后：
  web/index.html        结构骨架（C4）
  web/css/app.css       样式（C4）
  web/js/api.js         基础工具 + fetch 封装（C4）
  web/js/pages.js       总览/设备/测点/审计/导入 页面逻辑（C4）
  web/js/lineage.js     血缘影响面 + 环形图渲染（B2）
  web/js/main.js        Tab 切换与启动（C4）
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INDEX = ROOT / "web" / "index.html"

lines = INDEX.read_text(encoding="utf-8").splitlines(keepends=True)


def seg(start: int, end: int) -> str:
    """取 1-indexed 闭区间 [start, end] 的行。"""
    return "".join(lines[start - 1 : end]).rstrip("\n") + "\n"


# 校验锚点，防止行号漂移导致拆错
assert lines[6].strip() == "<style>", lines[6]
assert lines[81].strip() == "</style>", lines[81]
assert lines[279].strip() == "<script>", lines[279]
assert lines[752].strip() == "</script>", lines[752]
assert "function drawGraph" in lines[518]
assert "async function doImport" in lines[676]

(ROOT / "web" / "css").mkdir(parents=True, exist_ok=True)
(ROOT / "web" / "js").mkdir(parents=True, exist_ok=True)

css = seg(8, 81)
(ROOT / "web" / "css" / "app.css").write_text(
    "/* G05 控制台样式 —— 负责人：C4（CRUD 与 API） */\n" + css, encoding="utf-8"
)

api_js = seg(281, 309)
(ROOT / "web" / "js" / "api.js").write_text(
    "/* 基础工具与 HTTP 封装 —— 负责人：C4（CRUD 与 API） */\n" + api_js, encoding="utf-8"
)

pages_js = seg(341, 466) + "\n" + seg(636, 743)
(ROOT / "web" / "js" / "pages.js").write_text(
    "/* 总览 / 设备 / 测点 / 审计 / 导入 页面逻辑 —— 负责人：C4（CRUD 与 API） */\n"
    + pages_js,
    encoding="utf-8",
)

lineage_js = seg(468, 634)
(ROOT / "web" / "js" / "lineage.js").write_text(
    "/* 血缘影响面查询与分层环形图渲染 —— 负责人：B2（血缘引擎） */\n" + lineage_js,
    encoding="utf-8",
)

main_js = seg(311, 339) + "\n" + seg(745, 752)
(ROOT / "web" / "js" / "main.js").write_text(
    "/* Tab 切换与启动引导 —— 负责人：C4（CRUD 与 API） */\n" + main_js,
    encoding="utf-8",
)

head = "".join(lines[:6])  # 前 6 行：doctype/head 开头
body_start = 82  # </style> 的下一行
body_end = 279  # <script> 的上一行

new_html = (
    head
    + '<link rel="stylesheet" href="css/app.css" />\n'
    + "".join(lines[body_start:body_end])
    + """<script src="js/api.js"></script>
<script src="js/pages.js"></script>
<script src="js/lineage.js"></script>
<script src="js/main.js"></script>
"""
    + "</body>\n</html>\n"
)

INDEX.write_text(new_html, encoding="utf-8")

for p in [
    "web/index.html",
    "web/css/app.css",
    "web/js/api.js",
    "web/js/pages.js",
    "web/js/lineage.js",
    "web/js/main.js",
]:
    f = ROOT / p
    print(f"{p:24s} {len(f.read_text(encoding='utf-8').splitlines()):5d} 行")
