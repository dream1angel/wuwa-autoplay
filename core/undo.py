# -*- coding: utf-8 -*-
"""快照式撤销 / 重做栈。

为什么用整表快照而不是差分命令？
--------------------------------
一首曲谱通常只有几百个音符，深拷贝一次的开销在微秒级；
而「整表快照」让每一个编辑动作（插入、删除、延音、移调、粘贴、框选删除）
都能复用同一条代码路径，不会出现某条分支忘记记录撤销点的 bug。
内存上限由 ``limit`` 控制（默认 200 步），足够覆盖正常编辑习惯。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

from .model import Score, ScoreNote

__all__ = ["clone_notes", "UndoStack"]


def clone_notes(notes: List[ScoreNote]) -> List[ScoreNote]:
    """深拷贝音符列表（Key 是 frozen dataclass，浅拷贝列表即可）。"""
    return [ScoreNote(float(n.beat), float(n.duration), list(n.keys)) for n in notes]


def _same(a: List[ScoreNote], b: List[ScoreNote]) -> bool:
    if len(a) != len(b):
        return False
    for left, right in zip(a, b):
        if not left.same_content(right):
            return False
    return True


@dataclass
class _Snapshot:
    label: str
    before: List[ScoreNote]
    after: List[ScoreNote]
    cursor_before: int = 0
    cursor_after: int = 0
    #: 合并键：同一次「连续手势」（例如按住空格写和弦）产生的多笔编辑共用一个键，
    #: 只占撤销栈里的一步
    merge_key: str = ""


class UndoStack:
    """维护一条命令时间线，并跟踪「已保存位置」以支持脏标记。"""

    def __init__(self, limit: int = 200) -> None:
        self.limit = max(1, int(limit))
        self._items: List[_Snapshot] = []
        self._index = 0  # 已应用的命令数量（0 表示位于初始状态）
        self._saved_index = 0

    # ------------------------------------------------------------------ #
    # 状态
    # ------------------------------------------------------------------ #
    @property
    def can_undo(self) -> bool:
        return self._index > 0

    @property
    def can_redo(self) -> bool:
        return self._index < len(self._items)

    @property
    def is_dirty(self) -> bool:
        """与最近一次保存/新建相比是否有改动。"""
        return self._index != self._saved_index

    def undo_label(self) -> Optional[str]:
        return self._items[self._index - 1].label if self.can_undo else None

    def redo_label(self) -> Optional[str]:
        return self._items[self._index].label if self.can_redo else None

    def clear(self) -> None:
        self._items.clear()
        self._index = 0
        self._saved_index = 0

    def mark_saved(self) -> None:
        self._saved_index = self._index

    # ------------------------------------------------------------------ #
    # 记录
    # ------------------------------------------------------------------ #
    def push(
        self,
        label: str,
        before: List[ScoreNote],
        after: List[ScoreNote],
        cursor_before: int = 0,
        cursor_after: int = 0,
        merge_key: str = "",
    ) -> bool:
        """记录一次编辑；内容无变化时返回 False（不污染撤销栈）。

        ``merge_key`` 非空且与上一步相同时，这次编辑会**并入上一步**
        （保留上一步的 before、更新 after）——「按住空格写和弦」这类
        连续手势因此只占一步撤销：敲了 4 个音再按 Ctrl+Z 会整格回退，
        而不是一个一个音地退。
        """
        if _same(before, after) and cursor_before == cursor_after:
            return False
        # 丢弃 redo 分支
        del self._items[self._index :]
        if (
            merge_key
            and self._index > 0
            and self._items[self._index - 1].merge_key == merge_key
        ):
            previous = self._items[self._index - 1]
            previous.after = clone_notes(after)
            previous.cursor_after = cursor_after
            previous.label = label
            self._index = len(self._items)
            return True
        self._items.append(
            _Snapshot(
                label,
                clone_notes(before),
                clone_notes(after),
                cursor_before,
                cursor_after,
                merge_key,
            )
        )
        if len(self._items) > self.limit:
            drop = len(self._items) - self.limit
            del self._items[:drop]
            self._saved_index = max(0, self._saved_index - drop)
        self._index = len(self._items)
        return True

    # ------------------------------------------------------------------ #
    # 撤销 / 重做
    # ------------------------------------------------------------------ #
    def undo(self, score: Score) -> Optional[int]:
        """回退一步，返回应当恢复的光标槽位；无可撤销时返回 None。"""
        if not self.can_undo:
            return None
        self._index -= 1
        snapshot = self._items[self._index]
        score.notes = clone_notes(snapshot.before)
        return snapshot.cursor_before

    def redo(self, score: Score) -> Optional[int]:
        """前进一步，返回应当恢复的光标槽位；无可重做时返回 None。"""
        if not self.can_redo:
            return None
        snapshot = self._items[self._index]
        score.notes = clone_notes(snapshot.after)
        self._index += 1
        return snapshot.cursor_after
