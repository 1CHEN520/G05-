# 任务指导书 · A（组长 / 架构师）

> 读者：陈锦妍的 AI。请在动手前把本文读完，并遵守文末的「边界」。
> 你的产出不是"多写代码"，而是**让别人能并行写代码，并且合起来能跑**。

---

## 一、角色卡

| 项 | 内容 |
|---|---|
| 编号 | A（A 层仅 1 人，故不编号） |
| 姓名 / 学号 | 陈锦妍 / 2025460978 |
| 岗位 | 组长 / 架构师 |
| 分支 | `feat/a-bootstrap` |
| 一句话职责 | 定契约、守骨架、做集成，对最终交付负责 |
| 前置依赖 | 无（你是所有人的上游） |
| 下游 | 全体 8 人 |

**你要交付的四件东西**：仓库骨架、接口契约、集成后的 main、答辩材料。

---

## 二、你的战场（52 个文件，按用途分六组）

```
① 工程骨架    pyproject.toml  environment.yml  requirements.txt  requirements-dev.txt
              .gitignore  .env.example  Makefile  run.bat  setup_pp03.ps1  README.md
② 配置中心    config/app.yaml  config/database.yaml  config/logging.yaml
③ 应用入口    src/g05/__init__.py  __main__.py  cli.py  main.py
              src/g05/core/{config,logging,db,schema}.py
④ 接口契约    src/g05/schemas/dto.py   src/g05/api/deps.py
⑤ 协作文档    collab/roles.yaml  issues.yaml  roster.yaml  推送指南.md
              docs/00-项目框架与分工方案.md  01-需求规格.md  02-架构设计.md  03-流程与标准映射.md
              .github/CODEOWNERS  pull_request_template.md  ISSUE_TEMPLATE/task.md
⑥ 协作脚本    scripts/make_role_bundles.py  gen_collab_docs.py  create_issues.py
              split_web_assets.py  make_roster_xlsx.py  apply_roster.py
              guides/README.md
```

包内其余 `__init__.py`、`data/*/.gitkeep`、`logs/.gitkeep` 也是你的。

**注意**：`config/rules.yaml` 不归你，归 C2；`src/g05/models/*` 归 B1 和 C3。你只**评审**，不直接改。

---

## 三、先读什么（按这个顺序，别跳）

1. `README.md` —— 项目定位、开题四问、硬指标
2. `docs/02-架构设计.md` —— 组件图、ER 图、接口契约章节
3. `src/g05/schemas/dto.py` —— 你冻结的契约本身
4. `src/g05/core/config.py` —— 配置加载与覆盖规则
5. `src/g05/main.py` —— 路由容错注册（骨架阶段为什么能起服务）
6. `src/g05/cli.py` —— `preflight` 的 hard/soft 语义
7. `collab/roles.yaml` + `collab/issues.yaml` —— 分工与任务的事实来源

---

## 四、要做的五件事（对应 issue）

### G05-01 仓库骨架与分支保护（M1，4h）

- 建**空**仓库 `g05-asset-catalog`（不要勾 README/.gitignore/license）
- 只推骨架：`dist/roles/A/files/` 的内容，不是整个仓库
- 开 main 保护：Require PR + Require approvals 1
- 建标签体系（`type/` `area/` `prio/`）与四个里程碑

**验收**：新人照 README 从零到 `preflight` 通过 ≤30 分钟；main 不能直推；`git ls-files` 里没有 `.db`/日志。

### G05-02 冻结接口契约（M1，6h）

- `dto.py` 里的 `Page[T]`、`DeviceIn/Out`、`PointIn/Out`、`RenameResult`、`ImportSummary`、`AuditOut` 是**契约**，改动必须走 PR
- 错误体统一 `{code, message, detail}`，错误码 `G05-1xxx` 参数 / `2xxx` 业务 / `5xxx` 系统
- 契约写进 `docs/02-架构设计.md` 的「接口契约」章节

**验收**：`/docs` 里每个端点有入参、出参、错误码；任何人改 DTO 都要你 review。

### G05-03 配置外置与结构化日志（M1，5h）

- `settings` 单例加载 `config/*.yaml`，支持 `G05_SERVER__PORT=9000` 这种环境变量覆盖
- 日志输出 **stderr**，JSON 行格式，带 `trace_id`；`stdout` 只留机器可读结果（preflight 的 JSON 就在这里）
- 慢查询/慢导入打 WARN，附耗时

**验收**：源码里没有裸写的阈值常量；改 `config/rules.yaml` 阈值后行为随之变化；日志每行可 `json.loads`。

### G05-38 集成联调与里程碑验收（M4，10h）

合并顺序**不能乱**，按依赖来：

```
M1  A 骨架
M2  B1 模型 → C3 审计模型 → B2 血缘表 → C2 规则 → C1 解析
M3  C4 接口 → C1 导入 → C2 校验 → B2 影响面
M4  C5 测试 → C6 文档
```

每合一个 PR，立刻在 main 上跑：

