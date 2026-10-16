# -*- coding: utf-8 -*-
"""虚拟键盘。

* **自然阶模式**：3 行 × 7 键（高音 q~u / 中音 a~j / 低音 z~m），与游戏内布局一致；
* **半音阶模式**：3 行 × 12 键，白键与黑键按钢琴方式交错排列（黑键更窄更靠上），
  键帽显示实际按键（组合键显示为 ``C+Z``、``⇧Z``）。

单击即往当前格插入对应音；演奏过程中会高亮正在发声的键。
键位文字来自 :class:`core.notes.KeyMap`，因此用户改键后这里会自动跟着变。
"""

from __future__ import annotations

from typing import Dict, List, Optional, Set

from PySide6.QtCore import QPoint, QRect, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import QWidget

from core.notes import (
    DEGREE_MAX,
    DEGREE_MIN,
    MODE_CHROMATIC,
    MODE_DIATONIC,
    NATURAL_SEMITONES,
    OCTAVE_NAMES,
    Key,
    KeyMap,
    KeyStroke,
)

from .theme import Theme

__all__ = ["PianoBar"]

_ROW_HEIGHT = 36
_WHITE_WIDTH = 46
_WHITE_HEIGHT = 30
_BLACK_WIDTH = 26
_BLACK_HEIGHT = 19
_LABEL_WIDTH = 52
_PADDING = 10

#: 黑键跟随的白键序号（黑键 semitone → 它左边那个白键在 0..6 中的位置）
_BLACK_AFTER = {1: 0, 3: 1, 6: 3, 8: 4, 10: 5}
_WHITE_SEMITONES = NATURAL_SEMITONES  # (0, 2, 4, 5, 7, 9, 11)


