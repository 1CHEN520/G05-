# AI 协作指南 · C3（蒋蕊娴）

> 读者：你自己。**《任务指导书-C3》是给 AI 读的，这份是给你读的。**
> 你的活只有一条判据：**审计要么 100% 可信，要么一文不值。**
> 所以你的 AI 用法核心是：**永远先问"这条记录能证明什么"，再问"怎么写"。**

---

## 一、用哪种 AI

| 优先级 | 工具形态 | 具体建议 | 为什么适合你 |
|---|---|---|---|
| **主力** | 能读文件 + 能跑命令的 Agent 型（Claude Code / Cursor Agent） | 任选 | 你要反复"造一次修改 → 看审计记录长什么样"，必须让它自己跑 |
| **主力补充** | 擅长解释机制的推理型模型 | 专门用来问 SQLAlchemy 事件生命周期 | `before_flush` / `loaded_as_persistent` 的触发时机是这一岗的技术命门，含糊不得 |
| **备选** | 网页对话（DeepSeek / Kimi） | 查 SQLite 触发器语法 | 触发器语法很小，查一下够用 |
| **不推荐** | 纯 Tab 补全 | — | 它会把你写成"改完再取旧值"，这是这一岗最经典的错误 |

**一条硬要求**：每次让它碰审计埋点，先问一句"**旧值是从哪来的？**" 答"flush 时从对象上取"就是错的。

---

## 二、开工前 15 分钟

- [ ] Git 装好，能 clone
- [ ] 收到 `C3` 分发包与两份文档
- [ ] 通读 `src/g05/audit/service.py` 的 `_install()`——三个 `for obj in session.*` 循环是核心
- [ ] 想清楚一件事：**你的代码是"别人改完数据后自动跑"的，所以你出错时别人不会发现**

---

## 三、环境配置（约 20 分钟）

### 3.1 手动四步

打开 **Anaconda Prompt**：

```bat
conda --version
cd /d D:\
git clone <组长给你的仓库地址> g05
cd g05

conda env create -f environment.yml
conda activate pp03
:: 报 conda-launchers 找不到时：conda create -n pp03 python=3.12 pip -y

powershell -ExecutionPolicy Bypass -File .\setup_pp03.ps1

xcopy /E /Y <分发包路径>\files\* .
```

### 3.2 把这段提示词发给 AI

```
我要在 Windows 上配 G05 项目环境，请当陪跑员，一次只给一步。

背景：Python 3.12 + SQLAlchemy 2.0 + FastAPI + SQLite，四层环境。
第 1 层 conda 只装 python 和 pip，其余全 pip。根目录 D:\g05，已有 setup_pp03.ps1。
已知坑：conda-launchers 元数据损坏、editable 指向别的目录、conda activate 静默失败。

每步：命令 + 预期输出 + 失败判断。
最后三条验收：pip check / import g05 路径 / pytest tests/test_audit.py -q。
额外一步：确认触发器真的装上了（给一条查询 sqlite_master 里 trigger 的命令）。
```

### 3.3 三条验收 + 一条触发器验收

```bat
python -m pip check
python -c "import g05; print(g05.__file__)"
python -m pytest tests/test_audit.py -q

:: 触发器在不在
python -c "import sqlite3,glob; p=(glob.glob('data/**/*.db',recursive=True) or ['data/g05.db'])[0]; c=sqlite3.connect(p); print(c.execute(\"select name from sqlite_master where type='trigger'\").fetchall())"
```

| 结果 | 含义 |
|---|---|
| 三条全过 | 环境 OK |
| 触发器列表里没有 `trg_audit_no_update` | 没建库或没调 `apply_triggers`，跑 `python scripts/init_db.py` |

---

## 四、项目初始化提示词（第一次对话，整段复制）

