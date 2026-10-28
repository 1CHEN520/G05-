# AI 协作指南 · B1（刘涵）

> 读者：你自己。**《任务指导书-B1》是给 AI 读的，这份是给你读的。**  
> 你是全组的地基：**你的表结构定错了，上面 6 个人全要返工。**  
> 所以你的 AI 用法核心是：**先让它确认 API 版本，再让它写代码。**

---

## 一、用哪种 AI

| 优先级      | 工具形态                                                 | 具体建议             | 为什么适合你                                                     |
| -------- | ---------------------------------------------------- | ---------------- | ---------------------------------------------------------- |
| **主力**   | 能读本地文件 + 能跑命令的 Agent 型（Cursor Agent / Claude Code 等） | 任选               | 你要反复跑 `create_all`、`EXPLAIN QUERY PLAN`、`pytest`，必须让它自己看输出 |
| **主力补充** | 同一个 Agent，指定"只用 SQLAlchemy 2.0 风格"                   | 每次开新会话都重申        | AI 的训练数据里 SQLAlchemy 1.x 占绝大多数，**默认会给你 `Column()` 老写法**    |
| **备选**   | 网页对话（DeepSeek / Kimi）                                | 用来查 API 细节、看报错含义 | 但它看不到你的 `base.py`，回答可能和你已有的 `utcnow` / `Base` 不匹配          |
| **不推荐**  | 纯 Tab 补全                                             | —                | 会按 1.x 风格补全，混进项目后极难清理                                      |

**一条硬要求**：每次让它写模型代码，**开头必须加一句"只用 SQLAlchemy 2.0 的 `Mapped` / `mapped_column`，不许用 `Column()`、不许用 `declarative_base()`"**。不加这句，你有一半概率拿到 1.x 代码。

---

## 二、开工前 15 分钟

- [ ] Git 装好，能 clone
- [ ] 收到组长发的 `B1` 分发包（`dist/roles/B1/`）
- [ ] 收到两份文件：《任务指导书-B1》《AI 协作指南-B1》（就是本文）
- [ ] 想清楚一件事：**你负责的 11 个文件里，`models/base.py` 是所有人 import 的地基**，改它的成本最高

---

## 三、环境配置（约 20 分钟）

### 3.1 手动四步（照抄）

打开 **Anaconda Prompt**（不是普通 PowerShell，`conda activate` 在普通 PowerShell 里可能静默失败）。

```bat
:: 第 0 步：确认 conda 能用
conda --version

:: 第 1 步：拿到代码
cd /d D:\
git clone <组长给你的仓库地址> g05
cd g05

:: 第 2 步：建环境（已有 pp03 就跳过这两行）
conda env create -f environment.yml
conda activate pp03

:: 如果第 2 步报 conda-launchers 找不到（已知坑），改用这行：
:: conda create -n pp03 python=3.12 pip -y
:: conda activate pp03

:: 第 3 步：一键装依赖（第 2/3/4 层）
powershell -ExecutionPolicy Bypass -File .\setup_pp03.ps1

:: 第 4 步：把分发包里的文件覆盖进来（保持相对路径）
xcopy /E /Y <分发包路径>\files\* .
```

> Git Bash 用户把第 4 步换成：`cp -r <分发包路径>/files/* .`

### 3.2 把这段提示词发给 AI（让它带你走一遍）

```
我要在 Windows 上配 G05 项目的开发环境，请你当"陪跑员"，一步一步来，一次只给一步。

已知信息：
- 项目：Python 3.12 / SQLAlchemy 2.0 / FastAPI / SQLite，四层环境
- 第 1 层 conda 只装 python=3.12 + pip；第 2/3/4 层全部用 pip
- 项目根目录：D:\g05
- 已有脚本 setup_pp03.ps1 负责第 2/3/4 层

要求：
1. 每步给我：命令 + 预期输出 + 失败时怎么判断
2. 不要一次给完所有命令，我给完上一步的结果你再给下一步
3. 已知坑：conda-launchers 元数据损坏导致 env create 失败、editable 指向别的目录、普通 PowerShell 里 conda activate 静默失败
4. 最后一步必须是三条验收：pip check / import g05 路径 / pytest -q
```

**为什么强调"一次只给一步"**：AI 一次给 10 条命令，中间某条失败了你不知道是哪条，最后只看到一堆报错。

### 3.3 三条验收（必须全过）

```bat
python -m pip check
python -c "import g05; print(g05.__file__)"
python -m pytest -q
```

