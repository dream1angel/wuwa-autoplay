# -*- coding: utf-8 -*-
"""资源路径与图标（兼容 PyInstaller 单文件模式）。"""

from __future__ import annotations

import os
import sys

__all__ = ["resource_path", "app_icon"]


def resource_path(*parts: str) -> str:
    """返回资源绝对路径。

    PyInstaller 单文件模式会把数据解包到 ``sys._MEIPASS``；
    源码运行时则是项目根目录。
    """
    base = getattr(sys, "_MEIPASS", None)
    if not base:
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, *parts)


def app_icon():
    """加载窗口图标；失败返回空 QIcon（不影响使用）。"""
    try:
        from PySide6.QtGui import QIcon
    except Exception:  # pragma: no cover
        return None
    path = resource_path("assets", "app.ico")
    if not os.path.exists(path):
        return QIcon()
    return QIcon(path)
