# setup_pp03.ps1 —— G05 环境一键安装（第 2/3/4 层）
#
# 前提：已经在 Anaconda Prompt 里 conda activate pp03
#       第 1 层（Python 解释器）由 conda 负责，脚本不碰
#
# 用法：
#   cd D:\G05
#   powershell -ExecutionPolicy Bypass -File .\setup_pp03.ps1

$ErrorActionPreference = 'Stop'

function Write-Step($msg) { Write-Host "==> $msg" -ForegroundColor Cyan }
function Write-Ok($msg)   { Write-Host "    OK  $msg" -ForegroundColor Green }
function Write-Bad($msg)  { Write-Host "    !!  $msg" -ForegroundColor Red }

Write-Step "0/5 前置检查"
$ver = & python -c "import sys; print('%d.%d' % sys.version_info[:2])"
$exe = & python -c "import sys; print(sys.executable)"
if ($ver -notmatch '^3\.(1[0-9]|[2-9][0-9])$') {
    Write-Bad "当前 Python $ver 不满足 >=3.10，请先 conda activate pp03"
    exit 1
}
Write-Ok "Python $ver"
Write-Ok "解释器 $exe"
if ($exe -notmatch 'envs\\pp03') {
    Write-Host "    注意：解释器路径里没有 envs\pp03，确认你激活的是课程约定的环境" -ForegroundColor Yellow
}

Write-Step "1/5 安装运行与开发依赖（第 2、3 层，版本已锁定）"
& python -m pip install -r requirements-dev.txt --disable-pip-version-check
if ($LASTEXITCODE -ne 0) { Write-Bad "依赖安装失败"; exit 1 }
Write-Ok "依赖就绪"

Write-Step "2/5 安装项目本体（editable，第 4 层）"
& python -m pip install --no-deps --no-build-isolation -e . --disable-pip-version-check
if ($LASTEXITCODE -ne 0) { Write-Bad "editable 安装失败"; exit 1 }
Write-Ok "g05-asset-catalog 已安装"

Write-Step "3/5 依赖一致性检查"
& python -m pip check
if ($LASTEXITCODE -ne 0) { Write-Bad "存在依赖冲突，见上方输出"; exit 1 }
Write-Ok "No broken requirements found."

Write-Step "4/5 确认 g05 指向本仓库（最容易出错的一条）"
$path = & python -c "import g05; print(g05.__file__)"
Write-Host "    $path"
if ($path -notmatch [regex]::Escape((Get-Location).Path)) {
    Write-Bad "g05 指向的不是当前目录，执行下面两条修复："
    Write-Host "      python -m pip uninstall -y g05-asset-catalog"
    Write-Host "      python -m pip install --no-deps --no-build-isolation -e ."
    exit 1
}
Write-Ok "指向本仓库"

Write-Step "5/5 环境自检与测试"
& python -m g05 preflight
if ($LASTEXITCODE -ne 0) { Write-Bad "preflight 未通过，见上方 JSON 里 ok=false 的项"; exit 1 }
& python -m pytest -q
if ($LASTEXITCODE -ne 0) { Write-Bad "测试未通过"; exit 1 }

Write-Host ""
Write-Host "环境就绪。启动服务：" -ForegroundColor Green
Write-Host "    python -m g05 serve" -ForegroundColor Green
Write-Host "然后打开 http://127.0.0.1:8000/" -ForegroundColor Green