class PianoBar(QWidget):
    """三个八度的虚拟键盘（按模式切换 7 键 / 12 键布局）。"""

    keyClicked = Signal(object)  # 左键：插入该音，光标前进一格
    keyToggled = Signal(object)  # 右键：加入 / 移出当前格（不前进，方便写和弦）

    def __init__(self, keymap: Optional[KeyMap] = None, parent=None) -> None:
        super().__init__(parent)
        self._keymap = keymap or KeyMap(MODE_DIATONIC)
        self._mode = self._keymap.mode
        self._highlight: Set[Key] = set()
        self._hover: Optional[Key] = None
        self.setMouseTracking(True)
        self.setMinimumHeight(_PADDING * 2 + 3 * _ROW_HEIGHT)
        self.setToolTip(
            "左键点击琴键：插入该音，光标前进一格\n"
            "右键点击琴键：加入 / 移出当前格（不前进，方便点着写和弦）\n"
            "也可以直接用电脑键盘敲对应字母；按住空格再敲多个音 = 同一格和弦"
        )

    # ------------------------------------------------------------------ #
    # 状态
    # ------------------------------------------------------------------ #
    @property
    def mode(self) -> str:
        return self._mode

    def set_keymap(self, keymap: KeyMap) -> None:
        self._keymap = keymap
        self._mode = keymap.mode
        self.update()

    def set_highlight(self, keys: Optional[Set[Key]]) -> None:
        """高亮正在发声的音（演奏中由进度回调驱动）。"""
        new_value = set(keys or ())
        if new_value != self._highlight:
            self._highlight = new_value
            self.update()

    # ------------------------------------------------------------------ #
    # 几何
    # ------------------------------------------------------------------ #
    def _row_top(self, octave: int) -> int:
        row = {1: 0, 0: 1, -1: 2}[octave]
        return _PADDING + row * _ROW_HEIGHT

    def _white_rect(self, octave: int, semitone: int) -> QRect:
        column = _WHITE_SEMITONES.index(semitone)
        x = _PADDING + _LABEL_WIDTH + column * (_WHITE_WIDTH + 4)
        y = self._row_top(octave) + (_ROW_HEIGHT - _WHITE_HEIGHT) // 2 + 4
        return QRect(x, y, _WHITE_WIDTH, _WHITE_HEIGHT)

    def _black_rect(self, octave: int, semitone: int) -> QRect:
        after = _BLACK_AFTER[semitone]
        x = _PADDING + _LABEL_WIDTH + after * (_WHITE_WIDTH + 4) + _WHITE_WIDTH + 4 - _BLACK_WIDTH // 2
        y = self._row_top(octave) + (_ROW_HEIGHT - _WHITE_HEIGHT) // 2 + 2
        return QRect(x, y, _BLACK_WIDTH, _BLACK_HEIGHT)

    def _key_rect(self, key: Key) -> Optional[QRect]:
        if self._mode == MODE_CHROMATIC:
            if key.semitone in _BLACK_AFTER:
                return self._black_rect(key.octave, key.semitone)
            if key.semitone in _WHITE_SEMITONES:
                return self._white_rect(key.octave, key.semitone)
            return None
        if key.semitone in _WHITE_SEMITONES:
            return self._white_rect(key.octave, key.semitone)
        return None

    def _key_at(self, point: QPoint) -> Optional[Key]:
        # 黑键画在白键之上，命中测试也要先问黑键
        for octave in (1, 0, -1):
            if self._mode == MODE_CHROMATIC:
                for semitone in sorted(_BLACK_AFTER):
                    if self._black_rect(octave, semitone).contains(point):
                        return Key(octave, semitone)
            for semitone in _WHITE_SEMITONES:
                if self._white_rect(octave, semitone).contains(point):
                    return Key(octave, semitone)
        return None

    def sizeHint(self) -> QSize:  # noqa: N802
        width = _PADDING * 2 + _LABEL_WIDTH + 7 * (_WHITE_WIDTH + 4)
        return QSize(width, _PADDING * 2 + 3 * _ROW_HEIGHT)

    # ------------------------------------------------------------------ #
    # 交互
    # ------------------------------------------------------------------ #
    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        hovered = self._key_at(event.position().toPoint())
        if hovered != self._hover:
            self._hover = hovered
            self.setCursor(Qt.CursorShape.PointingHandCursor if hovered else Qt.CursorShape.ArrowCursor)
            if hovered is not None:
                stroke = self._keymap.stroke_for(hovered)
                self.setToolTip(
                    "%s → %s" % (hovered.label(), stroke.display() if stroke else "未绑定按键")
                )
            self.update()

    def leaveEvent(self, event) -> None:  # noqa: N802
        self._hover = None
        self.update()

    def mousePressEvent(self, event) -> None:  # noqa: N802
        key = self._key_at(event.position().toPoint())
        if key is None:
            return
        if event.button() == Qt.MouseButton.LeftButton:
            self.keyClicked.emit(key)
        elif event.button() == Qt.MouseButton.RightButton:
            self.keyToggled.emit(key)

    # ------------------------------------------------------------------ #
    # 绘制
    # ------------------------------------------------------------------ #
    def _paint_key(self, painter: QPainter, key: Key, rect: QRect, black: bool) -> None:
        stroke = self._keymap.stroke_for(key)
        active = key in self._highlight
        hovered = key == self._hover

        if active:
            background = QColor(Theme.PIANO_KEY_ACTIVE)
        elif black:
            background = QColor(Theme.PIANO_KEY_BLACK)
        else:
            background = QColor(Theme.PIANO_KEY)
        if hovered and not active:
            background = QColor(Theme.ACCENT_DIM)

        painter.setBrush(background)
        painter.setPen(QPen(QColor(Theme.PIANO_KEY_BORDER), 1))
        painter.drawRoundedRect(rect, 5 if not black else 3, 5 if not black else 3)

        text_color = QColor("#0d1017" if active else Theme.PIANO_KEY_TEXT)
        if stroke is None:
            label = "—"
        elif stroke.is_combo:
            label = stroke.display()
        else:
            label = stroke.main.upper()
        font = QFont("Consolas", 8 if len(label) > 2 else (11 if not black else 9))
        font.setBold(True)
        painter.setFont(font)
        painter.setPen(text_color)
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, label)

        # 白键下面标音级 / 半音简谱（黑键太小就不标了）
        if not black:
            painter.setFont(QFont("Microsoft YaHei", 7))
            painter.setPen(QColor("#0d1017" if active else Theme.PIANO_LABEL))
            painter.drawText(
                rect.adjusted(0, 0, 0, -1),
                Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignBottom,
                key.short_label(),
            )

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.fillRect(self.rect(), QColor(Theme.PANEL))

        label_font = QFont("Microsoft YaHei", 9)
        for octave in (1, 0, -1):
            row_rect = QRect(
                _PADDING,
                self._row_top(octave),
                _LABEL_WIDTH - 6,
                _ROW_HEIGHT,
            )
            painter.setFont(label_font)
            painter.setPen(QColor(Theme.PIANO_LABEL))
            painter.drawText(
                row_rect, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, OCTAVE_NAMES[octave]
            )

            # 先画白键，再画黑键（黑键压在白键上，像真钢琴）
            for semitone in _WHITE_SEMITONES:
                self._paint_key(painter, Key(octave, semitone), self._white_rect(octave, semitone), False)
            if self._mode == MODE_CHROMATIC:
                for semitone in sorted(_BLACK_AFTER):
                    self._paint_key(painter, Key(octave, semitone), self._black_rect(octave, semitone), True)

    # ------------------------------------------------------------------ #
    def describe_rows(self) -> Dict[int, str]:
        return {octave: self._keymap.row_chars(octave) for octave in (1, 0, -1)}
