# -*- coding: utf-8 -*-
"""简谱符号绘制：数字 + 上下八度点。

统一约定（编辑器网格与 PNG/PDF 导出共用，自然阶与半音阶模式完全一致）：

* 单数字 = **中音**，如 ``1`` ``2`` ``7``
* 数字**上方一点** = **高音**，如 ``1`` 带点
* 数字**下方一点** = **低音**，如 ``1`` 带点
* 升半音仍在数字前加 ``#``，如 ``#1``（高音 #1 就是 ``#1`` 加上方点）

点用 QPainter 直接画成实心小圆，而不是用 Unicode 组合字符——
组合点在很多中文字体里渲染不稳（要么不显示，要么错位）。
"""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import QRect, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter

from core.notes import Key

__all__ = ["jianpu_symbol", "draw_jianpu", "DEFAULT_DOT_RADIUS", "DEFAULT_DOT_GAP"]

#: 八度点的半径与离字间隙（像素）
DEFAULT_DOT_RADIUS = 1.6
DEFAULT_DOT_GAP = 2.2


def jianpu_symbol(key: Key) -> str:
    """返回简谱文字部分（不含八度点）：``1`` / ``#1``。"""
    return key.short_label()


def draw_jianpu(
    painter: QPainter,
    rect: QRect,
    key: Key,
    font: Optional[QFont] = None,
    color: Optional[QColor] = None,
    dot_radius: float = DEFAULT_DOT_RADIUS,
    dot_gap: float = DEFAULT_DOT_GAP,
) -> None:
    """在 ``rect`` 里居中画一个音的简谱符号（数字 + 上/下八度点）。

    空间不足（和弦叠很多音时）会自动缩小留白，点也会被夹在 ``rect`` 内，不会溢出格子。
    """
    text = jianpu_symbol(key)
    dot = key.octave_dot()
    if font is not None:
        painter.setFont(font)
    ink = QColor(color) if color is not None else QColor(painter.pen().color())
    painter.setPen(ink)
    painter.setBrush(ink)

    metrics = QFontMetrics(painter.font())
    reserve = int(round(dot_radius * 2 + dot_gap)) if dot else 0
    text_rect = QRect(rect)
    if dot > 0:
        text_rect.setTop(text_rect.top() + reserve)
    elif dot < 0:
        text_rect.setBottom(text_rect.bottom() - reserve)
    # 空间太小就别留白了，优先保证数字本身看得见
    if reserve and (text_rect.height() < 8 or text_rect.width() < 6):
        text_rect = QRect(rect)
        reserve = 0

    painter.drawText(text_rect, Qt.AlignmentFlag.AlignCenter, text)
    if not dot or not reserve:
        return

    glyph_height = metrics.boundingRect(text).height() or metrics.height()
    center_x = text_rect.center().x()
    half = glyph_height / 2.0
    if dot > 0:
        center_y = text_rect.center().y() - half - dot_gap - dot_radius
    else:
        center_y = text_rect.center().y() + half + dot_gap + dot_radius
    # 夹在 rect 内，绝不越界到别的格子
    center_y = max(rect.top() + dot_radius + 0.5, min(rect.bottom() - dot_radius - 0.5, center_y))

    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(ink)
    painter.drawEllipse(
        QRectF(center_x - dot_radius, center_y - dot_radius, dot_radius * 2.0, dot_radius * 2.0)
    )
