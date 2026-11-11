"""校验引擎测试：规则来自 YAML，改规则不改代码。"""

from __future__ import annotations

import pytest

from g05.importers.validator import RuleConfigError, RuleEngine


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


def test_type_violation():
    """type 规则：device_name 之外再给一个非整数列校验整数类型。"""
    rows = [
        {"device_code": "FAN-0001", "device_name": "x", "device_type": "风机", "location": "一厂A区"}
    ]
    # 构造自定义规则：给 status 挂 int 类型，传非整数应报 type
    import copy

    from g05.core.config import settings

    rules = copy.deepcopy(settings.rules)
    rules["sheets"]["device"]["columns"]["status"] = {"type": "int"}
    custom = RuleEngine(rules=rules)
    bad = rows[0] | {"status": "不是数字"}
    report = custom.validate("device", [bad])
    assert any(i.rule == "type" for i in report.issues)
    good = rows[0] | {"status": "42"}
    report_ok = custom.validate("device", [good])
    assert not any(i.rule == "type" for i in report_ok.issues)
    assert _engine().validate("device", rows).passed  # 原规则不受影响


def test_range_violation():
    """range 规则：数值超出区间应报错，边界值含端点。"""
    import copy

    from g05.core.config import settings

    rules = copy.deepcopy(settings.rules)
    rules["sheets"]["device"]["columns"]["status"] = {"range": [1, 10]}
    engine = RuleEngine(rules=rules)

    base = {"device_code": "FAN-0001", "device_name": "x", "device_type": "风机", "location": "一厂A区"}
    assert any(i.rule == "range" for i in engine.validate("device", [base | {"status": "11"}]).issues)
    assert any(i.rule == "range" for i in engine.validate("device", [base | {"status": "0"}]).issues)
    assert not any(i.rule == "range" for i in engine.validate("device", [base | {"status": "1"}]).issues)
    assert not any(i.rule == "range" for i in engine.validate("device", [base | {"status": "10"}]).issues)


def test_length_violation():
    """length 规则：device_code 长度超出 [2,32] 应报错（regex 之前先查长度）。"""
    engine = _engine()
    rows = [
        {"device_code": "FAN-0001", "device_name": "x", "device_type": "风机", "location": "一厂A区"},
        {"device_code": "A" * 33, "device_name": "x", "device_type": "风机", "location": "一厂A区"},
    ]
    report = engine.validate("device", rows)
    assert any(i.rule == "length" and i.row_no == 3 for i in report.issues)


def test_validator_does_not_mutate_input_rows():
    """规则执行是只读的：校验前后原始数据必须完全一致。"""
    engine = _engine()
    rows = [
        {"device_code": " fan-0001 ", "device_name": " 1号风机 ", "device_type": "风机", "location": "一厂A区", "status": None}
    ]
    import copy

    snapshot = copy.deepcopy(rows)
    report = engine.validate("device", rows)
    assert rows == snapshot  # strip 只发生在内部副本，不改入参
    assert any(i.rule == "regex" for i in report.issues)  # 原值未清洗，仍被正则拦住


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


# ==================== C2 重做新增（G05-14 / G05-23 / G05-24 DoD）====================


def _good_device_row(**overrides):
    """一条合法设备行，供多行构造用。"""
    row = {
        "device_code": "FAN-0001",
        "device_name": "1号风机",
        "device_type": "风机",
        "location": "一厂A区",
    }
    row.update(overrides)
    return row


def test_high_score_but_missing_required_is_rejected():
    """G05-24 验收：构造"总分超过阈值但有必填缺失"的数据，必须被打回。"""
    import copy

    from g05.core.config import settings

    rules = copy.deepcopy(settings.rules)
    rules["scoring"]["pass_threshold"] = 85
    engine = RuleEngine(rules=rules)

    rows = [_good_device_row(device_code=f"FAN-{i:04d}") for i in range(1, 10)]
    rows.append(_good_device_row(device_code="FAN-0010", device_name=""))  # 唯一的必填缺失
    report = engine.validate("device", rows)

    assert report.total_score >= report.threshold  # 分数确实达标
    assert report.fatal_count >= 1  # 但必填缺失是结构性错误
    assert report.passed is False  # 一票否决，高分救不了


def test_fatal_count_gt_zero_means_passed_false():
    """G05-24 验收：fatal_count > 0 时 passed 恒为 false（unique / foreign_key 同样适用）。"""
    engine = RuleEngine()
    rows = [
        _good_device_row(device_code="FAN-0001"),
        _good_device_row(device_code="FAN-0001"),  # 主键重复
    ]
    report = engine.validate("device", rows)
    assert report.fatal_count > 0
    assert report.passed is False


