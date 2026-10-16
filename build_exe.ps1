<#
.SYNOPSIS
    把「鸣潮自动演奏器」打包成单文件 exe（带管理员清单）。

.DESCRIPTION
    默认输出 dist\wuwa-autoplay.exe（--onefile + --uac-admin）。
    需要先安装依赖：pip install -r requirements.txt

.PARAMETER OneDir
    打成文件夹而不是单文件（启动更快、更不容易被杀软误报）。

.PARAMETER Console
    保留控制台窗口，方便查看报错。

.PARAMETER Clean
    打包前删除 build\ 与 dist\。

.PARAMETER Selftest
    打包完成后运行 dist 里的 exe --selftest 做一次冒烟验证。

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File build_exe.ps1 -Selftest
#>
[CmdletBinding()]
param(
    [switch]$OneDir,
    [switch]$Console,
    [switch]$Clean,
    [switch]$Selftest
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $MyInvocation.MyCommand.Definition
Set-Location $root

Write-Host "== 鸣潮自动演奏器 打包 ==" -ForegroundColor Cyan
Write-Host "项目目录：$root"

# 1) 检查 Python
$python = Get-Command python -ErrorAction SilentlyContinue
if (-not $python) {
    throw "找不到 python，请先安装 Python 3.10+ 并加入 PATH"
}
$version = (& python -c "import sys; print('%d.%d' % sys.version_info[:2])")
Write-Host "Python 版本：$version"

# 2) 检查依赖
& python -c "import PySide6, pydirectinput, keyboard" 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host "缺少运行依赖，正在安装 requirements.txt …" -ForegroundColor Yellow
    & python -m pip install -r requirements.txt
    if ($LASTEXITCODE -ne 0) { throw "依赖安装失败" }
}
& python -c "import PyInstaller" 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host "正在安装 PyInstaller …" -ForegroundColor Yellow
    & python -m pip install "pyinstaller>=6.6"
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller 安装失败" }
}

# 3) 生成图标（缺失时自动用 Qt 画一个，避免打包报错）
if (-not (Test-Path "assets\app.ico")) {
    Write-Host "未找到 assets\app.ico，正在生成 …" -ForegroundColor Yellow
    & python tools\make_icon.py
}

# 4) 清理
if ($Clean) {
    foreach ($dir in @('build', 'dist')) {
        if (Test-Path $dir) { Remove-Item -Recurse -Force $dir }
    }
    Write-Host "已清理 build\ 与 dist\"
}

# 5) 打包
if ($OneDir) { $env:WUWA_ONEFILE = '0' } else { $env:WUWA_ONEFILE = '1' }
if ($Console) { $env:WUWA_CONSOLE = '1' } else { $env:WUWA_CONSOLE = '0' }

& python -m PyInstaller --noconfirm --clean wuwa_autoplay.spec
if ($LASTEXITCODE -ne 0) { throw "打包失败" }

# 6) 结果
if ($OneDir) {
    $exe = Join-Path $root 'dist\wuwa-autoplay\wuwa-autoplay.exe'
} else {
    $exe = Join-Path $root 'dist\wuwa-autoplay.exe'
}
if (-not (Test-Path $exe)) { throw "没有找到生成的 exe：$exe" }

$size = [math]::Round((Get-Item $exe).Length / 1MB, 1)
Write-Host "打包完成：$exe（$size MB）" -ForegroundColor Green

# 7) 冒烟验证
if ($Selftest) {
    Write-Host "运行 exe --selftest …" -ForegroundColor Cyan
    $log = Join-Path $root 'dist\selftest.log'
    $errLog = Join-Path $root 'dist\selftest.err.log'
    $code = $null
    try {
        # 注意：exe 带管理员清单，普通权限的会话里直接启动会报「需要提升」；
        # 另外 GUI 子系统的程序用 & 调用不会等待，必须用 Start-Process -Wait 才能拿到真实退出码。
        $proc = Start-Process -FilePath $exe -ArgumentList '--selftest' -Wait -PassThru -NoNewWindow `
            -RedirectStandardOutput $log -RedirectStandardError $errLog
        $code = $proc.ExitCode
    } catch {
        Write-Host "无法在本会话启动 exe：$($_.Exception.Message)" -ForegroundColor Yellow
        Write-Host "原因通常是 exe 已写入 UAC 管理员清单。请在「管理员身份」的 PowerShell 里运行本脚本，" -ForegroundColor Yellow
        Write-Host "或手动执行：$exe --selftest（程序会把结果同时写进 exe 旁边的 selftest.log）" -ForegroundColor Yellow
    }

    if ($null -ne $code) {
        if (Test-Path $log) { Get-Content $log | Write-Host }
        if ($code -ne 0) {
            if (Test-Path $errLog) {
                Write-Host "--- 自检错误输出 ---" -ForegroundColor Red
                Get-Content $errLog | Write-Host
            }
            throw "自检失败，退出码 $code"
        }
        Write-Host "自检通过 ✅" -ForegroundColor Green
    }
}

# 走到这里就算成功，明确返回 0（避免被捕获的异常或 GUI 子进程污染退出码）
exit 0
