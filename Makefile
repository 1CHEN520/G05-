# G05 常用命令
# Windows 用户若没有 make，可直接看 README 或运行 run.bat

.PHONY: help install init seed run test cov fault lint clean reset

help:            ## 显示所有命令
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

install:         ## 安装依赖
	pip install -r requirements-dev.txt

init:            ## 建表 + 装触发器（幂等）
	python scripts/init_db.py

seed:            ## 灌演示数据
	python scripts/seed_data.py

reset:           ## 清空业务表后重灌（审计表保留）
	python scripts/init_db.py --drop
	python scripts/seed_data.py

run:             ## 启动服务
	python -m g05.main

test:            ## 跑单测
	python -m pytest tests/ -q

cov:             ## 单测 + 覆盖率
	python -m pytest tests/ --cov=src/g05 --cov-report=term-missing

fault:           ## 五类故障注入
	python scripts/fault_inject.py --all

lint:            ## 代码检查
	ruff check src/ tests/ scripts/

clean:           ## 清理缓存与日志
	rm -rf .pytest_cache .ruff_cache htmlcov .coverage
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