def test_unique_flags_all_duplicate_rows():
    """重复组的每一行都要被标记（用户改任何一行都能消除重复），且行号可定位。"""
    engine = RuleEngine()
    rows = [
        _good_device_row(device_code="FAN-0001"),
        _good_device_row(device_code="FAN-0002"),
        _good_device_row(device_code="FAN-0001", device_name="改名风机"),
    ]
    report = engine.validate("device", rows)
    unique_rows = {i.row_no for i in report.issues if i.rule == "unique"}
    assert unique_rows == {2, 4}  # 首次出现（第2行）与再次出现（第4行）都被标记
    assert report.passed is False


def test_alias_columns_resolve():
    """G05-14 验收：规则按 sheet 分组并支持列名别名 —— 用中文别名作表头也能命中规则。"""
    engine = RuleEngine()
    rows = [
        {
            "设备编号": "FAN-0001",
            "设备名称": "1号风机",
            "设备类型": "风机",
            "安装位置": "一厂A区",
        }
    ]
    report = engine.validate("device", rows)
    assert report.issues == []  # 别名归一后全部命中规则
    assert report.passed is True
    assert report.valid_rows == 1


def test_alias_violation_still_caught():
    """别名只改列名映射，不改校验强度：别名列里的坏值照样被拦。"""
    engine = RuleEngine()
    rows = [{"设备编号": "fan-1", "设备名称": "x", "设备类型": "风机", "安装位置": "一厂A区"}]
    report = engine.validate("device", rows)
    assert any(i.rule == "regex" and i.column == "device_code" for i in report.issues)


def test_normalize_rows_does_not_mutate_input():
    """normalize_rows 返回新结构，别名归一不改入参。"""
    engine = RuleEngine()
    rows = [{"设备编号": "FAN-0001", "设备名称": "1号风机", "设备类型": "风机", "安装位置": "一厂A区"}]
    normalized = engine.normalize_rows("device", rows)
    assert "device_code" in normalized[0]
    assert "设备编号" in rows[0]  # 原数据不动


_BAD_RULES_YAML = """\
settings:
  strip_whitespace: true
sheets:
  device:
    sheet_name: "设备"
    columns:
      device_code:
        required: true
        colour: red
"""


def test_yaml_error_reports_line_number(tmp_path):
    """G05-14 验收：YAML 写错时有明确报错行号。"""
    rules_file = tmp_path / "rules.yaml"
    rules_file.write_text(_BAD_RULES_YAML, encoding="utf-8")
    with pytest.raises(RuleConfigError) as excinfo:
        RuleEngine.from_yaml(rules_file)
    msg = str(excinfo.value)
    assert "第 9 行" in msg  # colour: red 在第 9 行
    assert "colour" in msg


def test_yaml_syntax_error_reports_line_number(tmp_path):
    """YAML 语法错误（缩进/结构）同样带行号。"""
    rules_file = tmp_path / "rules.yaml"
    rules_file.write_text("sheets:\n  device:\n   sheet_name: [\"坏\", 结构", encoding="utf-8")
    with pytest.raises(RuleConfigError) as excinfo:
        RuleEngine.from_yaml(rules_file)
    assert "行" in str(excinfo.value)


def test_from_yaml_happy_path(tmp_path):
    """合法规则文件正常加载并生效。"""
    rules_file = tmp_path / "rules.yaml"
    rules_file.write_text(
        "\n".join(
            [
                "sheets:",
                "  device:",
                '    sheet_name: "设备"',
                "    columns:",
                "      device_code:",
                "        required: true",
                "        regex: \"^[A-Z]+$\"",
            ]
        ),
        encoding="utf-8",
    )
    engine = RuleEngine.from_yaml(rules_file)
    assert engine.expected_columns("device") == ["device_code"]
    rows = [{"device_code": "GOOD"}, {"device_code": "bad"}]
    report = engine.validate("device", rows)
    assert any(i.rule == "regex" for i in report.issues)


def test_unknown_rule_key_rejected():
    """规则键拼错（如 regix）应在构造时立刻报错，而不是被静默忽略。"""
    import copy

    from g05.core.config import settings

    rules = copy.deepcopy(settings.rules)
    rules["sheets"]["device"]["columns"]["device_name"] = {"required": True, "regix": ".*"}
    with pytest.raises(RuleConfigError) as excinfo:
        RuleEngine(rules=rules)
    assert "regix" in str(excinfo.value)


def test_alias_conflict_between_columns_rejected():
    """同一别名挂两列 = 校验结果不可预期，构造时必须报错。"""
    import copy

    from g05.core.config import settings

    rules = copy.deepcopy(settings.rules)
    rules["sheets"]["device"]["columns"]["device_name"]["aliases"] = ["设备编号"]
    with pytest.raises(RuleConfigError) as excinfo:
        RuleEngine(rules=rules)
    assert "设备编号" in str(excinfo.value)


