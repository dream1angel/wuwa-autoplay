# -*- coding: utf-8 -*-
"""程序入口。

用法::

    python main.py                  # 启动图形界面
    python main.py --selftest       # 无界面自检（打包后也可用）
    python main.py --no-elevate     # 不提示以管理员身份重启

Windows 下向游戏窗口发送输入需要更高权限：如果检测到当前不是管理员，
会询问是否以管理员身份重启（游戏本身多半是管理员权限）。
"""

from __future__ import annotations

import argparse
import ctypes
import os
import sys
from typing import List, Optional


def is_admin() -> bool:
    """当前进程是否具有管理员权限。"""
    if sys.platform != "win32":
        return True
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:  # pragma: no cover
        return False


def relaunch_as_admin() -> bool:
    """用 UAC 提权重启自身，成功返回 True。"""
    if sys.platform != "win32":
        return False
    if getattr(sys, "frozen", False):
        executable = sys.executable
        params = " ".join('"%s"' % a for a in sys.argv[1:])
    else:
        executable = sys.executable
        params = " ".join('"%s"' % a for a in sys.argv)
    try:
        result = ctypes.windll.shell32.ShellExecuteW(None, "runas", executable, params, None, 1)
        return int(result) > 32
    except Exception:  # pragma: no cover
        return False


def _parse_args(argv: List[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="wuwa-autoplay", description="鸣潮自动演奏器")
    parser.add_argument("--selftest", action="store_true", help="运行无界面自检后退出")
    parser.add_argument("--no-elevate", action="store_true", help="不要提示以管理员身份重启")
    parser.add_argument("--version", action="store_true", help="打印版本号")
    return parser.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    args = _parse_args(list(sys.argv[1:] if argv is None else argv))

    if args.version:
        print("鸣潮自动演奏器 1.0（Python %s）" % sys.version.split()[0])
        return 0

    if args.selftest:
        from ui.selftest import run_selftest

        return run_selftest()

    try:
        from PySide6.QtCore import Qt
        from PySide6.QtWidgets import QApplication, QMessageBox
    except Exception as exc:  # pragma: no cover
        print("缺少依赖：%s" % exc)
        print("请先执行：pip install -r requirements.txt")
        return 2

    from ui.main_window import APP_NAME, MainWindow
    from ui.theme import Theme

    # 高 DPI 屏幕下不糊
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )
    app = QApplication(sys.argv[:1])
    app.setApplicationName(APP_NAME)
    app.setApplicationDisplayName(APP_NAME)
    app.setFont(Theme.ui_font(9))
    app.setStyleSheet(Theme.stylesheet())

    # 管理员权限提示（不影响自检与演练模式）
    if sys.platform == "win32" and not is_admin() and not args.no_elevate:
        answer = QMessageBox.question(
            None,
            "建议以管理员身份运行",
            "Windows 下向游戏窗口发送按键通常需要管理员权限，否则游戏可能收不到输入。\n\n"
            "是否立即以管理员身份重启？\n（选择“否”仍可继续使用，但可能无法在游戏里生效）",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if answer == QMessageBox.StandardButton.Yes and relaunch_as_admin():
            return 0

    from core.config import AppConfig

    config = AppConfig()
    warnings = config.load()

    window = MainWindow(config)
    window.show()
    for message in warnings:
        window.status.showMessage("设置提示：%s" % message, 8000)

    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
