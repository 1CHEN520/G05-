"""校验引擎测试：规则来自 YAML，改规则不改代码。"""

from __future__ import annotations

from g05.importers.validator import RuleEngine


def _engine() -> RuleEngine:
    return RuleEngine()


def test_valid_rows_pass():
    engine = _engine()
    rows = [
        {
            "device_code": "FAN-0001",
            "device_name": "1号风机",
            "device_type": "风机",
            "location": "一厂A区",
            "status": "运行",
        }
    ]
    report = engine.validate("device", rows)
    assert report.total_rows == 1
    assert report.valid_rows == 1
    assert report.total_score >= 85
    assert report.passed is True


def test_required_field_missing_fails():
    engine = _engine()
    rows = [{"device_code": "FAN-0001", "device_name": "", "device_type": "风机", "location": "一厂A区"}]
    report = engine.validate("device", rows)
    assert report.passed is False
    assert any(i.rule == "required" for i in report.issues)


def test_regex_format_violation():
    engine = _engine()
    rows = [{"device_code": "fan-1", "device_name": "x", "device_type": "风机", "location": "一厂A区"}]
    report = engine.validate("device", rows)
    assert any(i.rule == "regex" for i in report.issues)


def test_enum_violation():
    engine = _engine()
    rows = [
        {"device_code": "FAN-0001", "device_name": "x", "device_type": "外星设备", "location": "一厂A区"}
    ]
    report = engine.validate("device", rows)
    assert any(i.rule == "enum" for i in report.issues)


def test_duplicate_key_detected():
    engine = _engine()
    rows = [
        {"device_code": "FAN-0001", "device_name": "a", "device_type": "风机", "location": "一厂A区"},
        {"device_code": "FAN-0001", "device_name": "b", "device_type": "风机", "location": "一厂B区"},
    ]
    report = engine.validate("device", rows)
    assert any(i.rule == "unique" for i in report.issues)
    assert report.invalid_rows >= 1


def test_foreign_key_violation():
    engine = _engine()
    rows = [
        {
            "device_code": "GHOST-9999",
            "point_code": "P_001",
            "point_name": "温度",
            "point_type": "温度",
            "unit": "℃",
        }
    ]
    report = engine.validate(
        "measure_point", rows, foreign_keys={"device.device_code": {"FAN-0001"}}
    )
    assert any(i.rule == "foreign_key" for i in report.issues)


def test_bad_row_numbers_are_excel_rows():
    """坏行号必须是 Excel 行号（表头第1行，数据从第2行起）。"""
    engine = _engine()
    rows = [
        {"device_code": "FAN-0001", "device_name": "ok", "device_type": "风机", "location": "一厂A区"},
        {"device_code": "BAD", "device_name": "bad", "device_type": "风机", "location": "一厂A区"},
    ]
    report = engine.validate("device", rows)
    bad = {i.row_no for i in report.issues}
    assert 3 in bad  # 第二条数据 = Excel 第 3 行


def test_scoring_weights_sum_to_100():
    engine = _engine()
    weights = engine.scoring_weights
    assert sum(weights.values()) == 100


def test_rules_are_configurable():
    """改 YAML 即改行为：临时降低阈值，格式瑕疵的数据应放行。"""
    import copy

    from g05.core.config import settings

    rules = copy.deepcopy(settings.rules)
    rules["scoring"]["pass_threshold"] = 10
    engine = RuleEngine(rules=rules)

    # 只有格式/枚举瑕疵（非结构性错误），降低阈值后应通过
    rows = [{"device_code": "bad", "device_name": "名", "device_type": "x", "location": "地址"}]
    report = engine.validate("device", rows)
    assert report.threshold == 10
    assert report.fatal_count == 0
    assert report.passed is True


def test_fatal_rule_cannot_be_offset_by_score():
    """结构性错误（必填缺失）不能被高分抵消 —— 一票否决。"""
    import copy

    from g05.core.config import settings

    rules = copy.deepcopy(settings.rules)
    rules["scoring"]["pass_threshold"] = 1  # 阈值放到最低
    engine = RuleEngine(rules=rules)

    rows = [{"device_code": "FAN-0001", "device_name": "", "device_type": "风机", "location": "一厂A区"}]
    report = engine.validate("device", rows)
    assert report.total_score >= 1
    assert report.fatal_count >= 1
    assert report.passed is False