| 结果                               | 含义                           | 怎么办                                      |
| -------------------------------- | ---------------------------- | ---------------------------------------- |
| `No broken requirements found.`  | 依赖一致                         | 过                                        |
| 路径是 `D:\g05\src\g05\__init__.py` | editable 指向本仓库               | 过                                        |
| 路径是别的目录                          | 你或别人在别处 `pip install -e .` 过 | `pip uninstall -y g05-asset-catalog` 后重装 |
| `ModuleNotFoundError: g05`       | editable 没装                  | 回到第 3 步                                  |
| pytest 红                         | 可能是你的分发包没覆盖全                 | 检查 `src/g05/models/` 下 7 个文件在不在          |

---

## 四、项目初始化提示词（第一次对话，整段复制）

```
你是我的结对开发 AI。我是 G05 项目的 B1（数据建模与 ORM），真名刘涵。

## 硬性技术约束（每次都适用，不要违反）
- SQLAlchemy 2.0 声明式：只用 Mapped / mapped_column，禁止 Column()、禁止 declarative_base()
- 数据库 SQLite，WAL 模式，每条连接都要 PRAGMA foreign_keys=ON
- 时间统一 UTC 存储（DateTime(timezone=True) + utcnow()），展示层再转本地
- 全项目软删，不做物理删除
- 代码风格：类型注解齐全、中文注释、行宽 110

## 第一步：读文件，只输出清单，不要写代码
1. guides/任务指导书-B1.md          ← 我的岗位说明书，最重要
2. src/g05/models/base.py           ← Base / utcnow / 公共 Mixin
3. src/g05/models/device.py
4. src/g05/models/measure_point.py
5. src/g05/core/db.py               ← PRAGMA 注入与 session 工厂
6. src/g05/core/schema.py           ← 触发器 DDL
7. tests/test_models.py
8. collab/roles.yaml                ← 我负责哪 11 个文件

## 第二步：回答四个问题（找不到就说"文件里没有"，不许猜）
1. 我负责的 11 个文件分别是干什么的？逐个一句话
2. 现在的模型里，哪些外键的 ondelete 策略已经定了？理由是什么？
3. 软删 + 版本号这套机制，在"查询默认过滤"这一步是靠什么实现的？有没有漏洞？
4. 我的 5 个 issue 里，哪个最容易把别人卡住？为什么？

## 第三步：给我一张"表结构变更影响表"
如果我要给 measure_point 加一个字段，请列出：
- 哪些文件要跟着改（含不属于我的）
- 哪些人会被影响
- 需要谁批准

## 工作约定
1. 写代码前先说：改哪个文件、改什么、为什么、影响谁
2. 不许改别人名下的文件（清单见指导书第二节）
3. 不许编造命令输出，没跑过就说"未验证"
4. 每次涉及 SQLAlchemy API，先确认是 2.0 还是 1.x 的写法，不确定就说不确定
5. 中文回答
```

### 它应该怎么回答（判断有没有读懂）

| 检查点      | 合格回答                                                    |
| -------- | ------------------------------------------------------- |
| 11 个文件   | 说得出 `base.py` 是"Base + utcnow + Mixin"，`lineage.py` 是双表 |
| ondelete | 说得出测点→设备是 CASCADE 或 RESTRICT，**且给了理由**                  |
| 软删过滤     | 说出"在查询层加 `is_deleted.is_(False)`"，并指出"漏一处就会漏出已删数据"      |
| 卡别人的     | 指向 G05-08（模型），因为 C1/C2/C3/C4 全都依赖表                      |

如果它把 `Mapped` 写成 `Column`，直接打断："你用了 1.x 写法，重写。"

---

## 五、日常四类提示词

### 5.1 写模型（最常用）

```
请给 <表名> 写模型，要求：
1. SQLAlchemy 2.0 风格（Mapped / mapped_column），先给我字段表再给代码
2. 字段表列：字段名 / 类型 / 长度 / 可空 / 默认值 / 索引 / 说明
3. 每个外键写明 ondelete 策略 + 一句理由
4. 继承 base.py 里的公共 Mixin，不要重复定义 id / created_at
5. 实现 to_dict()，字段顺序与 DTO 一致
6. 最后给一条 pytest 用例，覆盖默认值与唯一约束
先只给字段表和理由，我确认后再写代码。
```

**最后一句是关键**：先看设计，再看实现。AI 直接写代码时，字段类型往往"看起来合理"但和业务对不上。

### 5.2 排错（约束/关系/PRAGMA 类问题）

```
现象：<贴完整报错>
我期望：<期望行为>
实际：<实际行为>

请：
1. 判断这是 ORM 层、SQL 层还是 PRAGMA 层的问题
2. 给一条最小验证命令（3 行内）
3. 验证通过后再给修复
4. 说明这个修复会不会影响已有的表结构 / 已有数据
```

### 5.3 性能与索引验证

