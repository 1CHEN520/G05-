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
    echo [ERROR] 未找到 python。请先用 Anaconda Prompt 执行：
    echo         conda env create -f environment.yml
    echo         conda activate pp03
    pause
    exit /b 1
)

REM ---- 提示当前解释器（不是 pp03 也能跑，但版本可能不对）----
for /f "delims=" %%i in ('python -c "import sys; print(sys.executable)"') do set PYEXE=%%i
echo 解释器: %PYEXE%
echo %PYEXE% | findstr /i "envs\\pp03" >nul
if errorlevel 1 (
    echo [提示] 当前不在 pp03 环境里。建议先 conda activate pp03
    echo.
)

REM ---- 检查依赖 ----
python -c "import fastapi, sqlalchemy, yaml, openpyxl" >nul 2>nul
if errorlevel 1 (
    echo [1/4] 安装依赖...
    python -m pip install -r requirements-dev.txt
    if errorlevel 1 (
        echo [ERROR] 依赖安装失败，详见 docs\04-环境搭建步骤.md
        pause
        exit /b 1
    )
    python -m pip install --no-deps --no-build-isolation -e .
) else (
    echo [1/4] 依赖已就绪
)

REM ---- 环境自检 ----
echo [2/4] 环境自检...
python -m g05 preflight
if errorlevel 1 (
    echo [ERROR] 自检未通过，见上方 JSON 中 ok=false 的项
    pause
    exit /b 1
)

REM ---- 初始化数据库 + 演示数据 ----
echo [3/4] 初始化数据库...
python -m g05 initdb
if errorlevel 1 (
    echo [ERROR] 数据库初始化失败
    pause
    exit /b 1
)

python -c "from g05.core.db import session_scope; from sqlalchemy import select, func; from g05.models.device import Device; s=session_scope().__enter__(); n=s.execute(select(func.count()).select_from(Device)).scalar_one(); raise SystemExit(0 if n>0 else 1)" 2>nul
if errorlevel 1 (
    echo       数据库为空，灌入演示数据...
    python -m g05 seed
) else (
    echo       已有数据，跳过
)

REM ---- 启动服务 ----
echo [4/4] 启动服务...
echo.
echo   控制台:   http://127.0.0.1:8000
echo   接口文档: http://127.0.0.1:8000/docs
echo   健康检查: http://127.0.0.1:8000/healthz
echo.
echo   按 Ctrl+C 停止服务
echo ============================================================
echo.

python -m g05 serve

pause
