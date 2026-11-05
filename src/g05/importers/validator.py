"""YAML 规则驱动的校验引擎。

工业级要求："规则改动不改代码"。所有校验项来自 config/rules.yaml，
本模块只实现规则解释器。

校验类型：
- required      必填
- type          str | int | float | date
- length        长度区间 [min, max]
- regex         正则匹配
- enum          枚举白名单
- range         数值区间 [min, max]
- unique        列内唯一（可跨库校验）
- foreign_key   引用其他表已存在的业务键
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from g05.core.config import settings
from g05.core.logging import get_logger

log = get_logger("importers.validator")

# 结构性错误：命中任意一条即整批判不通过，不参与加权分的"补救"
# 理由：必填缺失 / 主键重复 / 外键悬空会直接破坏数据完整性，不是"质量瑕疵"
FATAL_RULES = frozenset({"required", "unique", "foreign_key"})


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
    """把 rules.yaml 编译成可执行校验器。"""

    def __init__(self, rules: dict | None = None) -> None:
        self._rules = rules if rules is not None else settings.rules
        self._settings = self._rules.get("settings", {}) or {}
        self._sheets = self._rules.get("sheets", {}) or {}
        self._scoring = self._rules.get("scoring", {}) or {}
        self._compiled_regex: dict[str, re.Pattern] = {}

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
            rows: 每行一个 dict，key 为列名
            foreign_keys: {"device.device_code": {"FAN-001", ...}} 已存在的业务键集合
            header_row_no: 表头所在行号，用于把 0-based 索引换算成 Excel 行号
        """
        rule = self.sheet_rule(sheet)
        columns: dict[str, dict] = rule.get("columns", {}) or {}
        strip = bool(self._settings.get("strip_whitespace", True))

        report = ValidationReport(
            sheet=sheet, total_rows=len(rows), threshold=self.pass_threshold
        )

        # 每列计数器，用于评分
        required_cells = 0
        filled_required = 0
        checked_cells = 0
        passed_cells = 0

        # 唯一性：列 -> 出现次数
        unique_seen: dict[str, dict[str, int]] = {
            name: {} for name, spec in columns.items() if spec.get("unique")
        }

        for idx, raw_row in enumerate(rows):
            row_no = header_row_no + idx + 1
            row_ok = True

            # 先做空白清洗
            row = {
                key: (value.strip() if strip and isinstance(value, str) else value)
                for key, value in raw_row.items()
            }

            for name, spec in columns.items():
                value = row.get(name)
                is_empty = value is None or (isinstance(value, str) and value == "")

                if spec.get("required"):
                    required_cells += 1
                    if not is_empty:
                        filled_required += 1

                # 空值：非必填则跳过后续格式校验（但默认值需要补）
                if is_empty:
                    if spec.get("required"):
                        report.issues.append(
                            CellIssue(row_no, name, "required", f"{name} 为必填项，当前为空")
                        )
                        row_ok = False
                    if "default" in spec:
                        row[name] = spec["default"]
                    continue

                checked_cells += 1
                issue = self._check_cell(name, spec, value, row_no, foreign_keys, row)
                if issue is None:
                    passed_cells += 1
                else:
                    report.issues.append(issue)
                    row_ok = False

                # 唯一性统计
                if name in unique_seen:
                    key = str(value)
                    unique_seen[name][key] = unique_seen[name].get(key, 0) + 1

            if row_ok:
                report.valid_rows += 1
            else:
                report.invalid_rows += 1
                report.bad_rows.append(row_no)

        # 唯一性违规：出现次数 > 1 的，除首次外全部标记
        for name, counter in unique_seen.items():
            dups = {k: v for k, v in counter.items() if v > 1}
            if not dups:
                continue
            flagged: set[str] = set()
            for idx, raw_row in enumerate(rows):
                value = raw_row.get(name)
                if value is None:
                    continue
                key = str(value).strip()
                if key in dups:
                    if key in flagged:
                        row_no = header_row_no + idx + 1
                        report.issues.append(
                            CellIssue(row_no, name, "unique", f"{name} 重复值: {key}", key)
                        )
                        if row_no not in report.bad_rows:
                            report.bad_rows.append(row_no)
                            report.invalid_rows += 1
                            report.valid_rows = max(0, report.valid_rows - 1)
                    flagged.add(key)

        # ---- 质量评分 ----
        completeness = (filled_required / required_cells * 100) if required_cells else 100.0
        compliance = (passed_cells / checked_cells * 100) if checked_cells else 100.0

        dup_total = sum(v - 1 for counter in unique_seen.values() for v in counter.values() if v > 1)
        unique_cells = sum(len(c) for c in unique_seen.values()) or 1
        uniqueness = max(0.0, (1 - dup_total / unique_cells) * 100)

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
        # 放行条件：分数达标 且 无结构性错误
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
        row: dict,
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

        text = str(value)

        # 2) 长度
        length = spec.get("length")
        if length and len(text) < length[0] or (length and len(text) > length[1]):
            return CellIssue(
                row_no, name, "length", f"{name} 长度应在 {length[0]}~{length[1]}，实际 {len(text)}", value
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
        if allowed and text not in allowed:
            return CellIssue(row_no, name, "enum", f"{name} 取值不在允许范围: {text}", value)

        # 5) 数值区间
        rng = spec.get("range")
        if rng:
            try:
                number = float(text)
            except ValueError:
                return CellIssue(row_no, name, "type", f"{name} 无法转为数值: {text}", value)
            if not (rng[0] <= number <= rng[1]):
                return CellIssue(
                    row_no, name, "range", f"{name} 应在 [{rng[0]}, {rng[1]}]，实际 {number}", value
                )

        # 6) 外键存在性
        fk = spec.get("foreign_key")
        if fk and foreign_keys is not None:
            known = foreign_keys.get(fk)
            if known is not None and text not in known:
                return CellIssue(
                    row_no, name, "foreign_key", f"{name} 引用的 {fk} 不存在: {text}", value
                )

        return None
