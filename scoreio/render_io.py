# -*- coding: utf-8 -*-
"""曲谱导出为图片 / PDF。

PySide6 在这里**惰性导入**：只用 JSON / 文本谱的用户不需要 Qt 也能跑核心逻辑，
缺少 Qt 时导出函数会抛出带中文说明的 :class:`ScoreIOError`。

实现要点
--------
* PN G：按内容计算画布大小，直接渲染整首曲谱；
* PDF：把页面尺寸设为「内容的实际尺寸」，单页放下整首谱（避免 Qt 多页 PDF 的坑），
  页宽固定 A4 宽度，页高按内容增长。
"""

from __future__ import annotations

import math
import os
from typing import Dict, List, Optional, Tuple

from core.model import Score
from core.notes import MODE_CHROMATIC, MODE_DIATONIC, Key, KeyMap

from .json_io import ScoreIOError

__all__ = ["RenderStyle", "render_score_image", "export_png", "export_pdf"]


class RenderStyle:
    """导出布局参数（像素，基于 96dpi 逻辑坐标）。"""

    def __init__(
        self,
        cell_width: int = 36,
        cell_height: int = 48,
        bar_gap: int = 12,
        row_gap: int = 16,
        margin: int = 32,
        header_height: int = 96,
        bars_per_row: int = 4,
        page_width_px: int = 1123,  # A4 横向宽度（96dpi）
        title_size: int = 22,
        meta_size: int = 13,
        note_size: int = 15,
        bar_number_size: int = 10,
    ) -> None:
        self.cell_width = cell_width
        self.cell_height = cell_height
        self.bar_gap = bar_gap
        self.row_gap = row_gap
        self.margin = margin
        self.header_height = header_height
        self.bars_per_row = max(1, bars_per_row)
        self.page_width_px = page_width_px
        self.title_size = title_size
        self.meta_size = meta_size
        self.note_size = note_size
        self.bar_number_size = bar_number_size


# 配色（与界面主题保持一致）
COLOR_BG = "#ffffff"
COLOR_TEXT = "#1f2430"
COLOR_MUTED = "#7a8394"
COLOR_BAR = "#3c4a63"
COLOR_CELL = "#dfe5ee"
COLOR_BEAT = "#b9c3d4"
COLOR_NOTE_BG = "#2f6df6"
COLOR_NOTE_SHARP = "#8b5cf6"
COLOR_NOTE_TEXT = "#ffffff"
COLOR_TIE = "#2f6df6"
COLOR_ERROR = "#e5484d"


def _require_qt():
    try:
        from PySide6 import QtCore, QtGui  # noqa: F401
    except Exception as exc:  # pragma: no cover - 取决于环境
        raise ScoreIOError(
            "导出图片/PDF 需要 PySide6，请先执行：pip install PySide6（%s）" % exc
        ) from exc
    return QtCore, QtGui


def _ensure_app():
    """确保存在 QGuiApplication。

    无界面脚本（例如 ``cli.py``）或单独跑某个测试时，Qt 还没有应用实例，
    此时创建 QFont / QPainter 会直接崩溃，所以这里兜底创建（离屏平台）。
    """
    from PySide6.QtGui import QGuiApplication

    app = QGuiApplication.instance()
    if app is None:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        app = QGuiApplication([])
    return app


def _jianpu_painter():
    """取简谱绘制函数（与编辑器共用）；拿不到就返回 None 让调用方回退成文字。"""
    try:
        from ui.notation import draw_jianpu
    except Exception:  # pragma: no cover - 仅在没有 ui 包时发生
        return None
    return draw_jianpu


def _compute_layout(score: Score, style: RenderStyle) -> Dict[str, int]:
    slots_per_bar = max(1, score.meta.slots_per_bar)
    total_slots = max(1, int(round(score.total_slots())))
    bars = max(1, math.ceil(total_slots / slots_per_bar))
    bars_per_row = style.bars_per_row
    rows = max(1, math.ceil(bars / bars_per_row))

    bar_width = slots_per_bar * style.cell_width
    row_height = style.cell_height + 22  # 给小节号留位置
    width = style.margin * 2 + bars_per_row * bar_width + (bars_per_row - 1) * style.bar_gap
    height = style.header_height + rows * row_height + (rows - 1) * style.row_gap + style.margin
    return {
        "slots_per_bar": slots_per_bar,
        "bars": bars,
        "rows": rows,
        "bar_width": bar_width,
        "row_height": row_height,
        "width": int(width),
        "height": int(height),
    }


