# AGENTS.md — G05 设备台账与测点资产目录

> 给所有参与本项目的 AI 助手。
> 这份文件不是"开发指南"，是**踩坑清单 + 纪律**。
> 每条都对应一次真实事故，带验证方法。请按它办事，别按"看起来应该这样"办事。
>
> 人物分工与开工提示词见 `guides/AI协作指南-<角色>.md`；
> 本文只写**那些指南没覆盖、但推进过程中真实出过事**的部分。

---

## 0. 这个仓库的真实形态（先读这段，会改变你的判断）

### 两条分支线，语义完全不同

| 分支 | 是什么 | 不要 |
|---|---|---|
| `main` | **集成线**。含功能层基线 + 各人的 PR | 不要拿来当"我的起点" |
| `master` | **发包线**。初始提交 `9b10869`（作者 G05 Team）就含**全套参考实现** | 不要把它当"最新代码" |

**关键事实**：`master` 在初始提交里就有路由 / 导入器 / 校验 / 审计 / 血缘 / 前端。
分包是把这份参考实现切给各人当起点，**不是从零实现**。

所以：**功能存在 ≠ 某人做过这件事。** 判断谁贡献了什么，只看 PR，不看 main 上有什么。

### 各人的战场

唯一权威：`collab/roles.yaml`（**清单即契约**）。
```bash
python scripts/make_role_bundles.py --check   # 退出码非 0 = 有文件没归属
python scripts/gen_collab_docs.py             # 改完 roles.yaml 必须重跑（派生文档）
```

---

## 1. 归属规则（今晚最大的教训，先读这条）

### 1.1 文件归属：只改你名下的

```bash
# 查某个文件归谁（把路径换成你要改的）
python - << 'PY'
import yaml, fnmatch
d = yaml.safe_load(open('collab/roles.yaml', encoding='utf-8'))
for tgt in ['src/g05/api/routes/devices.py', 'AGENTS.md']:
    owner = '—'
    for it in d['roles']:
        for pat in it.get('owns', []):
            if fnmatch.fnmatch(tgt, pat):
                owner = it['code']; break
        if owner != '—': break
    print(f'  {tgt} → {owner}')
PY
```

实测输出（就是这几行，别猜）：

```
  src/g05/api/routes/devices.py → C4
  AGENTS.md → A
```

注意别写成 `input()` 读 stdin —— 通过管道喂进去会 `EOFError`。
上面这种"把路径写在列表里"的形式在任何 shell 下都能跑。

**改别人的文件前，先在 issue 里说明为什么。** 核心层核心规则（事务 / ORM / 血缘 / 性能）
归属 B 层；跨层修改必须先开 issue 再改。

### 1.2 issue 归属：**只有本人 PR 合入 main 才能关**

这是集成验收时犯过的错：逐条跑了各角色的验收标准，基线实现确实满足，
于是把 C1/C2/C4/C6 的 issue 关了 —— **等于把别人的待办清单划掉了。**

判据（写死）：

| 情况 | issue 状态 |
|---|---|
| 只有基线实现，**该人 PR 未合入** | **保持 OPEN** |
| **该人的 PR 已合入 main** | CLOSED |
| 验收标准确实没满足 | OPEN + 写明差在哪 |

**判据的理由**：issue 追踪的是"这一块活交付了没有"，不是"功能是否存在"。
要关别人的 issue，先查他的分支有没有合进 main：
```bash
gh api repos/<owner>/<repo>/compare/main...feat/<分支> --jq '.ahead_by'
# ahead_by = 0 才是真的没动
```

### 1.3 文档里也分开写

`docs/需求追溯矩阵.md` 有两列，**不要合并**：
- **状态** = 功能是否达标（基线已满足）
- **个人产出** = 该角色自己做了没有（⏳ = 待本人 PR）

答辩材料里的贡献表按**实际作者归属**统计（C5 的提交曾记在我名下，
因为我推送用的自己的凭据 —— 答辩时必须主动说明，别让评委以为是统计错误）。

---

## 2. 契约冻结：这几个文件不能私自改

| 文件 | 内容 | 改它之前 |
|---|---|---|
| `src/g05/schemas/dto.py` | 接口契约 | 必须走 PR 并经 A review |
| `src/g05/schemas/errors.py` | 24 个错误码 + 状态码映射 | 同上 |
| `collab/roles.yaml` | 文件归属 | 补完必须 `gen_collab_docs.py` |
| `collab/issues.yaml` | 任务清单 | 由脚本生成 `issues.md` |
| `config/*.yaml` | 配置 | 阈值改动走 PR |

**改契约前，先看别人有没有依赖。**
#46 把参数校验错误的状态码从 422 改成 400，C5 的 3 条用例立刻红了
（`assert 400 == 422`）—— 因为参考实现和 C5 的 99 条用例一直按 422 断言。
#48 已校正回 422。**这是 FastAPI 对"语法对但语义不合法"的既定语义。**

