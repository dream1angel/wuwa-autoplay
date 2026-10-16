# -*- coding: utf-8 -*-
"""生成 README 用的界面截图（不需要人工截屏）。

用法::

    python tools/make_screenshots.py                  # 输出到 docs/images/
    python tools/make_screenshots.py --out 某个目录    # 换个输出目录
    python tools/make_screenshots.py --show           # 同时在屏幕上显示（调样式时用）

原理：给窗口打开 ``Qt.WA_DontShowOnScreen``，控件照常完成布局与绘制、但不会真的出现在桌面上，
再用 ``QWidget.grab()`` 把客户区渲染成图片。这样既不用人工截屏、也不会打断你手头的操作，
而且用的是真实的 Windows 平台插件 —— 字体与控件风格跟平时完全一致
（自检用的 offscreen 平台没有字体，截出来会是豆腐块）。

生成 4 张图：

* ``main.png``      主界面（自然阶，载入《小星星》）
* ``chromatic.png`` 半音阶模式（载入《半音阶练习》，能看到 #1 #2 #4 #5 #6 黑键格）
* ``settings.png``  设置 → 半音阶（36 键映射编辑器）
* ``export.png``    PNG 导出的曲谱效果
"""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
import time
from typing import List, Optional

# 让脚本无论从哪个目录调用都能 import 到项目包
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# 必须用真实平台插件：offscreen 平台取不到系统字体，中文和升降号都会变成方框
os.environ.pop("QT_QPA_PLATFORM", None)


def _pump(app, milliseconds: int = 300) -> None:
    """空转事件循环，等布局、样式和字体都就绪。"""
    deadline = time.time() + milliseconds / 1000.0
    while time.time() < deadline:
        app.processEvents()
        time.sleep(0.01)


def _grab(widget, path: str, app) -> str:
    """把控件客户区渲染成 PNG。"""
    _pump(app, 250)
    pixmap = widget.grab()
    if pixmap.isNull():
        raise RuntimeError("截图失败（grab 返回空图）：%s" % path)
    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    if not pixmap.save(path, "PNG"):
        raise RuntimeError("写入 PNG 失败：%s" % path)
    size = os.path.getsize(path)
    if size < 2000:
        raise RuntimeError("图片内容太小（%d 字节），八成是空窗口：%s" % (size, path))
    print("  [生成] %-16s %5d×%-5d %6.1f KB" % (
        os.path.basename(path), pixmap.width(), pixmap.height(), size / 1024.0
    ))
    return path


def _prepare_window(app, config, show: bool):
    """构造主窗口；show=False 时用 WA_DontShowOnScreen 做到「已显示但不出现」。"""
    from PySide6.QtCore import Qt
    from ui.main_window import MainWindow
    from ui.theme import Theme

    app.setStyleSheet(Theme.stylesheet())
    window = MainWindow(config)
    window.resize(1200, 820)
    if not show:
        window.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
    window.show()
    _pump(app, 400)
    return window


def _load_example(window, app, filename: str):
    """载入 examples/ 里的示例曲谱，并把光标停在开头。"""
    from scoreio import json_io

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    path = os.path.join(root, "examples", filename)
    score = json_io.load_score(path)
    score.path = ""          # 标题栏用曲谱名，而不是一长串绝对路径
    window._load_into_ui(score)
    window.grid.set_cursor(0)
    window.grid.clear_selection()
    window.status.showMessage("就绪：F8 开始演奏，F9 暂停，F10 紧急停止")
    _pump(app, 300)
    return score


def build_screenshots(out_dir: str, show: bool = False) -> List[str]:
    from PySide6.QtWidgets import QApplication

    from core.config import AppConfig

    app = QApplication.instance() or QApplication(sys.argv[:1])

    # 截图期间不要占用用户的全局快捷键，也不要把设置写进程序目录
    config = AppConfig(
        enable_hotkeys=False,
        pause_on_focus_loss=False,
        target_window_keyword="",
        settings_path=os.path.join(tempfile.gettempdir(), "wuwa-shot-settings.json"),
    )

    produced: List[str] = []
    window = _prepare_window(app, config, show)

    # 1) 主界面：自然阶 + 《小星星》
    _load_example(window, app, "小星星.score.json")
    produced.append(_grab(window, os.path.join(out_dir, "main.png"), app))

    # 2) 半音阶模式：载入半音阶练习曲，黑键格会显示成 #1 / #2 / #4 …
    from core.notes import MODE_CHROMATIC

    _load_example(window, app, "半音阶练习.score.json")
    if window.mode != MODE_CHROMATIC:   # 防御：示例文件模式字段异常时手动切一遍
        window._switch_mode(MODE_CHROMATIC)
        _pump(app, 200)
    window.grid.set_cursor(1)       # 停在一个黑键上，方便看清符号
    _pump(app, 200)
    produced.append(_grab(window, os.path.join(out_dir, "chromatic.png"), app))

    # 3) 设置 → 半音阶
    from ui.settings_dialog import SettingsDialog

    from PySide6.QtCore import Qt

    dialog = SettingsDialog(config, window)
    if not show:
        dialog.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
    dialog.tabs.setCurrentIndex(4)  # 演奏 / 按键与窗口 / 快捷键 / 按键映射 / 半音阶 / 界面
    dialog.show()
    _pump(app, 400)
    produced.append(_grab(dialog, os.path.join(out_dir, "settings.png"), app))
    dialog.close()

    # 4) 导出效果：直接用导出模块画，和用户点「导出 PNG」得到的东西一模一样
    _load_example(window, app, "小星星.score.json")
    produced.append(_grab_export(window, os.path.join(out_dir, "export.png"), app))

    # 干净收尾：标记为已保存，避免 close() 弹出「要保存吗」
    try:
        window.undo.mark_saved()
        window._meta_dirty = False
        window.close()
    except Exception:
        pass
    return produced


def _grab_export(window, path: str, app) -> str:
    """导出 PNG 曲谱（走真实的导出代码路径）。"""
    from scoreio import render_io

    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    render_io.export_png(window.score, path, window.keymap)
    size = os.path.getsize(path)
    if size < 2000:
        raise RuntimeError("导出图太小：%s" % path)
    print("  [生成] %-16s          %6.1f KB" % (os.path.basename(path), size / 1024.0))
    _pump(app, 50)
    return path


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="生成 README 用的界面截图")
    parser.add_argument("--out", default=os.path.join("docs", "images"),
                        help="输出目录（默认 docs/images）")
    parser.add_argument("--show", action="store_true",
                        help="同时在屏幕上显示窗口（调样式时用；平时不要开）")
    args = parser.parse_args(argv)

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    out_dir = args.out if os.path.isabs(args.out) else os.path.join(root, args.out)

    print("生成截图 → %s" % out_dir)
    try:
        build_screenshots(out_dir, show=args.show)
    except Exception as exc:  # pragma: no cover - 出错时给出人话提示
        import traceback

        print("[失败] %s" % exc)
        traceback.print_exc()
        return 1
    print("完成：%d 张" % len([name for name in os.listdir(out_dir) if name.endswith(".png")]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
