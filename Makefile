# G05 常用命令
# Windows 用户若没有 make，可直接看 README 或运行 run.bat
# 所有命令都假定已 conda activate pp03

.PHONY: help install preflight init seed run test cov fault lint bench bundle issues roster reset clean

help:            ## 显示所有命令
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

install:         ## 安装依赖 + editable 安装项目本体
	pip install -r requirements-dev.txt
	pip install --no-deps --no-build-isolation -e .

preflight:       ## 环境自检（输出 JSON，passed 必须为 true）
	python -m g05 preflight

init:            ## 建表 + 装触发器（幂等）
	python -m g05 initdb

seed:            ## 灌演示数据
	python -m g05 seed

reset:           ## 清空业务表后重灌
	python -m g05 initdb --drop
	python -m g05 seed

run:             ## 启动服务
	python -m g05 serve

test:            ## 跑单测
	python -m pytest -q

cov:             ## 单测 + 覆盖率
	python -m pytest --cov=src/g05 --cov-report=term-missing

fault:           ## 五类故障注入
	python scripts/fault_inject.py --all

bench:           ## 性能基线（导入 / 血缘）
	python scripts/bench.py

lint:            ## 代码检查
	ruff check src/ tests/ scripts/

bundle:          ## 按角色拆分文件包 -> dist/roles/
	python scripts/make_role_bundles.py

roster:          ## 生成《团队角色清单》Excel
	python scripts/make_roster_xlsx.py

issues:          ## 预览要建的 GitHub Issue（不联网）
	python scripts/create_issues.py --dry-run

clean:           ## 清理缓存与日志
	rm -rf .pytest_cache .ruff_cache htmlcov .coverage dist
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