---

## 3. 环境与运行

### 3.1 唯一可信解释器

```bash
/d/Anacoda/Anac/envs/pp03/python.exe --version   # 必须 3.12

# 四步（与 README 一致，别省步骤）
python -m pip install -r requirements.txt                      # 运行依赖（已锁版本）
python -m pip install -r requirements-dev.txt                  # 开发工具
python -m pip install --no-deps --no-build-isolation -e .      # 项目本体
python -m pip check                                            # 装完本体后再查冲突

# 自检 / 测试 / 静态检查
python -m g05 preflight
python -m pytest -q
python -m ruff check src tests scripts
```

- **CI 用 py3.12**，本地 pp03 也是 3.12 —— 不用两套。
- Python 3.12 起 `setuptools` 不随解释器分发，CI 里要 `pip install setuptools wheel`。
- `pip check` 要在 editable 安装**之后**跑（装本体之前查不出自家依赖冲突）。
- **顺序不能反**：先装 `requirements*.txt`，再 `-e .`。
  `--no-deps` 只作用在项目本体这一步，**不是叫你别装依赖**。
- **不要只用 `-e .`**（不带 requirements）—— 会漏掉 SQLAlchemy / FastAPI 等运行依赖，
  报 `No module named g05` 或 import 失败。

### 3.2 preflight 的判据（别搞错）

```
passed: true, hard_failures: [], soft_failures: []   ← 这才是集成完成
```

`passed=true` 但 `soft_failures` 非空 = **环境齐了但功能没接上**，不算数。

### 3.3 ⚠ 多 worktree 并存时，editable install 会静默错位

踩过：在项目目录跑 `python -m g05 initdb`，库却建到了另一个 worktree 里，
表现为"表和触发器都没装上"（实际是写错了地方）。

排查前先确认指向：
```bash
python -c "import g05.core.config as c; print(c.PROJECT_ROOT)"
# 不等于当前目录的话，清掉多余 worktree 再重装
```

---

## 4. 验收纪律：验收标准必须逐条跑命令

**"文档写着做到了" ≠ "做到了"。** 推进过程中发现的真实缺陷，都是"没人实际执行那条命令"：

| 验收标准原文 | 实测发现 |
|---|---|
| 日志每行可被 json.loads 解析 | **6959/14746 行解析失败**（引号没转义） |
| /docs 每个端点有错误码说明 | **0 个端点**声明了业务错误码 |
| 500 响应体没有堆栈、**但有 trace_id** | 堆栈确实没有，**trace_id 也没有** |
| 追溯矩阵每条需求至少映射到一个 issue | **27/28 行**没有 issue 引用 |
| 导出 Excel 能被 Excel 打开且中文不乱码 | `reports/__init__.py` 是空文件，**端点根本没做** |
| CI 红灯能挡住合并 | 必过检查列表**是空的**（已补） |

**给后来者的规矩**：
1. 关 issue 前，**把它验收标准里的命令逐条跑一遍**，把输出贴进评论。
2. 只"读代码觉得对"不算证据。
3. 没做就说没做（像 #32 那样），不要用"功能等价"糊弄。

### 4.1 守护测试要"验过能抓得到"

写了 18 条守护测试后，**临时把实现回退成旧行为**，确认测试真的 FAILED，
再恢复。**不然测试可能只是"看起来在测"。**

```bash
# 临时改坏实现 → 跑测试 → 必须 FAILED → 恢复 → 必须 PASS
```

---

## 5. GitHub 协作实测坑（都踩过）

### 5.1 gh CLI 登录的是谁 ≠ 你在替谁干活

```bash
gh auth status        # 可能登录的是别人（C5 的号），实际是 A 在操作
gh api user --jq '.login'
```

推送用的是 **Git Credential Manager 里的 PAT**（和 gh CLI 登录可以不是同一个号）：
```bash
TOK=$(printf 'protocol=https\nhost=github.com\n\n' | git credential-manager get 2>/dev/null | grep '^password=' | cut -d= -f2-)
export GH_TOKEN="$TOK"
```

**后果**：C5 的提交 git author 记成了我。**给别人的分支提交时先确认 author。**

### 5.2 gh api 传含 `&` 的 JSON 会被 bash 搞坏

```bash
# ❌ 会失败
gh api ... -f body='{"a": "lint & test (py3.12)"}'
# ✅ 用文件
gh api ... --input /tmp/payload.json
```

### 5.3 `gh pr merge` 遇 502 会卡死，改用 REST

```
GraphQL: Merge already in progress → 之后一直 Something went wrong
```

**改用 REST，立刻生效：**
```bash
gh api repos/<owner>/<repo>/pulls/<N>/merge -X PUT \
  -f merge_method=merge -f sha=<完整40位head sha> \
  -f commit_title="..." -f commit_message="..." --jq '{merged,sha}'
```

