## 这个 PR 做了什么

<!-- 一句话说清，例：实现测点改名接口，改名后同步返回影响面摘要 -->

关联 issue：`G05-__`

## 我负责的角色

- [ ] 我只改了自己 `dist/roles/<角色>/` 里的文件（`git diff --name-only` 自查过）
- [ ] 改了清单外的文件，已在 issue 里与 A 确认（文件：__）

## 我做了哪些验证

- [ ] `python -m g05 preflight` → `passed: true`
- [ ] `python -m pytest -q` → 全绿，用例数 __
- [ ] 手工验证（写出具体操作步骤与结果）

### 自动化检查（CI 会跑）

- [ ] `ruff check .` → 零告警
- [ ] `python -m pytest -q --cov=src/g05 --cov-report=term-missing` → 覆盖率报告已核对
- [ ] 核心四模块（models / audit / importers / lineage）行覆盖率 ≥ 70%
      实测：__%（`python scripts/check_coverage_gate.py coverage.xml --threshold 70`）
- [ ] `python scripts/fault_inject.py --all` → 5/5 通过
- [ ] `python scripts/bench.py` → 四类指标已记录，命令见 __

> 说明：以上勾选项对应 `.github/workflows/ci.yml` 的检查项。
> 红灯必须挡住合并，CI 里没有 `continue-on-error`。
> 性能类改动请把 `scripts/bench.py` 的输出贴到「截图 / 数据」一节。

## 有没有改契约

- [ ] 没有动 `src/g05/schemas/dto.py` / `config/*.yaml` / 数据库表结构
- [ ] 动了，已在 issue 里与 A 确认（issue 链接：__）

## 截图 / 数据

<!-- 性能类改动必须附前后对比数据；UI 改动必须附截图 -->