```
你是我的结对开发 AI。我是 G05 项目的 C3（审计与变更追踪），真名蒋蕊娴。

## 硬性技术约束
- 审计表 append-only：应用层不提供任何 update/delete 方法；数据库层用触发器 RAISE(ABORT)
- 旧值必须取自"对象加载时的快照"，不能在 flush 时现取（那时已经被改过了）
- 审计写入与被审计变更必须在同一事务内（回滚时审计一并回滚，不留假记录）
- SQLAlchemy 2.0 风格；SQLite + WAL
- 代码风格：类型注解齐全、中文注释、行宽 110

## 第一步：读文件，只输出清单，不要写代码
1. guides/任务指导书-C3.md            ← 我的岗位说明书，最重要
2. src/g05/models/audit_log.py        ← 表结构
3. src/g05/audit/service.py           ← 唯一写入口 + 自动埋点
4. src/g05/core/schema.py             ← 触发器 DDL（跨边界文件，归 A）
5. src/g05/api/routes/audit.py
6. tests/test_audit.py
7. src/g05/services/point_service.py  ← 业务层怎么配合我（_g05_skip_audit）

## 第二步：回答六个问题（找不到就说"文件里没有"，不许猜）
1. 一条 UPDATE 审计记录里的 old_value 是在哪个事件、哪个时刻采集的？为什么不能更晚？
2. before_flush 里为什么不能调 session.commit()？会发生什么？
3. 自动埋点和业务层显式审计怎么去重？_g05_skip_audit 在哪些循环里被检查？
4. append-only 的两道防线分别拦得住谁？只留一道会怎样？
5. verify-immutable 接口里为什么必须 rollback？不 rollback 会怎样？
6. 为什么历史查询用 business_key 而不是 record_id？

## 第三步：给我一张"审计失真清单"
列出至少 8 种"审计记录存在但不可信"的情况（比如：old_value 是改后的值、
审计与业务不同事务、操作人是 anonymous、触发器在测试库没装……），
每种说明：怎么发现 / 怎么修 / 用什么用例防复发。

## 工作约定
1. 写代码前先说：改哪个文件、改什么、为什么、影响谁
2. src/g05/core/schema.py 归 A，我要改必须走 issue + PR（CODEOWNERS 会要求 A 一起过）
3. 不许编造命令输出，没跑过就说"未验证"
4. 涉及 SQLAlchemy 事件，先确认事件名和触发时机，不确定就说不确定
5. 中文回答
```

### 它应该怎么回答（判断有没有读懂）

| 检查点 | 合格回答 |
|---|---|
| 旧值采集 | 说得出 `loaded_as_persistent` 事件，且解释"flush 时属性已改" |
| commit 递归 | 说得出"会再次触发 flush → 无限递归 / 事务状态错乱" |
| 去重 | 说得出三个循环（new/dirty/deleted）**都**要检查标记 |
| 两道防线 | 说得出应用层拦"正常代码路径"，触发器拦"直连 SQL / DBA" |
| rollback | 说得出"会话进入 aborted 状态，后续查询全报错" |
| business_key | 说得出"软删重建后 record_id 变了，历史会断" |

**如果它把旧值说成"flush 时从对象取"**，立刻纠正——这就是本项目专门写了 `_capture_old()` 的原因。

---

## 五、日常四类提示词

### 5.1 加审计覆盖（比如新增一张要审计的表）

```
我要让 <表名> 的写入自动留痕。请按顺序：
1. 加进 _AUDITED_TABLES 会有什么副作用？（会不会给已有数据产生大量审计？）
2. 业务键取哪个字段？如果没有合适的业务键怎么办？
3. 代码改动点
4. 两条用例：新增产生 INSERT 审计 / 修改产生 UPDATE 审计且 old_value 是改前的值
5. 会不会和某处的显式审计重复？怎么处理
```

### 5.2 排错（"审计没记录 / 记录错了"）

```
现象：<比如"改了设备名，审计表里有一条 UPDATE，但 old_value 和 new_value 一样">
期望：<old 是旧名，new 是新名>
实际：<两个都是新名>

请：
1. 先判断是"快照没拍"、"快照被覆盖"还是"取快照的时机错了"
2. 给一段 5 行以内的验证代码（打印对象上的 _g05_old_snapshot）
3. 验证后再改
4. 补一条防复发的用例
```

**"old 和 new 一样"是这一岗最典型的 bug**，先查 `_capture_old` 有没有生效。

### 5.3 验证不可篡改（G05-20 的验收）

```
请给我一套完整的"篡改拦截验证"，要求能直接贴进答辩材料：
1. 直接 SQL UPDATE audit_log → 期望报错，给出报错原文
2. 直接 SQL DELETE audit_log → 期望报错
3. 通过 ORM 尝试改（如果有办法）→ 期望做不到，说明为什么做不到
4. 接口 POST /api/audit/verify-immutable 的返回
5. 每项都标"拦住了 / 没拦住"，没拦住要说明原因

用表格输出。
```

