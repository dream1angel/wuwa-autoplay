# -*- coding: utf-8 -*-
"""节拍网格控件（自绘）。

布局
----
* 横向按最小细分单位（格）切分，**按小节强制换行**，一行放几个小节由控件宽度自动决定；
* 每格可容纳多个音（和弦），在格内纵向叠显字母；
* 时值 > 1 格的音符用横线连接（延音），被延音占用的格子画成暗色，不能再落音；
* 光标格高亮边框，鼠标框选/Shift+方向键可选中一段乐句；
* 演奏时当前小节整段高亮，并有一条绿色播放头。

编辑动作不在这里做撤销：调用方（主窗口）通过 :meth:`set_edit_handler` 注入
「先快照、执行、再入撤销栈」的包装器，保证所有编辑路径都能撤销。
"""

from __future__ import annotations

from typing import Callable, List, Optional, Sequence, Set, Tuple

from PySide6.QtCore import QPoint, QRect, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import QAbstractScrollArea, QScrollArea, QWidget

from core.model import EPS, Score, ScoreNote
from core.notes import (
    DEGREE_MAX,
    DEGREE_MIN,
    MODE_CHROMATIC,
    MODE_DIATONIC,
    Key,
    KeyMap,
    key_sort_index,
)
from core.timeline import Clip, Timeline

from .notation import draw_jianpu
from .theme import Theme

__all__ = ["GridWidget", "GridScrollArea"]


class GridScrollArea(QScrollArea):
    """承载节拍网格的滚动区。

    **为什么要专门写一个子类**：``QAbstractScrollArea`` 会在 ``keyPressEvent`` 里
    把方向键 / 翻页键 / Home / End **吃掉用于滚动画布**，事件因此永远冒泡不到主窗口，
    结果是「鼠标点过网格之后，方向键就再也移动不了选中框」。
    这里把这些编辑用键原样 ``ignore()`` 掉，让 Qt 继续向父窗口冒泡。
    """

    #: 需要让给主窗口处理的编辑键（含按住空格写和弦的 keyRelease）
    _PASS_THROUGH = frozenset(
        {
            Qt.Key.Key_Left,
            Qt.Key.Key_Right,
            Qt.Key.Key_Up,
            Qt.Key.Key_Down,
            Qt.Key.Key_Space,
            Qt.Key.Key_Backspace,
            Qt.Key.Key_Delete,
            Qt.Key.Key_Return,
            Qt.Key.Key_Enter,
            Qt.Key.Key_Escape,
            Qt.Key.Key_Home,
            Qt.Key.Key_End,
            Qt.Key.Key_PageUp,
            Qt.Key.Key_PageDown,
            Qt.Key.Key_BracketLeft,
            Qt.Key.Key_BracketRight,
            Qt.Key.Key_Minus,
            Qt.Key.Key_Equal,
        }
    )

    def keyPressEvent(self, event) -> None:  # noqa: N802
        if event.key() in self._PASS_THROUGH:
            event.ignore()          # 交给父窗口（MainWindow）处理
            return
        super().keyPressEvent(event)

    def keyReleaseEvent(self, event) -> None:  # noqa: N802
        if event.key() in self._PASS_THROUGH:
            event.ignore()
            return
        super().keyReleaseEvent(event)