```
我要验证这条查询是否走索引：
<SQL 或 SQLAlchemy 语句>

请：
1. 给我 EXPLAIN QUERY PLAN 的命令
2. 告诉我输出里出现什么关键词算"走了索引"、什么算"全表扫描"
3. 如果没走索引，建议加哪个复合索引（注意列顺序）
4. 加索引后可能拖慢什么操作
```

### 5.4 提交前自检

```
我要提交了，帮我做提交前检查（只读不改）：
1. git diff --stat：我只改了自己名下的文件吗？
2. 有没有 print / TODO / 调试代码残留？
3. 新加的字段有没有配套的测试？
4. 我改的东西会不会让别人的代码报错（尤其 C1/C2/C3/C4 的 import）？
5. 给我一条符合 type(scope): 描述的 commit message
```

---

## 六、你这个角色专属的技巧

### 1. 让 AI 先"背 API"，再写代码

```
在写任何代码前，请先用 10 行以内说明：
- SQLAlchemy 2.0 里 Mapped[int] 和 mapped_column(Integer) 的分工
- relationship() 的 back_populates 和 backref 的区别，2.0 推荐哪个
- 为什么 session.execute(select(...)) 不能写成 session.query(...)
```

它答对了再让它动手。答错说明上下文里混了 1.x 资料，换个会话。

### 2. `PRAGMA foreign_keys=ON` 是"每连接"，不是"每库"

这是全项目最容易误解的一条。让 AI 解释清楚：

```
请解释：为什么 SQLite 的 PRAGMA foreign_keys 必须在每个新连接上设置，
而不是执行一次就永久生效？如果只在建库时设置一次，会出现什么现象？
```

答不出"连接池会复用/新建连接，PRAGMA 是连接级会话变量"，说明它没真正理解——这时候它写的代码你不能信。

### 3. 用 AI 生成"数据字典"，省下 2 小时

G05-06 要交数据字典。别手写：

```
根据 src/g05/models/*.py 生成数据字典 Markdown 表格，列：表名 / 字段 / 类型 / 可空 / 默认 / 含义 / 谁维护。
含义列你要根据字段名和中文注释推断，推断不出来的写"待确认"并单独列出来。
```

**"待确认"那一节最有价值**——那就是你和 A 要对的业务口径。

### 4. 每次改模型后，跑一遍"下游影响检查"

```
我刚改了 models/measure_point.py。请检查这些地方是否还能正常工作，只读不改：
- src/g05/services/point_service.py 里对字段的引用
- src/g05/importers/ 里构造对象的代码
- src/g05/api/routes/ 里的 DTO 转换
- tests/ 下所有构造 MeasurePoint 的地方
逐条给"受影响/不受影响 + 依据行号"。
```

---

## 七、红线（对 AI 说什么都不行）

| 不许                                      | 为什么                           |
| --------------------------------------- | ----------------------------- |
| 接受 `Column()` / `declarative_base()` 写法 | 与项目风格冲突，混用后 `Mapped` 的类型检查全失效 |
| 让它"顺手"改 `schemas/dto.py` 来对齐字段          | 契约归 A，你改了会让 C4 的接口崩           |
| 用 `Float` 存金额/精确值                       | 项目里没有金额，但如果出现，必须 `Numeric`    |
| 加 `nullable=True` 来"先跑通"                | 必填字段变可空是数据质量问题，不是技术问题         |
| 物理删除（`session.delete`）                  | 全项目软删                         |
| 让它改 `core/db.py` 的 PRAGMA 配置            | 归 A。你发现要改，提 issue             |
| 相信它说"这个索引肯定有用"                          | 必须看 `EXPLAIN QUERY PLAN`      |

---

## 八、常见翻车与应对

| 现象               | 原因                            | 应对                                                         |
| ---------------- | ----------------------------- | ---------------------------------------------------------- |
| 插入孤儿测点竟然成功       | `PRAGMA foreign_keys` 没在连接上开  | 查 `core/db.py` 的 connect 事件；跑 `PRAGMA foreign_keys` 看是不是 1 |
| 关系双向不同步          | 用了 `backref` 且两边都赋值           | 统一 `back_populates`，只从一侧赋值                                 |
| 时间差 8 小时         | 存了本地时间                        | 存 UTC，展示层转；检查是不是用了 `datetime.now()` 而不是 `utcnow()`         |
| `version` 没自增    | 业务层忘了 `+1`                    | 版本号在业务层维护（C4 的 service），模型只提供字段；写进测试                       |
| 加了索引反而变慢         | 复合索引列顺序反了                     | 让 AI 解释"最左前缀"，用 `EXPLAIN` 验证                               |
| 别人 import 你的模型报错 | 你在 `models/__init__.py` 改了导出名 | `__init__.py` 的导出是公共契约，改名要通知全员                             |
