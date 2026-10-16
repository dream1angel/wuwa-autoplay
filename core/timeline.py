# -*- coding: utf-8 -*-
"""编辑器使用的「槽位 / 小节」换算与批量编辑工具。

术语
----
* **槽位（slot）**：最小细分单位，整数；``slot = beat``（因为 beat 以格为 1）。
* **小节（bar）**：从 0 开始计数，每小节 ``slots_per_bar = 分子 × subdivision`` 格。

本模块只做数据变换，不负责重绘与撤销；撤销由 :mod:`core.undo` 的整表快照实现。
所有编辑方法都直接修改传入的 :class:`~core.model.Score`，并在结束后保持音符有序。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple

from .model import EPS, Score, ScoreNote
from .notes import DEGREE_MAX, DEGREE_MIN, Key, KeyMap, key_sort_index

__all__ = ["Clip", "Timeline"]


@dataclass
class Clip:
    """一段被复制的乐句：音符位置已归一化到 0 槽起算。"""

    notes: List[ScoreNote] = field(default_factory=list)
    length_slots: int = 0

    @property
    def is_empty(self) -> bool:
        return not self.notes

    def describe(self, keymap: Optional[KeyMap] = None) -> str:
        if self.is_empty:
            return "空乐句"
        return "%d 个音符 / 共 %d 格" % (len(self.notes), self.length_slots)


class Timeline:
    """网格视图的几何与编辑逻辑。"""

    def __init__(self, score: Score) -> None:
        self.score = score

    # ------------------------------------------------------------------ #
    # 换算
    # ------------------------------------------------------------------ #
    @property
    def subdivision(self) -> int:
        return int(self.score.meta.subdivision)

    @property
    def time_signature(self) -> Tuple[int, int]:
        return tuple(self.score.meta.time_signature)  # type: ignore[return-value]

    @property
    def slots_per_beat(self) -> int:
        return self.score.meta.slots_per_beat

    @property
    def slots_per_bar(self) -> int:
        return max(1, self.score.meta.slots_per_bar)

    def snap(self, beat: float) -> float:
        """把任意 beat 吸附到最近的整数槽位。"""
        return float(int(round(float(beat))))

    def slot_of(self, beat: float) -> int:
        return int(round(float(beat)))

    def beat_of(self, slot: int) -> float:
        return float(int(slot))

    def bar_of_slot(self, slot: int) -> int:
        return int(slot) // self.slots_per_bar

    def slot_of_bar(self, bar: int) -> int:
        return int(bar) * self.slots_per_bar

    def bar_of_beat(self, beat: float) -> int:
        return self.bar_of_slot(self.slot_of(beat))

    @property
    def total_slots(self) -> int:
        return int(round(self.score.total_slots()))

    def bar_count(self) -> int:
        """至少 1 小节，保证空谱界面也有网格。"""
        used = self.total_slots
        return max(1, (used + self.slots_per_bar - 1) // self.slots_per_bar)

    def time_ms(self, slot: int, speed: float = 1.0) -> float:
        return float(slot) * self.score.meta.ms_per_slot(speed)

    # ------------------------------------------------------------------ #
    # 查询
    # ------------------------------------------------------------------ #
    def slot_map(self) -> Dict[int, ScoreNote]:
        """起始槽位 → 音符。"""
        return {self.slot_of(note.beat): note for note in self.score.notes}

    def note_at(self, slot: int) -> Optional[ScoreNote]:
        return self.score.note_at(self.beat_of(slot))

    def covering_note(self, slot: int) -> Optional[ScoreNote]:
        """返回覆盖该槽位的音符（含延音延续段）。"""
        target = self.beat_of(slot)
        for note in self.score.notes:
            if note.beat - EPS <= target < note.end - EPS:
                return note
        return None

    def is_continuation(self, slot: int) -> bool:
        """该槽位是否只是前一个音符的延音延续（不可再落音）。"""
        holder = self.covering_note(slot)
        return holder is not None and self.slot_of(holder.beat) != int(slot)

    def is_free(self, slot: int) -> bool:
        return self.covering_note(slot) is None

    def cell_keys(self, slot: int) -> List[Key]:
        note = self.note_at(slot)
        return list(note.keys) if note else []

    def duration_at(self, slot: int) -> int:
        note = self.note_at(slot)
        return int(round(note.duration)) if note else 0

    def keys_in_region(self, start: int, end: int) -> Set[Key]:
        result: Set[Key] = set()
        for note in self.score.notes_in_range(float(start), float(end) + EPS):
            result.update(note.keys)
        return result

    def note_count(self) -> int:
        return len(self.score.notes)

    # ------------------------------------------------------------------ #
    # 单格编辑
    # ------------------------------------------------------------------ #
    def set_cell(self, slot: int, keys: Sequence[Key], duration: Optional[int] = None) -> None:
        """写入/替换某格的音符；``keys`` 为空则等价于清空该格。"""
        slot = int(slot)
        keys = list(keys)
        existing = self.note_at(slot)
        if not keys:
            self.clear_cell(slot)
            return
        keep = duration if duration is not None else (int(round(existing.duration)) if existing else 1)
        note = ScoreNote(float(slot), float(max(1, keep)), keys)
        self.score.upsert(note)

    def set_cell_keys(self, slot: int, keys: Sequence[Key]) -> None:
        """只改按键，保留原时值。"""
        self.set_cell(slot, keys, duration=self.duration_at(slot) or 1)

    def toggle_key(self, slot: int, key: Key) -> bool:
        """切换某格中某个音的存在状态，返回操作后该格是否有音。

        空格子也能直接落第一个音（写和弦时「按住空格 → 敲多个音」依赖这个行为）；
        若该格被前一个音的延音占着，先把延音截断到这一格之前。
        """
        slot = int(slot)
        if self.is_continuation(slot):
            holder = self.covering_note(slot)
            if holder is not None:
                start = self.slot_of(holder.beat)
                self.set_duration(start, max(1, slot - start))
        note = self.note_at(slot)
        keys = list(note.keys) if note is not None else []
        if key in keys:
            keys.remove(key)
        else:
            keys.append(key)
        if not keys:
            self.clear_cell(slot)
            return False
        duration = max(1, int(round(note.duration))) if note is not None else 1
        self.set_cell(slot, keys, duration=duration)
        return True

    def clear_cell(self, slot: int) -> None:
        """删除某格上的音符（连同它的延音）。"""
        self.score.remove_at(self.beat_of(slot))

    def set_duration(self, slot: int, duration: int) -> None:
        note = self.note_at(slot)
        if note is None:
            return
        note.duration = float(max(1, int(duration)))
        self._trim_overlaps(note)

    def bump_duration(self, slot: int, delta: int) -> int:
        """延长/缩短时值，返回新的时值；延音线在界面上随之伸缩。"""
        note = self.note_at(slot)
        if note is None:
            return 0
        self.set_duration(slot, int(round(note.duration)) + int(delta))
        return int(round(note.duration))

    def _trim_overlaps(self, note: ScoreNote) -> None:
        """延音不得吞掉后面已有的音符：自动截断到下一个音之前。"""
        limit: Optional[float] = None
        for other in self.score.notes:
            if other is note:
                continue
            if other.beat > note.beat + EPS:
                limit = other.beat if limit is None else min(limit, other.beat)
        if limit is not None and note.end > limit + EPS:
            note.duration = max(1.0, limit - note.beat)

    # ------------------------------------------------------------------ #
    # 区域编辑
    # ------------------------------------------------------------------ #
    def delete_region(self, start: int, end: int) -> int:
        """删除 ``[start, end]`` 槽位区间内**起始**的音符，返回删除数量。"""
        lo, hi = sorted((int(start), int(end)))
        keep: List[ScoreNote] = []
        removed = 0
        for note in self.score.notes:
            slot = self.slot_of(note.beat)
            if lo <= slot <= hi:
                removed += 1
            else:
                keep.append(note)
        self.score.notes = keep
        return removed

    def copy_region(self, start: int, end: int) -> Clip:
        """复制区间内的音符，位置归一化到 0 槽。"""
        lo, hi = sorted((int(start), int(end)))
        clip = Clip()
        for note in self.score.notes:
            slot = self.slot_of(note.beat)
            if lo <= slot <= hi:
                clip.notes.append(
                    ScoreNote(float(slot - lo), float(note.duration), list(note.keys))
                )
        clip.notes.sort(key=lambda n: n.beat)
        clip.length_slots = hi - lo + 1
        return clip

    def paste(self, clip: Clip, slot: int, mode: str = "merge") -> int:
        """在指定槽位粘贴乐句，返回写入的音符数。

        mode = ``"merge"``：与目标格已有音符合并（取并集、时值取大）
        mode = ``"overwrite"``：覆盖整段区间
        """
        if clip.is_empty:
            return 0
        base = int(slot)
        if mode == "overwrite":
            self.delete_region(base, base + max(0, clip.length_slots - 1))
        written = 0
        for item in clip.notes:
            target = base + self.slot_of(item.beat)
            if mode == "merge":
                existing = self.note_at(target)
                if existing is not None:
                    merged = list(existing.keys)
                    for key in item.keys:
                        if key not in merged:
                            merged.append(key)
                    duration = max(int(round(existing.duration)), int(round(item.duration)))
                    self.set_cell(target, merged, duration=duration)
                    written += 1
                    continue
            self.set_cell(target, item.keys, duration=int(round(item.duration)))
            written += 1
        return written

    def transpose_region(self, start: int, end: int, degree_steps: int = 0, octave_steps: int = 0) -> int:
        """对区间内的音符移调/换八度，返回受影响音符数。"""
        if degree_steps == 0 and octave_steps == 0:
            return 0
        lo, hi = sorted((int(start), int(end)))
        count = 0
        for note in self.score.notes:
            slot = self.slot_of(note.beat)
            if lo <= slot <= hi:
                note.keys = [key.shifted(degree_steps, octave_steps) for key in note.keys]
                count += 1
        return count

    def transpose_all(self, degree_steps: int = 0, octave_steps: int = 0) -> int:
        count = len(self.score.notes)
        self.score.shift_all(degree_steps, octave_steps)
        return count

    def transpose_region_semitones(self, start: int, end: int, steps: int) -> int:
        """半音阶移调：对区间内的音符按半音平移，返回受影响音符数。"""
        if steps == 0:
            return 0
        lo, hi = sorted((int(start), int(end)))
        count = 0
        for note in self.score.notes:
            slot = self.slot_of(note.beat)
            if lo <= slot <= hi:
                note.keys = [key.shifted_semitones(steps) for key in note.keys]
                count += 1
        return count

    def transpose_all_semitones(self, steps: int) -> int:
        count = len(self.score.notes)
        self.score.shift_all_semitones(steps)
        return count

    def downgrade_sharps(self, start: Optional[int] = None, end: Optional[int] = None) -> int:
        """把黑键音「就近归入下方自然音」（切回自然阶模式时的降级处理）。

        返回被改动的音符数。
        """
        lo = 0 if start is None else int(start)
        hi = self.total_slots if end is None else int(end)
        changed = 0
        for note in self.score.notes:
            slot = self.slot_of(note.beat)
            if not (lo <= slot <= hi):
                continue
            new_keys: List[Key] = []
            touched = False
            for key in note.keys:
                if key.is_sharp:
                    new_keys.append(Key(key.octave, key.semitone - 1))
                    touched = True
                else:
                    new_keys.append(key)
            if touched:
                note.keys = new_keys
                changed += 1
        return changed

    def region_span(self, start: int, end: int) -> Tuple[int, int]:
        """返回区间内第一/最后一个音符的槽位（无音符时返回入参）。"""
        lo, hi = sorted((int(start), int(end)))
        slots = [self.slot_of(n.beat) for n in self.score.notes if lo <= self.slot_of(n.beat) <= hi]
        if not slots:
            return lo, hi
        return min(slots), max(slots)

    # ------------------------------------------------------------------ #
    # 便捷查询（界面提示用）
    # ------------------------------------------------------------------ #
    def out_of_range_keys(self, mode: Optional[str] = None) -> List[Key]:
        """返回整谱中超出当前模式音域的音，用于标红提示。"""
        target = mode or self.score.meta.mode
        bad: List[Key] = []
        for note in self.score.notes:
            for key in note.keys:
                if not key.is_valid(target) and key not in bad:
                    bad.append(key)
        return bad

    def describe_bar(self, bar: int) -> str:
        if self.time_signature[1] == 8:
            return "第 %d 小节（6/8 拍：%d 格）" % (bar + 1, self.slots_per_bar)
        return "第 %d 小节（%d 格）" % (bar + 1, self.slots_per_bar)
