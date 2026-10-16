# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置：把整个程序打成一个 exe（默认）。

用法（推荐直接用 build_exe.ps1）::

    python -m PyInstaller --noconfirm --clean wuwa_autoplay.spec

可用环境变量控制打包形态：
    WUWA_CONSOLE=1   保留控制台窗口（排查问题时有用）
    WUWA_ONEFILE=0   打成文件夹（启动更快，也更不容易被杀软误报）
    WUWA_NO_ADMIN=1  不写入「要求管理员」清单（打包机无法提权时自检用）
"""

import os

from PyInstaller.utils.hooks import collect_submodules

console = os.environ.get("WUWA_CONSOLE") == "1"
onefile = os.environ.get("WUWA_ONEFILE", "1") != "0"
#: 默认要求管理员权限：向游戏窗口发送输入通常需要提权
uac_admin = os.environ.get("WUWA_NO_ADMIN") != "1"

APP_NAME = "wuwa-autoplay"
ICON = "assets/app.ico"
icon_path = ICON if os.path.exists(ICON) else None

# keyboard 库按平台动态导入子模块，必须显式收集
hiddenimports = collect_submodules("keyboard") + collect_submodules("pydirectinput")

datas = []
if os.path.exists(ICON):
    datas.append((ICON, "assets"))
for extra in ("README.md",):
    if os.path.exists(extra):
        datas.append((extra, "."))

a = Analysis(
    ["main.py"],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    # 这些库跟本程序无关，排除后体积能小不少
    excludes=["tkinter", "test", "unittest", "pydoc_data", "numpy", "PIL", "matplotlib", "pypdf"],
    noarchive=False,
)
pyz = PYZ(a.pure)

if onefile:
    exe = EXE(
        pyz,
        a.scripts,
        a.binaries,
        a.datas,
        [],
        name=APP_NAME,
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        upx=False,
        runtime_tmpdir=None,
        console=console,
        disable_windowed_traceback=False,
        icon=icon_path,
        # 关键：向游戏窗口发送输入需要管理员权限，直接给 exe 打上 UAC 管理员清单
        uac_admin=uac_admin,
    )
else:
    exe = EXE(
        pyz,
        a.scripts,
        [],
        exclude_binaries=True,
        name=APP_NAME,
        debug=False,
        strip=False,
        upx=False,
        console=console,
        icon=icon_path,
        uac_admin=uac_admin,
    )
    coll = COLLECT(
        exe,
        a.binaries,
        a.datas,
        strip=False,
        upx=False,
        name=APP_NAME,
    )