```bash
python -m g05 preflight      # passed 必须 true
python -m pytest -q          # 必须全绿
```

**绿了再合下一个。** main 一旦红了，所有人的 rebase 都跟着难受。

### G05-39 答辩材料与复盘（M4，8h）

- 一页架构图 + 一页闭环流程图（可行性 → 运维）
- 三个数字的前后对比：万行导入耗时、血缘查询耗时、测试用例数
- 分工与贡献度（`git shortlog -sn` + 贡献表）

**验收**：每个结论都能指向仓库里的具体文件或命令，不能只有 PPT。

---

## 五、技术要点（这几处最容易做错）

### 1. 路由必须容错注册

骨架阶段 `g05.api.routes.devices` 还不存在，`main.py` 不能因此崩：

```python
for _name in ROUTE_MODULES:
    try:
        _module = importlib.import_module(f"g05.api.routes.{_name}")
    except ModuleNotFoundError as _exc:
        log.warning("route.module.missing", extra={"extra_fields": {"module": _name}})
        continue
    app.include_router(_module.router, prefix=API_PREFIX)
```

**为什么**：否则组长推完骨架，服务起不来，9 个人第一天就卡住。

### 2. `preflight` 要区分 hard / soft

- **hard**（缺了就真装错）：Python 版本、依赖导入、三个基础配置、目录可写
- **soft**（缺了只是"模块还没合进来"）：`web/*`（C4）、`config/rules.yaml`（C2）、`models/*`（B1）

`passed` 只由 hard 决定，soft 进 `soft_failures` 列表。这样骨架阶段 `passed: true` 且能起服务，同时又能看出还缺什么。

### 3. 触发器只对已存在的表装

`apply_triggers()` 先查 `sqlite_master`，表不存在就跳过。否则骨架阶段 `audit_log` 还没建，`CREATE TRIGGER ... ON audit_log` 直接报错，应用起不来。

### 4. 契约一旦冻结，改法只有一种

别人要加字段 → **开 issue** → 你改 `dto.py` → 他 `git rebase origin/main`。
绝不允许"我顺手加一下，反正只是多一个字段"——这是多人项目最常见的翻车方式。

### 5. 评审 PR 的六查（每次都要走一遍）

```bash
git diff --name-only origin/main...HEAD     # ① 是否只动了他自己包里的文件
python -m g05 preflight                     # ② 自检通过
python -m pytest -q                         # ③ 全绿
git log --oneline -3                        # ④ 提交信息说清做了什么（不是 "update"）
```

⑤ 有没有偷偷改 `dto.py` / `config/*.yaml` / `models/base.py`
⑥ issue 里的验收项是否逐条勾选

---

## 六、验收（你这一角色专属）

```bash
# 1. 骨架阶段（只有 A 的包）
python -m g05 preflight
# 期望：passed=true，soft_failures = ["静态资源","校验规则","数据库与触发器"]

python -m g05 serve --port 8011
# 期望：能起服务，首页返回占位页，日志有 route.module.missing 警告

# 2. 集成完成
python -m g05 preflight        # passed=true，soft_failures 为空
python -m pytest -q            # 全绿
python -m ruff check src tests scripts   # All checks passed

# 3. 协作设施自检
python scripts/make_role_bundles.py --check     # 无"未归属文件"
python scripts/create_issues.py --dry-run       # 39 个 issue 全部列出
```

---

## 七、边界（不许越线）

| 不许做 | 原因 |
|---|---|
| 直接改别人的模块文件 | 冲突、责任不清；要改就开 issue |
| 替别人写业务代码 | 你的职责是骨架与集成，替写会让贡献度失真 |
| 把整个仓库一次推上去 | PR 变摆设，过程性证据全丢 |
| 关掉 main 分支保护图省事 | 一次误推就能毁掉别人的 rebase |
| 在答辩材料里写没跑过的数字 | 现场被要求复现就崩 |

**遇到"必须改契约"的情况**：开 issue 说明影响面 → 自己改 `dto.py` → 通知所有受影响的人 rebase。改完在 issue 里记一笔。

---

## 八、完成定义（DoD）

- [ ] main 上 `preflight` passed、`pytest` 全绿、`ruff` 干净
- [ ] 9 个分支全部合并，无遗留 PR
- [ ] `collab/issues.md` 里 39 个 issue 全部关闭或有明确遗留说明
- [ ] 需求追溯矩阵每条需求标注"已验收/遗留"
- [ ] 答辩材料可支撑 10 分钟讲解 + 6 个追问
- [ ] 贡献表 9 人填写完成

## 九、卡住了找谁

| 问题 | 找 |
|---|---|
| 表结构、约束、索引 | B1 |
| 血缘、影响面、图渲染 | B2 |
| 导入、解析 | C1 |
| 校验规则、评分 | C2 |
| 审计、触发器 | C3 |
| 接口、页面 | C4 |
| 测试、压测、CI | C5 |
| 文档、部署、运维 | C6 |
