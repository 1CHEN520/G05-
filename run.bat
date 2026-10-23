@echo off
chcp 65001 >nul
setlocal

echo ============================================================
echo  G05 设备台账与测点资产目录 - 一键启动
echo ============================================================
echo.

cd /d "%~dp0"

REM ---- 检查 Python ----
where python >nul 2>nul
if errorlevel 1 (
    echo [ERROR] 未找到 python，请先安装 Python 3.10+ 并加入 PATH
    pause
    exit /b 1
)

REM ---- 检查依赖 ----
python -c "import fastapi, sqlalchemy, yaml, openpyxl" >nul 2>nul
if errorlevel 1 (
    echo [1/4] 安装依赖...
    python -m pip install -r requirements.txt
    if errorlevel 1 (
        echo [ERROR] 依赖安装失败
        pause
        exit /b 1
    )
) else (
    echo [1/4] 依赖已就绪
)

REM ---- 初始化数据库 ----
echo [2/4] 初始化数据库...
python scripts\init_db.py
if errorlevel 1 (
    echo [ERROR] 数据库初始化失败
    pause
    exit /b 1
)

REM ---- 演示数据（数据库为空时才灌）----
echo [3/4] 检查演示数据...
python -c "import sys; sys.path.insert(0,'src'); from g05.core.db import session_scope; from sqlalchemy import select, func; from g05.models.device import Device; s=session_scope().__enter__(); n=s.execute(select(func.count()).select_from(Device)).scalar_one(); sys.exit(0 if n>0 else 1)" 2>nul
if errorlevel 1 (
    echo       数据库为空，灌入演示数据...
    python scripts\seed_data.py
) else (
    echo       已有数据，跳过
)

REM ---- 启动服务 ----
echo [4/4] 启动服务...
echo.
echo   控制台:  http://127.0.0.1:8000
echo   接口文档: http://127.0.0.1:8000/docs
echo   健康检查: http://127.0.0.1:8000/healthz
echo.
echo   按 Ctrl+C 停止服务
echo ============================================================
echo.

set PYTHONPATH=%CD%\src
python -m g05.main

pause