class GridWidget(QWidget):
    """可视化的曲谱编辑网格。"""

    cursorChanged = Signal(int)
    selectionChanged = Signal(int, int)
    editNotified = Signal(str)

    #: 布局常量（像素）
    CELL_W = 36
    CELL_H = 58
    BAR_GAP = 18
    ROW_GAP = 24
    MARGIN = 12
    HEADER = 20

    def __init__(self, score: Score, keymap: Optional[KeyMap] = None, parent=None) -> None:
        super().__init__(parent)
        self._score = score
        self._keymap = keymap or KeyMap()
        self._timeline = Timeline(score)
        self._edit_handler: Optional[Callable[[str, Callable[[], None]], None]] = None

        self._cursor = 0
        self._anchor: Optional[int] = None
        self._selection: Optional[Tuple[int, int]] = None
        self._dragging = False
        self._clipboard: Optional[Clip] = None
        self._playhead_slot: Optional[int] = None
        self._playhead_bar: Optional[int] = None
        self._error_slots: Set[int] = set()
        #: 和弦输入模式（按住空格）：光标格加一层醒目标记
        self._chord_mode = False

        self.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        self.setMouseTracking(True)
        self.setMinimumHeight(200)
        self.setToolTip(
            "单击选中格；拖动框选乐句；\n"
            "用电脑键盘敲 q/w/e… 或点击下方虚拟键盘输入音符；空格=休止符"
        )
        self.refresh()

    # ------------------------------------------------------------------ #
    # 数据绑定
    # ------------------------------------------------------------------ #
    def set_score(self, score: Score) -> None:
        self._score = score
        self._timeline = Timeline(score)
        self._cursor = 0
        self._anchor = None
        self._selection = None
        self._clipboard = None
        self.clear_playhead()
        self.refresh()
        self.cursorChanged.emit(self._cursor)
        self.selectionChanged.emit(-1, -1)

    def set_keymap(self, keymap: KeyMap) -> None:
        self._keymap = keymap
        self.refresh()

    @property
    def mode(self) -> str:
        return self._score.meta.mode

    @property
    def is_chromatic(self) -> bool:
        return self._score.meta.mode == MODE_CHROMATIC

    def set_edit_handler(self, handler: Callable[[str, Callable[[], None]], None]) -> None:
        """注入编辑包装器：``handler(操作名, 真正的修改函数)``。"""
        self._edit_handler = handler

    @property
    def score(self) -> Score:
        return self._score

    @property
    def timeline(self) -> Timeline:
        return self._timeline

    @property
    def keymap(self) -> KeyMap:
        return self._keymap

    # ------------------------------------------------------------------ #
    # 光标与选区
    # ------------------------------------------------------------------ #
    @property
    def cursor(self) -> int:
        return self._cursor

    def set_cursor(self, slot: int, *, ensure_visible: bool = True, emit: bool = True) -> None:
        slot = max(0, int(slot))
        changed = slot != self._cursor
        self._cursor = slot
        if ensure_visible:
            self._ensure_visible(self.slot_rect(slot))
        if changed or emit:
            self.update()
        if changed:
            self.cursorChanged.emit(slot)

    def selection_range(self) -> Optional[Tuple[int, int]]:
        """返回规范化后的选区 ``(起, 止)``；无选区返回 None。"""
        if self._selection is None:
            return None
        lo, hi = sorted(self._selection)
        return lo, hi

    def effective_range(self) -> Tuple[int, int]:
        """有选区用选区，否则用当前光标格。"""
        selected = self.selection_range()
        if selected is not None:
            return selected
        return self._cursor, self._cursor

    def clear_selection(self) -> None:
        if self._selection is not None:
            self._selection = None
            self._anchor = None
            self.update()
            self.selectionChanged.emit(-1, -1)

    def select_range(self, start: int, end: int) -> None:
        lo, hi = sorted((max(0, int(start)), max(0, int(end))))
        self._anchor = lo
        self._selection = (lo, hi)
        self.update()
        self.selectionChanged.emit(lo, hi)

    def select_all(self) -> None:
        last = max(0, self._timeline.total_slots - 1)
        self.select_range(0, last)
        self.editNotified.emit("已选中整首曲谱（%d 格）" % (last + 1))

    def move_cursor(self, delta_slots: int = 0, delta_rows: int = 0, *, extend: bool = False) -> None:
        """移动光标 / 选中框。

        * 无选区 + 方向键：移动光标（选中框）
        * **有选区 + 方向键：整体平移选中框**（框内音符不动，方便先框好再操作）
        * ``Shift + 方向键``：以锚点扩展 / 收缩选区
        * ``Esc``：取消选区（之后方向键恢复为移动光标）
        """
        if extend:
            slot = self._shifted_slot(delta_slots, delta_rows)
            if self._anchor is None:
                self._anchor = self._cursor
            self._cursor = slot
            self._selection = (self._anchor, slot)
            self.update()
            self.selectionChanged.emit(*sorted(self._selection))
            self.cursorChanged.emit(slot)
            return
        if self._selection is not None:
            self.translate_selection(delta_slots, delta_rows)
            return
        self.set_cursor(self._shifted_slot(delta_slots, delta_rows))

    def _shifted_slot(self, delta_slots: int = 0, delta_rows: int = 0) -> int:
        """按「格」和「整行」计算目标槽位（一行 = 当前宽度下的一排小节）。"""
        slot = self._cursor + int(delta_slots)
        if delta_rows:
            step = self._bars_per_row() * self._timeline.slots_per_bar
            slot += int(delta_rows) * step
        return max(0, slot)

    def translate_selection(self, delta_slots: int = 0, delta_rows: int = 0) -> bool:
        """整体平移选中的格子区间（框移动，音符不动）。返回是否真的移动了。"""
        selected = self.selection_range()
        if selected is None:
            return False
        step = int(delta_slots)
        if delta_rows:
            step += int(delta_rows) * self._bars_per_row() * self._timeline.slots_per_bar
        if step == 0:
            return False
        lo, hi = selected
        if lo + step < 0:                      # 贴住左边界
            step = -lo
        if step == 0:
            return False
        lo, hi = lo + step, hi + step
        self._selection = (lo, hi)
        self._anchor = lo
        self._cursor = lo
        self._ensure_visible(self.slot_rect(hi), margin=60)
        self.update()
        self.selectionChanged.emit(lo, hi)
        self.cursorChanged.emit(self._cursor)
        self.editNotified.emit("选中框移动到第 %d~%d 格" % (lo, hi))
        return True

    # ------------------------------------------------------------------ #
    # 编辑操作（全部经由 edit_handler 入撤销栈）
    # ------------------------------------------------------------------ #
    def _edit(self, label: str, action: Callable[[], None]) -> None:
        if self._edit_handler is not None:
            self._edit_handler(label, action)
        else:  # pragma: no cover - 独立使用时的兜底
            action()
            self.refresh()
        self.editNotified.emit(label)

    def insert_keys(self, keys: Sequence[Key], *, advance: bool = True) -> None:
        """在当前格插入若干音（与已有音合并成和弦）。"""
        if not keys:
            return
        slot = self._cursor

        def action() -> None:
            # 若当前格被前一个音的延音占用，先把延音截断到这一格之前
            if self._timeline.is_continuation(slot):
                holder = self._timeline.covering_note(slot)
                if holder is not None:
                    start = self._timeline.slot_of(holder.beat)
                    self._timeline.set_duration(start, max(1, slot - start))
            merged = list(self._timeline.cell_keys(slot))
            for key in keys:
                if key not in merged:
                    merged.append(key)
            self._timeline.set_cell(slot, merged)

        names = "+".join(key.label() for key in keys)
        self._edit("插入 %s" % names, action)
        if advance:
            self.set_cursor(slot + 1)

    def toggle_key(self, key: Key) -> None:
        """插入/移除当前格中的某个音。"""
        slot = self._cursor

        def action() -> None:
            if self._timeline.is_continuation(slot):
                holder = self._timeline.covering_note(slot)
                if holder is not None:
                    start = self._timeline.slot_of(holder.beat)
                    self._timeline.set_duration(start, max(1, slot - start))
            self._timeline.toggle_key(slot, key)

        self._edit("切换 %s" % key.label(), action)

    def clear_cell(self, *, advance: bool = False) -> None:
        """清空当前格（休止符）或删除选区。"""
        slot = self._cursor
        selected = self.selection_range()

        def action() -> None:
            if selected is not None:
                self._timeline.delete_region(selected[0], selected[1])
            else:
                self._timeline.clear_cell(slot)

        label = "删除选区" if selected is not None else "休止符"
        self._edit(label, action)
        if selected is not None:
            self.clear_selection()
            self.set_cursor(selected[0])
        elif advance:
            self.set_cursor(slot + 1)

    def bump_duration(self, delta: int) -> None:
        """延长/缩短当前音符的时值（延音）。"""
        slot = self._cursor
        holder = self._timeline.covering_note(slot)
        if holder is None:
            self.editNotified.emit("当前格没有音符，无法设置时值")
            return
        start = self._timeline.slot_of(holder.beat)

        def action() -> None:
            self._timeline.bump_duration(start, delta)

        limit = "（延音不能盖住后面的音符）"
        self._edit("时值 %+d 格 %s" % (delta, limit if delta > 0 else ""), action)

    def set_duration(self, duration: int) -> None:
        holder = self._timeline.covering_note(self._cursor)
        if holder is None:
            self.editNotified.emit("当前格没有音符")
            return
        start = self._timeline.slot_of(holder.beat)

        def action() -> None:
            self._timeline.set_duration(start, duration)

        self._edit("时值设为 %d 格" % duration, action)

    def delete_backward(self) -> None:
        """Backspace：回到上一格并清空它。"""
        if self._cursor > 0:
            self.set_cursor(self._cursor - 1)
        self.clear_cell()

    def copy_selection(self) -> None:
        lo, hi = self.effective_range()
        clip = self._timeline.copy_region(lo, hi)
        if clip.is_empty:
            self.editNotified.emit("所选范围内没有音符")
            return
        self._clipboard = clip
        self.editNotified.emit("已复制 %s" % clip.describe(self._keymap))

    def cut_selection(self) -> None:
        self.copy_selection()
        if self._clipboard is None or self._clipboard.is_empty:
            return
        lo, hi = self.effective_range()

        def action() -> None:
            self._timeline.delete_region(lo, hi)

        self._edit("剪切乐句", action)
        self.clear_selection()
        self.set_cursor(lo)

    def paste_clipboard(self, mode: str = "merge") -> None:
        if self._clipboard is None or self._clipboard.is_empty:
            self.editNotified.emit("剪贴板里还没有乐句")
            return
        clip = self._clipboard
        slot = self._cursor

        def action() -> None:
            self._timeline.paste(clip, slot, mode=mode)

        self._edit("粘贴乐句（%s）" % ("合并" if mode == "merge" else "覆盖"), action)
        self.set_cursor(slot + max(1, clip.length_slots))
        self.clear_selection()

    def duplicate_selection(self) -> None:
        """Ctrl+D：把当前格/选区内容复制到选区之后。"""
        lo, hi = self.effective_range()
        clip = self._timeline.copy_region(lo, hi)
        if clip.is_empty:
            self.editNotified.emit("所选范围内没有音符")
            return
        target = hi + 1

        def action() -> None:
            self._timeline.paste(clip, target, mode="merge")

        self._edit("复制乐句到 %d 格" % target, action)
        self.set_cursor(target + clip.length_slots)
        self.clear_selection()

    def transpose(self, degree_steps: int = 0, octave_steps: int = 0, *, whole: bool = False) -> None:
        """移调 / 升降八度（选区或整首）。"""
        lo, hi = (0, max(0, self._timeline.total_slots - 1)) if whole else self.effective_range()
        if degree_steps == 0 and octave_steps == 0:
            return

        def action() -> None:
            self._timeline.transpose_region(lo, hi, degree_steps, octave_steps)

        parts = []
        if octave_steps:
            parts.append("八度 %+d" % octave_steps)
        if degree_steps:
            parts.append("音级 %+d" % degree_steps)
        self._edit("移调（%s）" % "，".join(parts), action)

    def transpose_semitones(self, steps: int, *, whole: bool = False) -> None:
        """按半音移调（±1 = 半音，±12 = 八度），半音阶模式专用。"""
        if steps == 0:
            return
        lo, hi = (0, max(0, self._timeline.total_slots - 1)) if whole else self.effective_range()

        def action() -> None:
            self._timeline.transpose_region_semitones(lo, hi, steps)

        self._edit("移调 %+d 半音" % steps, action)

    # ------------------------------------------------------------------ #
    # 播放可视化
    # ------------------------------------------------------------------ #
    def set_playhead(self, slot: Optional[int], bar: Optional[int] = None) -> None:
        new_slot = None if slot is None else max(0, int(slot))
        if new_slot == self._playhead_slot and bar == self._playhead_bar:
            return
        self._playhead_slot = new_slot
        self._playhead_bar = bar
        if new_slot is not None:
            self._ensure_visible(self.slot_rect(new_slot), margin=80)
        self.update()

    def clear_playhead(self) -> None:
        self._playhead_slot = None
        self._playhead_bar = None
        self.update()

    def set_chord_mode(self, active: bool) -> None:
        """和弦输入模式：给光标格加醒目标记。"""
        if self._chord_mode != bool(active):
            self._chord_mode = bool(active)
            self.update()

    def keys_at_playhead(self) -> Set[Key]:
        if self._playhead_slot is None:
            return set()
        note = self._timeline.covering_note(self._playhead_slot)
        return set(note.keys) if note else set()

    # ------------------------------------------------------------------ #
    # 布局计算
    # ------------------------------------------------------------------ #
    @property
    def slots_per_bar(self) -> int:
        return self._timeline.slots_per_bar

    def _bars_per_row(self) -> int:
        unit = self.slots_per_bar * self.CELL_W + self.BAR_GAP
        usable = max(unit, self.width() - 2 * self.MARGIN)
        return max(1, int(usable // unit))

    def _row_pitch(self) -> int:
        return self.HEADER + self.CELL_H + self.ROW_GAP

    def bar_count(self) -> int:
        """至少覆盖光标所在小节，编辑到后面时网格会同步变长。"""
        cursor_bar = self._cursor // self.slots_per_bar + 1
        return max(self._timeline.bar_count(), cursor_bar, 1)

    def slot_rect(self, slot: int) -> QRect:
        per_row = self._bars_per_row()
        bar, offset = divmod(int(slot), self.slots_per_bar)
        row, column = divmod(bar, per_row)
        x = self.MARGIN + column * (self.slots_per_bar * self.CELL_W + self.BAR_GAP) + offset * self.CELL_W
        y = self.MARGIN + row * self._row_pitch() + self.HEADER
        return QRect(x, y, self.CELL_W, self.CELL_H)

    def slot_at(self, point: QPoint) -> Optional[int]:
        """屏幕坐标 → 格号（点在格间空隙时返回 None）。"""
        per_row = self._bars_per_row()
        x = point.x() - self.MARGIN
        y = point.y() - self.MARGIN
        if x < 0 or y < 0:
            return None
        row, y_in_row = divmod(y, self._row_pitch())
        if y_in_row > self.HEADER + self.CELL_H:
            return None
        bar_pitch = self.slots_per_bar * self.CELL_W + self.BAR_GAP
        column, x_in_bar = divmod(x, bar_pitch)
        if column >= per_row or x_in_bar >= self.slots_per_bar * self.CELL_W:
            return None
        bar = int(row * per_row + column)
        offset = int(x_in_bar // self.CELL_W)
        return max(0, bar * self.slots_per_bar + offset)

    def content_height(self) -> int:
        rows = (self.bar_count() + self._bars_per_row() - 1) // self._bars_per_row()
        return self.MARGIN * 2 + rows * self._row_pitch()

    def _recalc_height(self) -> None:
        height = self.content_height()
        if height != self.minimumHeight():
            self.setMinimumHeight(height)

    def _ensure_visible(self, rect: QRect, margin: int = 40) -> None:
        area = self.parentWidget()
        while area is not None and not isinstance(area, QAbstractScrollArea):
            area = area.parentWidget()
        if isinstance(area, QAbstractScrollArea):
            area.ensureVisible(rect.center().x(), rect.center().y(), 20, margin)

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._recalc_height()

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(self.width(), self.content_height())

    # ------------------------------------------------------------------ #
    # 刷新
    # ------------------------------------------------------------------ #
    def refresh(self) -> None:
        """数据变化后调用：重算高度、收集越界/无映射音、重绘。"""
        mode = self.mode
        errors: Set[int] = set()
        for note in self._score.notes:
            slot = self._timeline.slot_of(note.beat)
            for key in note.keys:
                if not key.is_valid(mode) or self._keymap.stroke_for(key) is None:
                    errors.add(slot)
                    break
        self._error_slots = errors
        self._recalc_height()
        self.update()

    # ------------------------------------------------------------------ #
    # 鼠标
    # ------------------------------------------------------------------ #
    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() != Qt.MouseButton.LeftButton:
            return
        slot = self.slot_at(event.position().toPoint())
        if slot is None:
            return
        self.setFocus()
        self._dragging = True
        self._anchor = slot
        self._selection = None
        self.set_cursor(slot)
        self.selectionChanged.emit(-1, -1)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if not self._dragging or self._anchor is None:
            return
        slot = self.slot_at(event.position().toPoint())
        if slot is None or slot == self._anchor:
            self._selection = None
        else:
            self._selection = (self._anchor, slot)
            self._ensure_visible(self.slot_rect(slot), margin=60)
        self.update()
        if self._selection is None:
            self.selectionChanged.emit(-1, -1)
        else:
            self.selectionChanged.emit(*sorted(self._selection))

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if event.button() != Qt.MouseButton.LeftButton:
            return
        self._dragging = False
        if self._selection is not None:
            lo, hi = sorted(self._selection)
            self.editNotified.emit("已选中 %d 格" % (hi - lo + 1))
        self._anchor = None

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802
        """双击某格：选中该音符的完整时值范围。"""
        slot = self.slot_at(event.position().toPoint())
        if slot is None:
            return
        holder = self._timeline.covering_note(slot)
        if holder is None:
            return
        start = self._timeline.slot_of(holder.beat)
        end = start + max(1, int(round(holder.duration))) - 1
        self.set_cursor(start)
        self.select_range(start, end)

    # ------------------------------------------------------------------ #
    # 绘制
    # ------------------------------------------------------------------ #
    def _paint_cell(self, painter: QPainter, slot: int, rect: QRect) -> None:
        note = self._timeline.note_at(slot)
        if note is not None:
            self._paint_note(painter, note, slot, rect)
            return
        if self._timeline.is_continuation(slot):
            painter.fillRect(rect.adjusted(1, 2, -1, -2), QColor(Theme.GRID_CONTINUATION))
            painter.setPen(QPen(QColor(Theme.NOTE_TIE), 2))
            mid_y = rect.center().y()
            painter.drawLine(rect.left(), mid_y, rect.right(), mid_y)

    def _paint_note(self, painter: QPainter, note: ScoreNote, slot: int, rect: QRect) -> None:
        keys: List[Key] = note.sorted_keys()
        has_error = slot in self._error_slots
        has_sharp = any(key.is_sharp for key in keys)
        if not has_error:
            for key in keys:
                if self._keymap.stroke_for(key) is None:
                    has_error = True
                    break
        body = rect.adjusted(2, 2, -2, -2)
        painter.setPen(Qt.PenStyle.NoPen)
        if has_error:
            background = Theme.NOTE_ERROR
        elif has_sharp:
            background = Theme.NOTE_SHARP
        else:
            background = Theme.NOTE_BG
        painter.setBrush(QColor(background))
        painter.drawRoundedRect(body, 5, 5)

        # 格内符号：两种模式统一用简谱「数字 + 上/下八度点」，
        # 升半音仍在数字前加 #（如 #1）；高音数字上方一点、低音下方一点。
        ink = QColor(Theme.NOTE_TEXT)
        count = len(keys)
        font_size = 13 if count == 1 else (10 if count <= 3 else 8)
        font = QFont("Consolas", font_size)
        font.setBold(True)
        if count <= 4:
            line_height = body.height() / count
            for index, key in enumerate(keys):
                line = QRect(
                    body.left(),
                    int(round(body.top() + index * line_height)),
                    body.width(),
                    int(round(line_height)),
                )
                draw_jianpu(painter, line, key, font=font, color=ink)
        else:
            top = QRect(body.left(), body.top() + 1, body.width(), body.height() // 2)
            bottom = QRect(body.left(), body.center().y() - 1, body.width(), body.height() // 2 - 2)
            draw_jianpu(painter, top, keys[0], font=font, color=ink)
            painter.setFont(font)
            painter.setPen(ink)
            painter.drawText(bottom, Qt.AlignmentFlag.AlignCenter, "+%d" % (count - 1))

        # 延音线：从本格右侧画到最后一格
        span = max(1, int(round(float(note.duration))))
        if span > 1:
            painter.setPen(QPen(QColor(Theme.NOTE_TIE), 2))
            mid_y = rect.center().y()
            painter.drawLine(rect.right() - 2, mid_y, rect.right() + (span - 1) * self.CELL_W, mid_y)

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.fillRect(self.rect(), QColor(Theme.GRID_BG))

        per_row = self._bars_per_row()
        row_pitch = self._row_pitch()
        bar_pitch = self.slots_per_bar * self.CELL_W + self.BAR_GAP
        bars = self.bar_count()

        dirty = event.rect()
        first_row = max(0, (dirty.top() - self.MARGIN) // row_pitch)
        last_row = max(0, (dirty.bottom() - self.MARGIN) // row_pitch)

        number_font = QFont("Microsoft YaHei", 8)
        selection = self.selection_range()
        selected_set: Set[int] = set()
        if selection is not None:
            selected_set = set(range(selection[0], selection[1] + 1))

        for row in range(first_row, last_row + 1):
            for column in range(per_row):
                bar = row * per_row + column
                if bar >= bars:
                    break
                bar_x = self.MARGIN + column * bar_pitch
                bar_y = self.MARGIN + row * row_pitch + self.HEADER

                # 播放小节高亮
                if self._playhead_bar is not None and bar == self._playhead_bar:
                    painter.fillRect(
                        QRect(bar_x - 3, bar_y - self.HEADER + 2, self.slots_per_bar * self.CELL_W + 6, self.CELL_H + 8),
                        QColor(Theme.PLAYHEAD).darker(320),
                    )

                # 小节号
                painter.setFont(number_font)
                painter.setPen(QColor(Theme.GRID_BAR_NUMBER))
                painter.drawText(bar_x + 2, bar_y - 6, "小节 %d" % (bar + 1))

                # 格子与网格线
                for offset in range(self.slots_per_bar):
                    slot = bar * self.slots_per_bar + offset
                    rect = QRect(bar_x + offset * self.CELL_W, bar_y, self.CELL_W, self.CELL_H)
                    painter.setPen(QPen(QColor(Theme.GRID_CELL), 1))
                    painter.setBrush(Qt.BrushStyle.NoBrush)
                    painter.drawRect(rect)
                    if offset % max(1, self._timeline.slots_per_beat) == 0 and offset:
                        painter.setPen(QPen(QColor(Theme.GRID_BEAT), 2))
                        painter.drawLine(rect.left(), rect.top(), rect.left(), rect.bottom())
                    self._paint_cell(painter, slot, rect)

                # 选区
                if selected_set:
                    painter.setPen(Qt.PenStyle.NoPen)
                    painter.setBrush(QColor(Theme.GRID_SELECTION))
                    for offset in range(self.slots_per_bar):
                        slot = bar * self.slots_per_bar + offset
                        if slot in selected_set:
                            painter.drawRect(
                                QRect(bar_x + offset * self.CELL_W + 1, bar_y + 1, self.CELL_W - 1, self.CELL_H - 1)
                            )

                # 光标
                cursor_bar, cursor_offset = divmod(self._cursor, self.slots_per_bar)
                if cursor_bar == bar:
                    cursor_rect = QRect(bar_x + cursor_offset * self.CELL_W, bar_y, self.CELL_W, self.CELL_H)
                    if self._chord_mode:
                        # 和弦输入中：填充 + 更粗的框，一眼看出「现在敲的音都进这一格」
                        painter.setPen(Qt.PenStyle.NoPen)
                        painter.setBrush(QColor(Theme.CHORD_MODE))
                        painter.drawRect(cursor_rect.adjusted(1, 1, -1, -1))
                        painter.setBrush(Qt.BrushStyle.NoBrush)
                        painter.setPen(QPen(QColor(Theme.OK), 2))
                        painter.drawRect(cursor_rect)
                        painter.setFont(QFont("Microsoft YaHei", 7))
                        painter.setPen(QColor(Theme.OK))
                        painter.drawText(
                            cursor_rect.adjusted(2, 1, 0, 0),
                            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop,
                            "和弦",
                        )
                    else:
                        painter.setBrush(Qt.BrushStyle.NoBrush)
                        painter.setPen(QPen(QColor(Theme.GRID_CURSOR), 2))
                        painter.drawRect(cursor_rect)

                # 播放头
                if self._playhead_slot is not None:
                    ph_bar, ph_offset = divmod(self._playhead_slot, self.slots_per_bar)
                    if ph_bar == bar:
                        painter.setPen(QPen(QColor(Theme.PLAYHEAD), 2))
                        x = bar_x + ph_offset * self.CELL_W
                        painter.drawLine(x, bar_y - 4, x, bar_y + self.CELL_H + 4)

        # 空谱提示
        if not self._score.notes:
            painter.setFont(QFont("Microsoft YaHei", 11))
            painter.setPen(QColor(Theme.MUTED))
            painter.drawText(
                QRect(self.MARGIN, self.MARGIN + self.HEADER, self.width() - 2 * self.MARGIN, 40),
                Qt.AlignmentFlag.AlignCenter,
                "用键盘敲 q/w/e… 或点击下方虚拟键盘开始写谱；空格 = 休止符"
                + ("；半音阶模式：先按 # 再按音键可输入升半音" if self.is_chromatic else ""),
            )