### 5.4 改分支保护用 PATCH 子资源，别 PUT 整个 protection

PUT 会把没写的字段重置掉。
```bash
gh api .../branches/main/protection/required_status_checks -X PATCH --input sc.json
# sc.json: {"strict":false,"checks":[{"context":"lint & test (py3.12)","app_id":15368}]}
```

check 名要跟 CI workflow 里 `jobs.quality.name` **完全一致**（含空格和括号）。

### 5.5 删分支前，先查有没有 PR 挂在上面

删掉 PR 的 base 分支 → PR 自动关闭且**无法 reopen**（base 不存在）。
C5 的 PR #41 因此重建为 #50。
```bash
gh pr list --state all --json baseRefName,headRefName,number
```

### 5.6 验证 main 内容，中文路径会 404

```bash
gh api "contents/docs/<中文名>.md?ref=main"   # ❌ 404（但目录列表里明明有）
# ✅ 用 tree + blob
SHA=$(gh api "git/trees/<sha>?recursive=1" --jq '.tree[] | select(.path=="docs/xxx.md") | .sha')
gh api "git/blobs/$SHA" --jq '.content' | tr -d '\n ' | base64 -d
```

base64 解码前必须 `tr -d '\n '`，否则换行会破坏。

### 5.7 网络不稳时：API 通道可能仍可用

git over HTTPS 报 `schannel: failed to receive handshake` 时，
`gh api` 可能仍正常。**优先走 API，别反复重试 git。**

---

## 6. 命令速查

```bash
# 环境自检（验收用，输出 JSON）
python -m g05 preflight

# 全量测试 + 静态检查
python -m pytest -q
python -m ruff check src tests scripts

# 归属自检（退出码非 0 = 有文件没归属）
python scripts/make_role_bundles.py --check
python scripts/gen_collab_docs.py        # 改完 roles.yaml 必须跑

# 故障注入（答辩证据）
python scripts/fault_inject.py --all     # 期望 5/5

# 性能基线（报告里的命令必须能复现）
python scripts/bench.py --devices 1000 --points-per-device 5 --repeat 5

# 演示验收
python scripts/demo_acceptance.py

# 起服务（/docs /healthz / 全部 200）
python -m g05 serve --port 8000
```

---

## 7. PR 描述必须包含的

标题用 `[角色] 做了什么`，正文按 `.github/pull_request_template.md` 的 5 节写
（做了什么 / 我负责的角色 / 做了哪些验证 / 有没有改契约 / 截图或数据）。

**关键四条，别省**：
1. **我负责的角色** —— 逐个列出改动的文件，并说明归属（用第 1.1 节的命令查）
2. **做了哪些验证** —— 贴**实测输出**，不是"应该能过"
3. **有没有改契约** —— 改了就说，并说明别人有没有依赖
4. **截图 / 数据** —— UI 改动附截图，性能改动附前后对比

**PR 里不要带这些**：
- `.coverage` / `coverage.xml` / `pytest-report.xml`（`.gitignore` 已忽略）
- 别人的文件（先查归属）
- 回滚别人的提交（先开 issue 说明原因）

---

## 8. 别做这些事

1. **别替别人关 issue**（见 1.2）。
2. **别把"功能达标"当"这块活完成"** —— 两列分开写。
3. **别在空数据集上验证约束** —— SQLite 的 `BEFORE UPDATE/DELETE` 触发器**逐行**触发，
   空表上影响 0 行就不触发，测出来的是"没有行可改"不是"改不动"。
   `fault_inject.py` 的 tamper 用例就栽在这里。
4. **别把测试夹具和生产用两套定义** —— 触发器统一在 `core/schema.py`，单一事实源。
5. **别只改架构文档就宣布契约改了** —— 要核对参考实现 + 既有测试。
6. **别在 PR 描述里写"已验收"而没有实测输出**。
7. **别用 `--force` 推别人的分支** —— 要 rebase 别人分支先说明。
8. **别把 `master` 当最新代码** —— 它是发包线。

---

## 9. 当前状态速览（写这份文件时）

```
main：preflight passed=true soft=[] · 183 passed · ruff 干净
      故障注入 5/5 · 22 端点全 200 · 覆盖率 85%（核心四模块 94%）
分支保护：CI 必过 `lint & test (py3.12)`（app_id 15368）
          普通成员红灯被挡；admin 仍可绕过（enforce_admins=false，有意为之）
已交付：A · B1(#43) · B2(#49) · C5(#50) · C3(#57 在途)
待交付：C1 · C2 · C4 · C6
```

**开工命令**（在最新 main 上起步）：
```bash
git fetch origin
git checkout -b feat/<你的分支> origin/main
```

---

_维护者：A（架构师）。_
_这份文件本身也是契约 —— 改它走 PR，并在 PR 里写清"哪条教训已经过期"。_
