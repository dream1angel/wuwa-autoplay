# -*- coding: utf-8 -*-
"""演奏前的倒计时遮罩（3-2-1），方便用户切回游戏窗口。"""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QFont, QGuiApplication, QPainter, QPen
from PySide6.QtWidgets import QWidget

from .theme import Theme

__all__ = ["CountdownOverlay"]


class CountdownOverlay(QWidget):
    """无边框、置顶、不抢焦点的倒计时窗口。"""

    def __init__(self, parent=None) -> None:
        super().__init__(None)  # 顶层窗口，方便盖在其它程序之上
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowDoesNotAcceptFocus
            | Qt.WindowType.WindowTransparentForInput
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setFixedSize(260, 260)
        self._value: float = 0.0
        self._hint = "请切换到游戏窗口"

    # ------------------------------------------------------------------ #
    # 对外接口
    # ------------------------------------------------------------------ #
    def show_countdown(self, value: float) -> None:
        """显示倒计时数字；``value <= 0`` 时自动隐藏。"""
        if value <= 0:
            self.hide_overlay()
            return
        self._value = float(value)
        self._center_on_cursor_screen()
        if not self.isVisible():
            self.show()
        self.raise_()
        self.update()

    def hide_overlay(self) -> None:
        self._value = 0.0
        self.hide()

    # ------------------------------------------------------------------ #
    # 绘制
    # ------------------------------------------------------------------ #
    def _center_on_cursor_screen(self) -> None:
        screen = QGuiApplication.screenAt(self.cursor().pos()) or QGuiApplication.primaryScreen()
        if screen is None:
            return
        area = screen.availableGeometry()
        self.move(area.center().x() - self.width() // 2, area.top() + area.height() // 6)

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = self.rect().adjusted(8, 8, -8, -8)

        painter.setBrush(QColor(18, 21, 28, 225))
        painter.setPen(QPen(QColor(Theme.ACCENT), 3))
        painter.drawEllipse(rect)

        painter.setPen(QColor(Theme.TEXT))
        font = QFont("Microsoft YaHei", 64)
        font.setBold(True)
        painter.setFont(font)
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, "%d" % max(1, int(round(self._value))))

        painter.setFont(QFont("Microsoft YaHei", 12))
        painter.setPen(QColor(Theme.MUTED))
        painter.drawText(
            rect.adjusted(0, 110, 0, 0),
            Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop,
            self._hint,
        )