def _paint(painter, score: Score, keymap: KeyMap, style: RenderStyle) -> None:
    """把曲谱画到 painter 上（PNG 与 PDF 共用）。"""
    from PySide6.QtCore import QRectF, Qt
    from PySide6.QtGui import QBrush, QColor, QFont, QPen

    layout = _compute_layout(score, style)
    slots_per_bar = layout["slots_per_bar"]
    painter.setRenderHint(painter.RenderHint.Antialiasing, True)
    painter.fillRect(0, 0, layout["width"], layout["height"], QColor(COLOR_BG))

    meta = score.meta
    font_title = QFont("Microsoft YaHei", style.title_size // 2 + 4)
    font_title.setBold(True)
    font_meta = QFont("Microsoft YaHei", style.meta_size // 2 + 2)
    font_note = QFont("Consolas", style.note_size // 2 + 3)
    font_note.setBold(True)
    font_bar = QFont("Microsoft YaHei", style.bar_number_size // 2 + 2)

    painter.setPen(QColor(COLOR_TEXT))
    painter.setFont(font_title)
    painter.drawText(style.margin, style.margin + 26, meta.title or "未命名曲谱")

    painter.setFont(font_meta)
    painter.setPen(QColor(COLOR_MUTED))
    painter.drawText(
        style.margin,
        style.margin + 50,
        "BPM %d ｜ 拍号 %s ｜ 细分 1/%d拍 ｜ 音符 %d 个 ｜ 时长 %.1f 秒"
        % (
            meta.bpm,
            meta.time_signature_text,
            max(1, meta.subdivision),
            len(score.notes),
            score.total_ms() / 1000.0,
        ),
    )
    if keymap.overrides():
        painter.drawText(style.margin, style.margin + 68, "（使用了自定义按键映射）")

    start_map: Dict[int, object] = {}
    coverage: Dict[int, int] = {}
    for note in score.notes:
        slot = int(round(float(note.beat)))
        start_map[slot] = note
        for offset in range(max(1, int(round(float(note.duration))))):
            coverage[slot + offset] = slot

    for bar in range(layout["bars"]):
        row = bar // style.bars_per_row
        column = bar % style.bars_per_row
        x = style.margin + column * (layout["bar_width"] + style.bar_gap)
        y = style.header_height + row * (layout["row_height"] + style.row_gap)

        painter.setFont(font_bar)
        painter.setPen(QColor(COLOR_MUTED))
        painter.drawText(x + 2, y - 6, "第 %d 小节" % (bar + 1))

        bar_rect = QRectF(x, y, layout["bar_width"], style.cell_height)
        painter.setPen(QPen(QColor(COLOR_BAR), 2))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(bar_rect)

        # 网格线
        for offset in range(1, slots_per_bar):
            gx = x + offset * style.cell_width
            is_beat = offset % max(1, meta.slots_per_beat) == 0
            painter.setPen(QPen(QColor(COLOR_BEAT if is_beat else COLOR_CELL), 1))
            painter.drawLine(int(gx), int(y), int(gx), int(y + style.cell_height))

        # 音符
        for offset in range(slots_per_bar):
            slot = bar * slots_per_bar + offset
            cx = x + offset * style.cell_width
            cell_rect = QRectF(cx + 2, y + 2, style.cell_width - 4, style.cell_height - 4)
            note = start_map.get(slot)
            if note is not None:
                keys: List[Key] = note.sorted_keys()
                has_error = False
                has_sharp = False
                for key in keys:
                    has_sharp = has_sharp or key.is_sharp
                    if not key.is_valid(MODE_CHROMATIC) or keymap.stroke_for(key) is None:
                        has_error = True
                if has_error:
                    background = COLOR_ERROR
                elif has_sharp:
                    background = COLOR_NOTE_SHARP
                else:
                    background = COLOR_NOTE_BG
                painter.setBrush(QBrush(QColor(background)))
                painter.setPen(Qt.PenStyle.NoPen)
                chip = cell_rect.adjusted(0, 8, 0, -8)
                painter.drawRoundedRect(chip, 4, 4)

                # 简谱符号（数字 + 上/下八度点），与编辑器格子里的显示完全一致
                ink = QColor(COLOR_NOTE_TEXT)
                count = len(keys)
                font = QFont("Consolas", max(7, style.note_size // 2 + (1 if count == 1 else 0)))
                font.setBold(True)
                draw = _jianpu_painter()
                if draw is None:  # pragma: no cover - ui 不可用时的回退
                    painter.setFont(font)
                    painter.setPen(ink)
                    painter.drawText(
                        chip, Qt.AlignmentFlag.AlignCenter, "+".join(key.jianpu_ascii() for key in keys[:4])
                    )
                elif count <= 4:
                    line_height = chip.height() / count
                    for index, key in enumerate(keys):
                        line = QRectF(
                            chip.left(), chip.top() + index * line_height, chip.width(), line_height
                        ).toRect()
                        draw(painter, line, key, font=font, color=ink)
                else:
                    top = QRectF(chip.left(), chip.top() + 1, chip.width(), chip.height() / 2).toRect()
                    bottom = QRectF(
                        chip.left(), chip.center().y() - 1, chip.width(), chip.height() / 2 - 2
                    ).toRect()
                    draw(painter, top, keys[0], font=font, color=ink)
                    painter.setFont(font)
                    painter.setPen(ink)
                    painter.drawText(bottom, Qt.AlignmentFlag.AlignCenter, "+%d" % (count - 1))

                span = max(1, int(round(float(note.duration))))
                if span > 1:
                    mid_y = y + style.cell_height / 2
                    painter.setPen(QPen(QColor(COLOR_TIE), 2))
                    painter.drawLine(
                        int(cx + style.cell_width - 6),
                        int(mid_y),
                        int(cx + style.cell_width * span - 6),
                        int(mid_y),
                    )
            elif slot in coverage:
                # 延音延续段
                mid_y = y + style.cell_height / 2
                painter.setPen(QPen(QColor(COLOR_TIE), 2))
                painter.drawLine(int(cx), int(mid_y), int(cx + style.cell_width), int(mid_y))


def render_score_image(score: Score, keymap: Optional[KeyMap] = None, style: Optional[RenderStyle] = None):
    """把整首曲谱渲染成 :class:`QImage`（供 PNG 导出与界面预览复用）。"""
    _require_qt()
    _ensure_app()
    from PySide6.QtGui import QImage, QPainter

    style = style or RenderStyle()
    keymap = keymap or KeyMap()
    layout = _compute_layout(score, style)
    image = QImage(layout["width"], layout["height"], QImage.Format.Format_ARGB32)
    image.fill(0xFFFFFFFF)
    painter = QPainter(image)
    try:
        _paint(painter, score, keymap, style)
    finally:
        painter.end()
    return image


def export_png(score: Score, path: str, keymap: Optional[KeyMap] = None, style: Optional[RenderStyle] = None) -> str:
    """导出 PNG 图片，返回绝对路径。"""
    target = os.path.abspath(path)
    if not target.lower().endswith(".png"):
        target += ".png"
    image = render_score_image(score, keymap, style)
    if not image.save(target, "PNG"):
        raise ScoreIOError("保存图片失败：%s" % target)
    return target


def export_pdf(score: Score, path: str, keymap: Optional[KeyMap] = None, style: Optional[RenderStyle] = None) -> str:
    """导出单页 PDF（页面高度按内容自适应），返回绝对路径。"""
    _require_qt()
    _ensure_app()
    from PySide6.QtCore import QMarginsF, QSizeF
    from PySide6.QtGui import QPageSize, QPainter, QPdfWriter

    style = style or RenderStyle()
    keymap = keymap or KeyMap()
    target = os.path.abspath(path)
    if not target.lower().endswith(".pdf"):
        target += ".pdf"

    layout = _compute_layout(score, style)
    dpi = 96.0
    width_mm = layout["width"] / dpi * 25.4
    height_mm = layout["height"] / dpi * 25.4

    writer = QPdfWriter(target)
    writer.setResolution(int(dpi))
    writer.setTitle(score.meta.title or "曲谱")
    writer.setCreator("鸣潮自动演奏器")
    unit = getattr(QPageSize, "Unit", QPageSize)
    try:
        page_size = QPageSize(QSizeF(width_mm, height_mm), unit.Millimeter, "WutheringScore")
    except TypeError:  # pragma: no cover - 兼容旧版 PySide6 签名
        page_size = QPageSize(QSizeF(width_mm, height_mm), unit.Millimeter)
    writer.setPageSize(page_size)
    writer.setPageMargins(QMarginsF(0, 0, 0, 0))

    painter = QPainter()
    if not painter.begin(writer):
        raise ScoreIOError("无法写入 PDF：%s" % target)
    try:
        _paint(painter, score, keymap, style)
    finally:
        painter.end()
    return target
