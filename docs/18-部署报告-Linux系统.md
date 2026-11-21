# G05 部署报告（Linux 系统 · 独立成册）

> 对应国标软件工程流程「5. 运维维护——软件部署（至少支持两个系统）」的**第二份独立部署报告**。系统 B：Linux（Ubuntu 22.04 LTS，标准部署路径）。Windows 系统部署见《部署报告》（docs/13，双系统总报告）。维护者：C6（王新宇）· A（Linux 章节实测）。

## 一、部署目标与环境

| 项 | 规格 |
|---|---|
| 操作系统 | Ubuntu 22.04 LTS（也适用于 Debian 12 / WSL2 实测可行） |
| Python | 3.12.x（conda 或系统 venv） |
| 数据库 | SQLite 3.53（WAL 模式，零安装） |
| 端口 | 8000（uvicorn） |
| 部署形态 | systemd 服务（开机自启）或 nohup 手动 |

## 二、部署步骤（标准路径，逐条可抄）

```bash
# 1) 获取代码
git clone https://github.com/1CHEN520/G05-.git && cd G05-

# 2) 环境（conda 或 venv 二选一）
conda create -n pp09 python=3.12 -y && conda activate pp09
pip install -r requirements.txt

# 3) 建库与灌数据
python -m g05 initdb --drop
python -m g05 seed                     # 60 设备 / 180 测点

# 4) 环境自检（8/8 通过才继续）
python -m g05 preflight

# 5) 启动（两种方式）
python -m g05 serve                    # 手动前台
# 或 systemd：
sudo tee /etc/systemd/system/g05.service <<'EOF'
[Unit]
Description=G05 Industrial Device Ledger
After=network.target
[Service]
WorkingDirectory=/opt/G05-
ExecStart=/opt/conda/envs/pp09/bin/python -m g05 serve
Restart=always
User=g05
[Install]
WantedBy=multi-user.target
EOF
sudo systemctl daemon-reload && sudo systemctl enable --now g05

# 6) 健康验证
curl http://127.0.0.1:8000/healthz    # {"ok":true,...,"journal_mode":"wal"}
curl http://127.0.0.1:8000/metrics    # devices:60, points:180
```

## 三、Linux 专项注意事项（与 Windows 的差异）

| 差异点 | Windows 行为 | Linux 行为与处置 |
|---|---|---|
| 行尾 | 生成脚本统一写 LF（`newline="\n"`）避免 CRLF 污染 diff | 天然 LF，无处理 |
| 路径分隔 | 相对路径（ingest 用 `os.path.dirname(__file__)`） | 相对路径一致，零改动 |
| 后台驻留 | 任务计划程序 | systemd Restart=always 或 nohup |
| 文件权限 | 无 | logs/、data/db/ 目录需运行用户可写（systemd User=g05） |
| 信号处理 | Ctrl+C | systemd stop 发 SIGTERM，uvicorn 优雅退出（in-flight 请求完成后关闭） |

## 四、部署验证清单（Linux）

| # | 检查项 | 命令 | 通过标准 |
|---|---|---|---|
| 1 | 服务状态 | `systemctl status g05` | active (running) |
| 2 | 健康检查 | `curl :8000/healthz` | ok=true 且 foreign_keys=true |
| 3 | WAL 生效 | healthz 的 journal_mode | "wal" |
| 4 | 数据完整 | `curl :8000/metrics` | devices=60, points=180 |
| 5 | 自检 | `python -m g05 preflight` | 8/8 passed=true |
| 6 | 对账恒等 | `python scripts/reconcile.py` | 5/5 成立 |
| 7 | 性能基线 | `python scripts/bench.py` | 四类达标 |
| 8 | 日志轮转 | `ls logs/` | app.jsonl（10MB 轮转 ×5 备份） |

## 五、故障处置（Linux 版 runbook 摘要）

1. **端口占用**：`ss -tlnp | grep 8000` → 换端口（`python -m g05 serve --port 8001`）或 kill 占用进程；
2. **权限拒绝**：`chown -R g05:g05 /opt/G05-/logs /opt/G05-/data`；
3. **依赖冲突**：`pip install -r requirements.txt` 报版本冲突时，按 `requirements.txt` 锁定版本逐个核对（README 四层结构）；
4. **磁盘写满**：logs 轮转自动限 10MB×5+1；data/db/g05.db 膨胀时跑 `python scripts/reconcile.py` 核对后用 initdb --drop 重建演示库；
5. **回滚**：`git checkout <上一个 tag 或 commit>` + `systemctl restart g05`（数据库文件独立于代码，回滚代码不丢数据）。