def test_weights_must_sum_to_100():
    """权重之和不是 100 的配置直接拒绝（评分口径必须能对账）。"""
    import copy

    from g05.core.config import settings

    rules = copy.deepcopy(settings.rules)
    rules["scoring"]["weights"] = {"completeness": 50, "compliance": 50, "uniqueness": 50}
    with pytest.raises(RuleConfigError) as excinfo:
        RuleEngine(rules=rules)
    assert "100" in str(excinfo.value)


def test_invalid_regex_pattern_rejected():
    """写错的正则在构造时报行号级错误，而不是导入第一条数据时才炸。"""
    import copy

    from g05.core.config import settings

    rules = copy.deepcopy(settings.rules)
    rules["sheets"]["device"]["columns"]["device_name"]["regex"] = "[未闭合"
    with pytest.raises(RuleConfigError):
        RuleEngine(rules=rules)


def test_range_bounds_must_be_ordered():
    """range/length 区间写反（min > max）属于配置错误。"""
    import copy

    from g05.core.config import settings

    rules = copy.deepcopy(settings.rules)
    rules["sheets"]["device"]["columns"]["device_name"] = {"required": True, "range": [10, 1]}
    with pytest.raises(RuleConfigError):
        RuleEngine(rules=rules)


def test_date_type_rule():
    """type: date —— datetime 对象与 ISO 字符串通过，普通文本被拦。"""
    import copy
    from datetime import datetime

    from g05.core.config import settings

    rules = copy.deepcopy(settings.rules)
    rules["sheets"]["device"]["columns"]["status"] = {"type": "date"}
    engine = RuleEngine(rules=rules)
    base = _good_device_row()
    assert not any(i.rule == "type" for i in engine.validate("device", [base | {"status": datetime(2026, 1, 2, 10, 0)}]).issues)
    assert not any(i.rule == "type" for i in engine.validate("device", [base | {"status": "2026-01-02"}]).issues)
    bad = engine.validate("device", [base | {"status": "二零二六年"}])
    assert any(i.rule == "type" and "日期" in i.message for i in bad.issues)


def test_new_rule_only_yaml_change():
    """G05-14 验收：新增一条规则只改 YAML，不改 Python —— 行为立刻生效。"""
    import copy

    from g05.core.config import settings

    rules = copy.deepcopy(settings.rules)
    rules["sheets"]["device"]["columns"]["location"]["regex"] = "^[\\u4e00-\\u9fa5]+"  # 位置必须纯中文
    engine = RuleEngine(rules=rules)
    assert any(
        i.rule == "regex" and i.column == "location"
        for i in engine.validate("device", [_good_device_row(location="Plant A")]).issues
    )
    assert engine.validate("device", [_good_device_row(location="一厂区")]).passed


def test_max_null_ratio_rejects_whole_sheet():
    """settings.max_null_ratio：必填列空值比例超限 -> 整表判不通过（列级 null_ratio 问题）。"""
    import copy

    from g05.core.config import settings

    rules = copy.deepcopy(settings.rules)
    rules["settings"]["max_null_ratio"] = 0.3
    engine = RuleEngine(rules=rules)
    rows = [
        _good_device_row(device_name="有名字"),
        _good_device_row(device_name=""),
        _good_device_row(device_name=""),
        _good_device_row(device_name=""),
    ]
    report = engine.validate("device", rows)
    null_issues = [i for i in report.issues if i.rule == "null_ratio"]
    assert null_issues and null_issues[0].column == "device_name"
    assert report.fatal_count >= 1
    assert report.passed is False


def test_max_null_ratio_not_triggered_below_threshold():
    """空值比例在容忍范围内时不产生 null_ratio（数据靠 fatal 语义正常判）。"""
    import copy

    from g05.core.config import settings

    rules = copy.deepcopy(settings.rules)
    rules["settings"]["max_null_ratio"] = 0.5
    engine = RuleEngine(rules=rules)
    rows = [_good_device_row(), _good_device_row(device_name="")]
    report = engine.validate("device", rows)
    assert not any(i.rule == "null_ratio" for i in report.issues)
    # 必填缺失仍是结构性错误 -> 不放行
    assert report.passed is False


def test_sheet_missing_error_mentions_available_sheets():
    """sheet 名写错时给出可用清单，方便定位是 sheet 分组问题。"""
    engine = RuleEngine()
    with pytest.raises(KeyError) as excinfo:
        engine.validate("devices", [_good_device_row()])
    assert "device" in str(excinfo.value)


def test_bad_rows_are_sorted_and_unique():
    """bad_rows 必须是去重排序后的 Excel 行号，供打回单直接使用。"""
    engine = RuleEngine()
    rows = [
        _good_device_row(device_code="BAD", device_name=""),
        _good_device_row(device_code="WORSE", device_name=""),
    ]
    report = engine.validate("device", rows)
    assert report.bad_rows == [2, 3]
    assert report.invalid_rows == 2
    assert report.valid_rows == 0
