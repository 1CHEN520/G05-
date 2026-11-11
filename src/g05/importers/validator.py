"""YAML 规则驱动的校验引擎。

工业级要求："规则改动不改代码"。所有校验项来自 config/rules.yaml，
本模块只实现规则解释器 —— 新增/调整规则只改 YAML（issue G05-14）。

校验类型（issue G05-23，六类全部可执行并定位到行列）：
- required      必填
- type          str | int | float | date
- length        长度区间 [min, max]
- regex         正则匹配
- enum          枚举白名单
- range         数值区间 [min, max]
- unique        列内唯一（重复组的所有行都会被标记，便于用户改任何一处）
- foreign_key   引用其他表已存在的业务键（键集合由调用方查库后传入）

配置可读性（issue G05-14 验收）：
- 列名别名：``aliases`` 声明中文/旧列名，导入数据用别名作键也能命中规则
- YAML 写错时有明确报错行号：结构非法统一抛 ``RuleConfigError``，
  行号来自加载时的节点位置索引

质量评分与一票否决（issue G05-24）：
- 三档评分：完整性 / 合规性 / 唯一性（权重来自 YAML，总和必须为 100）
- 结构性错误（必填缺失、主键重复、外键悬空、必填列空值超限）直接判不通过，
  ``fatal_count > 0`` 时 ``passed`` 恒为 False，高分无法补救
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any

import yaml

from g05.core.config import CONFIG_DIR, settings
from g05.core.logging import get_logger

log = get_logger("importers.validator")

# 结构性错误：命中任意一条即整批判不通过，不参与加权分的"补救"
# 理由：必填缺失 / 主键重复 / 外键悬空会直接破坏数据完整性，不是"质量瑕疵"
FATAL_RULES = frozenset({"required", "unique", "foreign_key", "null_ratio"})

# 列规则允许声明的全部键（写错键名属于配置错误，直接报行号）
COLUMN_RULE_KEYS = frozenset(
    {"required", "type", "length", "regex", "enum", "range", "unique", "foreign_key", "default", "aliases"}
)
KNOWN_TYPES = frozenset({"str", "int", "float", "date"})
SETTINGS_KEYS = frozenset({"strict_header", "max_null_ratio", "strip_whitespace"})
DEFAULT_WEIGHTS = {"completeness": 40, "compliance": 35, "uniqueness": 25}


class RuleConfigError(Exception):
    """rules.yaml 结构或内容非法。

    ``problems`` 里每条都是可直接展示的一句话；
    从文件加载时带 ``第 N 行`` 定位（验收：YAML 写错时有明确报错行号）。
    """

    def __init__(self, problems: list[str]) -> None:
        self.problems = problems
        super().__init__("规则配置非法，共 %d 处:\n  - %s" % (len(problems), "\n  - ".join(problems)))


# ---------------- 行号索引：节点路径 -> 1-based 行号 ----------------

def _build_line_index(root: yaml.Node | None) -> dict[tuple[Any, ...], int]:
    """遍历 YAML 节点树，记录每个键/值出现的位置，供报错时回溯行号。"""
    index: dict[tuple[Any, ...], int] = {}

    def walk(node: yaml.Node | None, path: tuple[Any, ...]) -> None:
        if node is None:
            return
        if isinstance(node, yaml.MappingNode):
            for key_node, value_node in node.value:
                key = str(key_node.value)
                index[(*path, f":{key}")] = key_node.start_mark.line + 1
                index[(*path, key)] = value_node.start_mark.line + 1
                walk(value_node, (*path, key))
        elif isinstance(node, yaml.SequenceNode):
            for i, item in enumerate(node.value):
                index[(*path, i)] = item.start_mark.line + 1
                walk(item, (*path, i))

    walk(root, ())
    return index


def _rules_line_index(path: str | Path) -> dict[tuple[Any, ...], int]:
    """从 rules.yaml 文件构建行号索引；文件不存在返回空索引。"""
    path = Path(path)
    if not path.exists():
        return {}
    text = path.read_text(encoding="utf-8")
    try:
        root = yaml.compose(text)
    except yaml.YAMLError as exc:
        mark = getattr(exc, "problem_mark", None)
        line = mark.line + 1 if mark is not None else None
        loc = f"第 {line} 行" if line else "未知行"
        raise RuleConfigError([f"rules.yaml {loc}: YAML 解析失败 —— {exc}"]) from exc
    return _build_line_index(root)


def _load_rules_file(path: str | Path) -> tuple[dict, dict[tuple[Any, ...], int]]:
    """加载 rules.yaml，返回 (规则字典, 行号索引)；解析失败带行号抛 RuleConfigError。"""
    path = Path(path)
    text = path.read_text(encoding="utf-8")
    try:
        data = yaml.safe_load(text)
        root = yaml.compose(text)
    except yaml.YAMLError as exc:
        mark = getattr(exc, "problem_mark", None)
        line = mark.line + 1 if mark is not None else None
        loc = f"第 {line} 行" if line else "未知行"
        raise RuleConfigError([f"rules.yaml {loc}: YAML 解析失败 —— {exc}"]) from exc
    return (data or {}), _build_line_index(root)


# ---------------- 结构校验 ----------------

def _validate_rules(rules: dict, lines: dict[tuple[Any, ...], int] | None) -> None:
    """校验规则结构，非法处统一收集后抛 RuleConfigError（一次报全，别让用户改一轮报一轮）。"""
    problems: list[str] = []

    def err(path: tuple[Any, ...], msg: str) -> None:
        where = "/".join(str(p) for p in path) or "顶层"
        if lines:
            line = lines.get(path)
            loc = f"rules.yaml 第 {line} 行" if line else "rules.yaml"
        else:
            loc = "规则字典"
        problems.append(f"{loc} · {where}: {msg}")

    if not isinstance(rules, dict):
        raise RuleConfigError(["规则配置顶层必须是映射（mapping），实际 %s" % type(rules).__name__])

    for key in rules:
        if key not in {"settings", "sheets", "scoring"}:
            err((f":{key}",), f"未知顶层键 {key!r}，允许 settings / sheets / scoring")

    # ---- sheets ----
    sheets = rules.get("sheets")
    if not isinstance(sheets, dict) or not sheets:
        err((":sheets",), "sheets 必须是非空映射（按 sheet 分组的规则）")
        sheets = {}

    for sheet, sheet_rule in sheets.items():
        spath = ("sheets", str(sheet))
        if not isinstance(sheet_rule, dict):
            err(spath, "sheet 规则必须是映射")
            continue
        if not isinstance(sheet_rule.get("sheet_name"), str) or not sheet_rule["sheet_name"]:
            err((*spath, ":sheet_name"), "缺少 sheet_name（该组规则对应的 Excel 工作表名）")
        columns = sheet_rule.get("columns")
        if not isinstance(columns, dict) or not columns:
            err((*spath, ":columns"), "columns 必须是非空映射（列名 -> 规则）")
            continue

        canonical_names = set(columns.keys())
        alias_owner: dict[str, str] = {}
        for col, spec in columns.items():
            cpath = (*spath, "columns", str(col))
            if not isinstance(spec, dict):
                err(cpath, "列规则必须是映射")
                continue
            for rule_key in spec:
                if rule_key not in COLUMN_RULE_KEYS:
                    err((*cpath, f":{rule_key}"), f"未知规则键 {rule_key!r}，允许 {sorted(COLUMN_RULE_KEYS)}")

            rule_type = spec.get("type")
            if rule_type is not None and rule_type not in KNOWN_TYPES:
                err((*cpath, ":type"), f"type 只允许 {sorted(KNOWN_TYPES)}，实际 {rule_type!r}")

            for pair_key in ("length", "range"):
                pair = spec.get(pair_key)
                if pair is None:
                    continue
                valid = (
                    isinstance(pair, (list, tuple))
                    and len(pair) == 2
                    and all(isinstance(x, (int, float)) and not isinstance(x, bool) for x in pair)
                    and pair[0] <= pair[1]
                )
                if not valid:
                    err((*cpath, f":{pair_key}"), f"{pair_key} 必须是 [min, max] 且 min <= max，实际 {pair!r}")

            pattern = spec.get("regex")
            if pattern is not None:
                try:
                    re.compile(pattern)
                except re.error as exc:
                    err((*cpath, ":regex"), f"正则无法编译 —— {exc}")

            aliases = spec.get("aliases")
            if aliases is not None:
                if isinstance(aliases, str):
                    aliases = [aliases]
                if not isinstance(aliases, list) or not all(isinstance(a, str) and a.strip() for a in aliases):
                    err((*cpath, ":aliases"), "aliases 必须是非空字符串（或字符串列表）")
                else:
                    for alias in aliases:
                        alias = alias.strip()
                        if alias in canonical_names:
                            err((*cpath, ":aliases"), f"别名 {alias!r} 与已有列名冲突，等于没起别名")
                        owner = alias_owner.get(alias)
                        if owner is not None:
                            err((*cpath, ":aliases"), f"别名 {alias!r} 同时挂在 {owner} 与 {col} 上，只能挂一处")
                        else:
                            alias_owner[alias] = str(col)

    # ---- scoring ----
    scoring = rules.get("scoring")
    if scoring is not None:
        if not isinstance(scoring, dict):
            err(("scoring",), "scoring 必须是映射")
        else:
            weights = scoring.get("weights")
            if weights is not None:
                if not isinstance(weights, dict):
                    err(("scoring", ":weights"), "weights 必须是映射")
                else:
                    missing = [k for k in DEFAULT_WEIGHTS if k not in weights]
                    if missing:
                        err(("scoring", ":weights"), f"weights 缺少 {missing} 三档权重")
                    else:
                        values = [weights[k] for k in DEFAULT_WEIGHTS]
                        if any(not isinstance(v, (int, float)) or isinstance(v, bool) or v <= 0 for v in values):
                            err(("scoring", ":weights"), "三档权重都必须是正数")
                        elif sum(values) != 100:
                            err(("scoring", ":weights"), f"三档权重之和必须为 100，实际 {sum(values)}")

    # ---- settings ----
    settings_sec = rules.get("settings")
    if settings_sec is not None:
        if not isinstance(settings_sec, dict):
            err(("settings",), "settings 必须是映射")
        else:
            for key in settings_sec:
                if key not in SETTINGS_KEYS:
                    err(("settings", f":{key}"), f"未知设置键 {key!r}，允许 {sorted(SETTINGS_KEYS)}")
            ratio = settings_sec.get("max_null_ratio")
            if ratio is not None and not (isinstance(ratio, (int, float)) and 0 < ratio < 1):
                err(("settings", ":max_null_ratio"), "max_null_ratio 必须是 0~1 之间的小数")

    if problems:
        raise RuleConfigError(problems)


# ---------------- 数据结构 ----------------

@dataclass
class CellIssue:
    """单元格级问题。"""

    row_no: int          # Excel 中的行号（1-based，含表头，便于用户直接定位）
    column: str
    rule: str
    message: str
    value: Any = None

    def to_dict(self) -> dict:
        return {
            "row": self.row_no,
            "column": self.column,
            "rule": self.rule,
            "message": self.message,
            "value": None if self.value is None else str(self.value)[:80],
        }


@dataclass
class ValidationReport:
    """整批校验结果。"""

    sheet: str
    total_rows: int = 0
    valid_rows: int = 0
    invalid_rows: int = 0
    issues: list[CellIssue] = field(default_factory=list)
    bad_rows: list[int] = field(default_factory=list)
    scores: dict[str, float] = field(default_factory=dict)
    total_score: float = 0.0
    passed: bool = False
    threshold: float = 85.0
    fatal_count: int = 0

    def to_dict(self) -> dict:
        return {
            "sheet": self.sheet,
            "total_rows": self.total_rows,
            "valid_rows": self.valid_rows,
            "invalid_rows": self.invalid_rows,
            "issue_count": len(self.issues),
            "fatal_count": self.fatal_count,
            "bad_rows": sorted(set(self.bad_rows))[:500],
            "scores": self.scores,
            "total_score": round(self.total_score, 2),
            "threshold": self.threshold,
            "passed": self.passed,
            "issues": [i.to_dict() for i in self.issues[:500]],
        }

    def summary_line(self) -> str:
        verdict = "放行" if self.passed else ("打回(结构性错误)" if self.fatal_count else "打回")
        return (
            f"[{self.sheet}] 共 {self.total_rows} 行，通过 {self.valid_rows}，"
            f"问题 {self.invalid_rows} 行，得分 {self.total_score:.1f}/{self.threshold}"
            f" -> {verdict}"
        )


class RuleEngine:
    """把 rules.yaml 编译成可执行校验器。

    构造即校验：配置非法立刻抛 ``RuleConfigError``（fail-fast），
    不让坏配置等到第一条数据导入时才炸。
    """

    def __init__(self, rules: dict | None = None, *, source: str | Path | None = None) -> None:
        lines: dict[tuple[Any, ...], int] | None = None
        if rules is None and source is not None:
            # 显式指定文件：数据与行号都来自该文件
            rules, lines = _load_rules_file(source)
        elif rules is None:
            # 默认路径：数据取 settings.rules（保留环境变量覆盖能力），行号索引来自文件
            rules = settings.rules
            lines = _rules_line_index(CONFIG_DIR / "rules.yaml") or None
        self._rules = rules
        self._source = str(source) if source is not None else None
        _validate_rules(rules, lines)

        self._settings = self._rules.get("settings", {}) or {}
        self._sheets = self._rules.get("sheets", {}) or {}
        self._scoring = self._rules.get("scoring", {}) or {}
        self._compiled_regex: dict[str, re.Pattern] = {}
        self._alias_maps = {sheet: self._build_alias_map(rule) for sheet, rule in self._sheets.items()}

    @classmethod
    def from_yaml(cls, path: str | Path) -> "RuleEngine":
        """从指定 YAML 文件构建引擎（结构非法带行号报错）。"""
        return cls(source=path)

    # ---------------- 公共接口 ----------------
    @property
    def scoring_weights(self) -> dict[str, float]:
        return self._scoring.get("weights", {}) or {
            "completeness": 40,
            "compliance": 35,
            "uniqueness": 25,
        }

    @property
    def pass_threshold(self) -> float:
        return float(self._scoring.get("pass_threshold", 85))

    def sheet_rule(self, sheet: str) -> dict:
        if sheet not in self._sheets:
            raise KeyError(f"rules.yaml 未定义 sheet: {sheet}，可选 {list(self._sheets)}")
        return self._sheets[sheet]

    def expected_columns(self, sheet: str) -> list[str]:
        return list(self.sheet_rule(sheet).get("columns", {}).keys())

    def column_aliases(self, sheet: str) -> dict[str, str]:
        """返回该 sheet 的 别名 -> 规范列名 映射（只读用途）。"""
        return dict(self._alias_maps.get(sheet, {}))

    def normalize_rows(self, sheet: str, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """把行数据里的别名键换成规范列名。

        返回全新 dict（不改入参）—— 规则执行只读，与 validate() 的约定一致。
        """
        alias_map = self._alias_maps.get(sheet, {})
        normalized: list[dict[str, Any]] = []
        for row in rows:
            new_row: dict[str, Any] = {}
            for key, value in row.items():
                name = key.strip() if isinstance(key, str) else key
                name = alias_map.get(name, name)
                new_row[name] = value
            normalized.append(new_row)
        return normalized

    def validate(
        self,
        sheet: str,
        rows: list[dict[str, Any]],
        *,
        foreign_keys: dict[str, set[str]] | None = None,
        header_row_no: int = 1,
    ) -> ValidationReport:
        """校验一批已解析为 dict 的行。

        Args:
            rows: 每行一个 dict，key 为列名（规范名或 ``aliases`` 里声明的别名）
            foreign_keys: {"device.device_code": {"FAN-001", ...}} 调用方查库后传入的
                已存在业务键集合；未传入时外键规则跳过（导入器负责查库）
            header_row_no: 表头所在行号，用于把列表下标换算成 Excel 行号

        只读保证：入参 rows 及其内层 dict 不会被修改，所有清洗发生在内部副本。
        """
        rule = self.sheet_rule(sheet)
        columns: dict[str, dict] = rule.get("columns", {}) or {}
        strip = bool(self._settings.get("strip_whitespace", True))
        max_null_ratio = float(self._settings.get("max_null_ratio", 1.0))
        alias_map = self._alias_maps.get(sheet, {})

        report = ValidationReport(
            sheet=sheet, total_rows=len(rows), threshold=self.pass_threshold
        )

        # 内部副本：键别名归一 + 空白清洗（原值不动）
        cleaned_rows: list[dict[str, Any]] = []
        for raw_row in rows:
            row: dict[str, Any] = {}
            for key, value in raw_row.items():
                name = key.strip() if isinstance(key, str) else key
                name = alias_map.get(name, name)
                row[name] = value.strip() if strip and isinstance(value, str) else value
            cleaned_rows.append(row)

        # ---- 评分计数器 ----
        required_cells = 0
        filled_required = 0
        checked_cells = 0
        passed_cells = 0
        # 必填列空值计数（用于 max_null_ratio 整表否决）
        col_empty: dict[str, int] = {
            name: 0 for name, spec in columns.items() if spec.get("required")
        }
        # 唯一性：列 -> 值 -> 出现的 Excel 行号列表
        unique_seen: dict[str, dict[str, list[int]]] = {
            name: {} for name, spec in columns.items() if spec.get("unique")
        }

        # 行号 -> 该行的单元格级问题（null_ratio 属列级，不进这里）
        row_issues: dict[int, list[CellIssue]] = {}

        def add(issue: CellIssue) -> None:
            report.issues.append(issue)
            row_issues.setdefault(issue.row_no, []).append(issue)

        # ---- 第一遍：逐行逐列执行规则 ----
        for idx, row in enumerate(cleaned_rows):
            row_no = header_row_no + idx + 1

            for name, spec in columns.items():
                value = row.get(name)
                is_empty = value is None or (isinstance(value, str) and value == "")

                if spec.get("required"):
                    required_cells += 1
                    if is_empty:
                        col_empty[name] += 1
                        add(CellIssue(row_no, name, "required", f"{name} 为必填项，当前为空"))
                    else:
                        filled_required += 1

                if is_empty:
                    if "default" in spec:
                        row[name] = spec["default"]  # 只补内部副本
                    continue

                checked_cells += 1
                issue = self._check_cell(name, spec, value, row_no, foreign_keys)
                if issue is None:
                    passed_cells += 1
                else:
                    add(issue)

                if name in unique_seen:
                    unique_seen[name].setdefault(str(value), []).append(row_no)

        # ---- 第二遍：唯一性 —— 重复组的所有行都标记，用户改哪一行都行 ----
        for name, occurrences in unique_seen.items():
            for key, row_nos in occurrences.items():
                if len(row_nos) < 2:
                    continue
                first = row_nos[0]
                for row_no in row_nos:
                    hint = (
                        f"（在第 {row_nos[1]} 行再次出现）"
                        if row_no == first
                        else f"（首次出现在第 {first} 行）"
                    )
                    add(CellIssue(row_no, name, "unique", f"{name} 重复值: {key}{hint}", key))

        # ---- 第三遍：必填列空值比例超限 -> 整表否决 ----
        if report.total_rows:
            for name, empty_count in col_empty.items():
                ratio = empty_count / report.total_rows
                if ratio > max_null_ratio:
                    # 列级问题挂在表头行上，不参与 bad_rows 行定位
                    report.issues.append(
                        CellIssue(
                            header_row_no,
                            name,
                            "null_ratio",
                            f"{name} 列空值 {empty_count}/{report.total_rows} 行"
                            f"（{ratio:.0%}）超过容忍阈值 {max_null_ratio:.0%}，整表判不通过",
                        )
                    )

        # ---- 汇总坏行（列级问题不计入行定位）----
        report.bad_rows = sorted(row_issues)
        report.invalid_rows = len(report.bad_rows)
        report.valid_rows = report.total_rows - report.invalid_rows

        # ---- 质量评分（三档加权，权重来自 YAML 且已校验总和为 100）----
        completeness = (filled_required / required_cells * 100) if required_cells else 100.0
        compliance = (passed_cells / checked_cells * 100) if checked_cells else 100.0

        unique_cells = sum(len(row_nos) for row_nos in unique_seen.values())
        duplicated_cells = sum(
            len(row_nos)
            for row_nos in (v for col in unique_seen.values() for v in col.values())
            if len(row_nos) > 1
        )
        uniqueness = ((1 - duplicated_cells / unique_cells) * 100) if unique_cells else 100.0

        weights = self.scoring_weights
        total = (
            completeness * weights.get("completeness", 40)
            + compliance * weights.get("compliance", 35)
            + uniqueness * weights.get("uniqueness", 25)
        ) / 100.0

        report.scores = {
            "completeness": round(completeness, 2),
            "compliance": round(compliance, 2),
            "uniqueness": round(uniqueness, 2),
        }
        report.total_score = round(total, 2)
        report.fatal_count = sum(1 for i in report.issues if i.rule in FATAL_RULES)
        # 放行条件：分数达标 且 无结构性错误 —— 一票否决，高分救不了脏数据
        report.passed = report.total_score >= report.threshold and report.fatal_count == 0

        log.info(
            "validation.done",
            extra={
                "extra_fields": {
                    "sheet": sheet,
                    "rows": report.total_rows,
                    "invalid": report.invalid_rows,
                    "score": report.total_score,
                    "fatal": report.fatal_count,
                    "passed": report.passed,
                }
            },
        )
        return report

    # ---------------- 单元格校验 ----------------
    def _check_cell(
        self,
        name: str,
        spec: dict,
        value: Any,
        row_no: int,
        foreign_keys: dict[str, set[str]] | None,
    ) -> CellIssue | None:
        # 1) 类型
        expected_type = spec.get("type")
        if expected_type == "int":
            try:
                int(str(value))
            except (TypeError, ValueError):
                return CellIssue(row_no, name, "type", f"{name} 应为整数，实际: {value}", value)
        elif expected_type == "float":
            try:
                float(str(value))
            except (TypeError, ValueError):
                return CellIssue(row_no, name, "type", f"{name} 应为数值，实际: {value}", value)
        elif expected_type == "date":
            if not self._is_date(value):
                return CellIssue(row_no, name, "type", f"{name} 应为日期（ISO 格式），实际: {value}", value)

        text = str(value)

        # 2) 长度（显式写成区间判断，避免 and/or 优先级歧义）
        length = spec.get("length")
        if length is not None:
            low, high = length[0], length[1]
            if not (low <= len(text) <= high):
                return CellIssue(
                    row_no, name, "length", f"{name} 长度应在 {low}~{high}，实际 {len(text)}", value
                )

        # 3) 正则
        pattern = spec.get("regex")
        if pattern:
            compiled = self._compiled_regex.get(pattern)
            if compiled is None:
                compiled = re.compile(pattern)
                self._compiled_regex[pattern] = compiled
            if not compiled.match(text):
                return CellIssue(row_no, name, "regex", f"{name} 格式不合法: {text}", value)

        # 4) 枚举
        allowed = spec.get("enum")
        if allowed is not None and text not in allowed:
            return CellIssue(row_no, name, "enum", f"{name} 取值不在允许范围: {text}", value)

        # 5) 数值区间
        rng = spec.get("range")
        if rng is not None:
            try:
                number = float(text)
            except ValueError:
                return CellIssue(row_no, name, "type", f"{name} 无法转为数值: {text}", value)
            if not (rng[0] <= number <= rng[1]):
                return CellIssue(
                    row_no, name, "range", f"{name} 应在 [{rng[0]}, {rng[1]}]，实际 {number}", value
                )

        # 6) 外键存在性（键集合由调用方查库后传入；未传则跳过，导入器负责）
        fk = spec.get("foreign_key")
        if fk and foreign_keys is not None:
            known = foreign_keys.get(fk)
            if known is not None and text not in known:
                return CellIssue(
                    row_no, name, "foreign_key", f"{name} 引用的 {fk} 不存在: {text}", value
                )

        return None

    @staticmethod
    def _is_date(value: Any) -> bool:
        """Excel 单元格可能是 datetime 对象，也可能是 ISO 字符串，两者都算合法日期。"""
        if isinstance(value, (datetime, date)):
            return True
        text = str(value).strip()
        for parser in (datetime.fromisoformat, date.fromisoformat):
            try:
                parser(text)
                return True
            except ValueError:
                continue
        return False

    # ---------------- 内部 ----------------
    @staticmethod
    def _build_alias_map(sheet_rule: dict) -> dict[str, str]:
        """从列规则里收集 别名 -> 规范列名（结构校验已保证不冲突）。"""
        alias_map: dict[str, str] = {}
        for col, spec in (sheet_rule.get("columns") or {}).items():
            aliases = spec.get("aliases") if isinstance(spec, dict) else None
            if isinstance(aliases, str):
                aliases = [aliases]
            for alias in aliases or []:
                alias = alias.strip()
                if alias:
                    alias_map[alias] = str(col)
        return alias_map
