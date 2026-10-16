# -*- coding: utf-8 -*-
"""配色与全局样式表（暗色主题）。

网格控件直接引用这里的常量，保证界面与导出的图片观感一致。
"""

from __future__ import annotations

from PySide6.QtGui import QColor, QFont

__all__ = ["Theme"]


class Theme:
    """集中管理颜色与字体。"""

    # 基础面板
    BG = "#12151c"
    PANEL = "#1a1f2b"
    PANEL_ALT = "#202634"
    BORDER = "#2c3446"
    TEXT = "#e6ebf5"
    MUTED = "#8d97ab"
    ACCENT = "#3d7bfd"
    ACCENT_DIM = "#2b56ad"
    OK = "#3ecf8e"
    WARN = "#f2b13d"
    ERROR = "#e5484d"

    # 节拍网格
    GRID_BG = "#161b25"
    GRID_BAR = "#39445c"
    GRID_CELL = "#242b3a"
    GRID_BEAT = "#323c52"
    GRID_BAR_NUMBER = "#8d97ab"
    GRID_CURSOR = "#3d7bfd"
    GRID_SELECTION = "#2f5fbe"
    GRID_CONTINUATION = "#1d2432"
    #: 和弦输入模式（按住空格）时光标格的填充色
    CHORD_MODE = "#1e3a2f"
    NOTE_BG = "#3d7bfd"
    NOTE_SHARP = "#8b5cf6"
    NOTE_TEXT = "#ffffff"
    NOTE_TIE = "#7aa2ff"
    NOTE_ERROR = "#e5484d"
    PLAYHEAD = "#3ecf8e"

    # 虚拟键盘
    PIANO_KEY = "#242b3a"
    PIANO_KEY_BLACK = "#12151c"
    PIANO_KEY_BORDER = "#39445c"
    PIANO_KEY_TEXT = "#e6ebf5"
    PIANO_KEY_ACTIVE = "#3ecf8e"
    PIANO_LABEL = "#8d97ab"

    @staticmethod
    def ui_font(size: int = 9) -> QFont:
        """界面字体：优先微软雅黑，保证中文清晰。"""
        font = QFont("Microsoft YaHei", size)
        font.setStyleStrategy(QFont.StyleStrategy.PreferAntialias)
        return font

    @staticmethod
    def mono_font(size: int = 10) -> QFont:
        """等宽字体：按键字母与时间显示用。"""
        font = QFont("Consolas", size)
        font.setBold(True)
        return font

    @staticmethod
    def color(name: str) -> QColor:
        return QColor(getattr(Theme, name))

    @staticmethod
    def stylesheet() -> str:
        """全局 QSS。"""
        return """
        QWidget {
            background: %(bg)s;
            color: %(text)s;
            font-family: "Microsoft YaHei", "Segoe UI", sans-serif;
            font-size: 13px;
        }
        QMainWindow::separator { background: %(border)s; width: 1px; height: 1px; }
        QToolBar {
            background: %(panel)s;
            border-bottom: 1px solid %(border)s;
            padding: 4px 6px;
            spacing: 4px;
        }
        QToolBar QToolButton {
            padding: 5px 10px;
            border-radius: 5px;
            color: %(text)s;
        }
        QToolBar QToolButton:hover { background: %(panel_alt)s; }
        QToolBar QToolButton:disabled { color: %(muted)s; }
        QMenuBar { background: %(panel)s; }
        QMenuBar::item:selected { background: %(panel_alt)s; }
        QMenu { background: %(panel)s; border: 1px solid %(border)s; }
        QMenu::item:selected { background: %(accent)s; }
        QStatusBar { background: %(panel)s; color: %(muted)s; }
        QGroupBox {
            border: 1px solid %(border)s;
            border-radius: 6px;
            margin-top: 12px;
            padding-top: 8px;
        }
        QGroupBox::title { subcontrol-origin: margin; left: 10px; color: %(muted)s; }
        QPushButton {
            background: %(panel_alt)s;
            border: 1px solid %(border)s;
            border-radius: 5px;
            padding: 5px 12px;
        }
        QPushButton:hover { border-color: %(accent)s; }
        QPushButton:pressed { background: %(accent_dim)s; }
        QPushButton:disabled { color: %(muted)s; border-color: %(border)s; }
        QPushButton#primary {
            background: %(accent)s;
            border: 1px solid %(accent)s;
            color: #ffffff;
            font-weight: bold;
        }
        QPushButton#primary:hover { background: #4a86ff; }
        QPushButton#danger { background: %(error)s; border-color: %(error)s; color: #ffffff; }
        QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox, QPlainTextEdit {
            background: %(panel_alt)s;
            border: 1px solid %(border)s;
            border-radius: 5px;
            padding: 4px 6px;
            selection-background-color: %(accent)s;
        }
        QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus { border-color: %(accent)s; }
        QComboBox QAbstractItemView { background: %(panel)s; border: 1px solid %(border)s; }
        QScrollArea { border: none; }
        QScrollBar:vertical { background: %(bg)s; width: 12px; margin: 0; }
        QScrollBar::handle:vertical { background: %(border)s; border-radius: 6px; min-height: 30px; }
        QScrollBar::handle:vertical:hover { background: %(accent_dim)s; }
        QScrollBar::add-line, QScrollBar::sub-line { height: 0; }
        QProgressBar {
            background: %(panel_alt)s;
            border: 1px solid %(border)s;
            border-radius: 5px;
            text-align: center;
            height: 18px;
        }
        QProgressBar::chunk { background: %(accent)s; border-radius: 4px; }
        QTabWidget::pane { border: 1px solid %(border)s; border-radius: 6px; }
        QTabBar::tab {
            background: %(panel)s;
            padding: 6px 14px;
            border: 1px solid %(border)s;
            border-bottom: none;
            border-top-left-radius: 5px;
            border-top-right-radius: 5px;
        }
        QTabBar::tab:selected { background: %(panel_alt)s; color: %(text)s; }
        QTableWidget { gridline-color: %(border)s; background: %(panel)s; }
        QHeaderView::section {
            background: %(panel_alt)s;
            border: none;
            border-right: 1px solid %(border)s;
            padding: 4px;
        }
        QCheckBox { spacing: 6px; }
        QToolTip { background: %(panel_alt)s; color: %(text)s; border: 1px solid %(border)s; }
        """ % {
            "bg": Theme.BG,
            "panel": Theme.PANEL,
            "panel_alt": Theme.PANEL_ALT,
            "border": Theme.BORDER,
            "text": Theme.TEXT,
            "muted": Theme.MUTED,
            "accent": Theme.ACCENT,
            "accent_dim": Theme.ACCENT_DIM,
            "ok": Theme.OK,
            "warn": Theme.WARN,
            "error": Theme.ERROR,
        }