### 5.4 提交前自检

```
我要提交了，做提交前检查（只读不改）：
1. git diff --stat：只改了我名下的文件吗？（core/schema.py 要单独说明）
2. 有没有给 AuditService 加了 update/delete 之类的方法？（红线）
3. 新加的分支有没有配套用例？
4. 我的改动会不会让 C4 的改名/软删产生重复审计？
5. 给我一条 type(scope): 格式的 commit message
```

---

## 六、你这个角色专属的技巧

### 1. 让 AI 画"事件时序图"，别只读代码

审计埋点涉及三个时刻，混了必错：

```
请用文字时序图说明这一次修改的完整过程：
用户改 point_name → 对象加载 → 修改属性 → flush → 审计记录生成 → commit

在每个时刻标注：
- 对象上的 point_name 是什么值
- _g05_old_snapshot 是什么值
- 审计表里有没有记录

用表格输出，不要写代码。
```

表格一对，`loaded_as_persistent` 为什么必须在"修改属性"之前，一目了然。

### 2. 让它解释"为什么审计要和业务同事务"

```
请解释：如果审计写入用了独立的事务/独立的 session，
在"业务提交成功但审计提交失败"和"业务回滚但审计已提交"两种情况下，
分别会产生什么后果？为什么这两种都不能接受？
```

答得好，说明它理解了"审计的价值在于与事实一致"；答不出，它写的代码会在边界上出问题。

### 3. `verify-immutable` 接口要防"自己把库搞坏"

```
检查 /api/audit/verify-immutable 的实现：
1. 它用的是不是独立的 session？会不会污染请求上下文？
2. 失败后有没有 rollback？
3. 如果库里一条审计都没有（MIN(id) 是 NULL），UPDATE 会怎样？
4. 错误信息有没有截断（太长会撑破前端布局）？
```

第 3 条是真空地带：空表时 `WHERE id=(SELECT MIN(id) FROM audit_log)` 匹配 0 行，`UPDATE` 成功但什么也没改，接口会返回 `blocked: false`——**误报"没拦住"**。这是要提前处理的边界。

### 4. 用 AI 检查"契约字段"

```
src/g05/api/routes/audit.py 返回的字段里，哪些被前端 web/js/pages.js 使用？
逐个核对：前端读的字段名 / 后端返回的字段名 / 是否一致。
不一致的列出来，标明谁该改。
```

审计页是 C4 写的，你改返回结构会直接让他白屏。

---

## 七、红线（对 AI 说什么都不行）

| 不许 | 为什么 |
|---|---|
| 给 `AuditService` 加 `update` / `delete` | 红线。哪怕"内部调试用"也不行 |
| 在 `before_flush` 里 `commit` / `flush` | 无限递归或事务错乱 |
| flush 时现取 old_value | 取到的是新值，审计失真 |
| 审计用独立事务 | 会出现"业务失败但审计留下"的假记录 |
| 用 `record_id` 做历史查询主轴 | 软删重建后历史断裂 |
| 未经 issue 改 `core/schema.py` | 跨边界文件，必须 A 一起 review |
| 去掉 `apply_triggers` 的表存在性判断 | 骨架阶段会起不来 |
| 相信"触发器建了就行" | 必须在**测试库**里也验证一遍 |

---

## 八、常见翻车与应对

| 现象 | 原因 | 应对 |
|---|---|---|
| `old_value` 和 `new_value` 一样 | 快照在 flush 时取 | 用 `loaded_as_persistent` 在加载时拍 |
| 一次修改产生两条审计 | 显式审计 + 自动埋点重复 | 业务层设 `_g05_skip_audit` |
| 改名后没有 RENAME 只有 UPDATE | 业务层没设标记或没调 record | 检查 `point_service.rename_point` |
| 触发器在测试里不生效 | conftest 没调 `apply_triggers` | 让 C5 补，或自己在测试里手动装 |
| 篡改测试通过了（没拦住） | 库里没数据，UPDATE 匹配 0 行 | 先插一条审计再测 |
| 回滚后审计表有残留 | 审计用了独立 session | 改回同一 session |
| 时间线顺序乱 | 用了 desc 或本地时间 | `changed_at.asc()` + UTC |
| 导出 CSV 中文乱码 | 没写 BOM | 开头写 `\ufeff` |
