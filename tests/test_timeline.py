# -*- coding: utf-8 -*-
"""阶段 1 测试：网格槽位逻辑与撤销栈。"""

from __future__ import annotations

from core.model import Score, ScoreNote
from core.notes import Key
from core.timeline import Timeline
from core.undo import UndoStack, clone_notes


def _timeline() -> Timeline:
    return Timeline(Score.empty(bpm=120, time_signature=(4, 4), subdivision=4))


def test_slot_and_bar_math() -> None:
    tl = _timeline()
    assert tl.slots_per_beat == 4
    assert tl.slots_per_bar == 16
    assert tl.bar_of_slot(0) == 0
    assert tl.bar_of_slot(15) == 0
    assert tl.bar_of_slot(16) == 1
    assert tl.slot_of_bar(2) == 32
    assert tl.snap(3.4) == 3.0
    assert tl.beat_of(7) == 7.0
    assert tl.bar_count() == 1  # 空谱也要有一小节
    assert abs(tl.time_ms(4) - 500.0) < 1e-9


def test_set_clear_and_toggle() -> None:
    tl = _timeline()
    tl.set_cell(0, [Key.from_degree(0, 1)])
    assert tl.cell_keys(0) == [Key.from_degree(0, 1)]
    assert not tl.is_free(0)

    tl.toggle_key(0, Key.from_degree(0, 5))
    assert set(tl.cell_keys(0)) == {Key.from_degree(0, 1), Key.from_degree(0, 5)}
    tl.toggle_key(0, Key.from_degree(0, 1))
    assert tl.cell_keys(0) == [Key.from_degree(0, 5)]
    tl.toggle_key(0, Key.from_degree(0, 5))
    assert tl.note_at(0) is None, "删掉最后一个音时应整格清空"
    assert tl.is_free(0)

    tl.set_cell(4, [Key.from_degree(-1, 2)])
    tl.clear_cell(4)
    assert tl.note_count() == 0


def test_duration_and_occupancy() -> None:
    tl = _timeline()
    tl.set_cell(0, [Key.from_degree(0, 1)], duration=3)
    assert tl.is_continuation(1)
    assert tl.is_continuation(2)
    assert not tl.is_continuation(3)
    assert tl.is_free(3)
    assert tl.duration_at(0) == 3

    assert tl.bump_duration(0, 1) == 4
    assert tl.bump_duration(0, -2) == 2
    assert tl.bump_duration(99, 1) == 0  # 空格子不报错


def test_tie_never_swallows_next_note() -> None:
    tl = _timeline()
    tl.set_cell(0, [Key.from_degree(0, 1)], duration=2)
    tl.set_cell(4, [Key.from_degree(0, 2)], duration=1)
    tl.set_duration(0, 10)  # 想延长到 10 格
    assert tl.duration_at(0) == 4, "延音必须被后面的音符截断"
    assert tl.note_at(4) is not None


def test_region_delete_copy_paste_merge() -> None:
    tl = _timeline()
    tl.set_cell(0, [Key.from_degree(0, 1)])
    tl.set_cell(2, [Key.from_degree(0, 3), Key.from_degree(1, 5)], duration=2)

    clip = tl.copy_region(0, 3)
    assert len(clip.notes) == 2
    assert clip.notes[0].beat == 0.0
    assert clip.notes[1].beat == 2.0
    assert clip.length_slots == 4

    # 粘贴到 16 槽（第二小节开头）
    tl.paste(clip, 16)
    assert tl.note_at(16) is not None
    assert tl.note_at(18).duration == 2
    assert set(tl.cell_keys(18)) == {Key.from_degree(0, 3), Key.from_degree(1, 5)}

    # 合并模式：同槽位取并集，时值取大
    tl.paste(clip, 16)
    assert set(tl.cell_keys(16)) == {Key.from_degree(0, 1)}
    single = tl.copy_region(0, 0)
    tl.paste(single, 18)
    assert set(tl.cell_keys(18)) == {Key.from_degree(0, 3), Key.from_degree(1, 5), Key.from_degree(0, 1)}
    assert tl.duration_at(18) == 2

    # 覆盖模式
    tl.paste(single, 18, mode="overwrite")
    assert set(tl.cell_keys(18)) == {Key.from_degree(0, 1)}
    assert tl.duration_at(18) == 1

    removed = tl.delete_region(16, 19)
    assert removed >= 2
    assert tl.note_at(16) is None


def test_transpose_region_and_all() -> None:
    tl = _timeline()
    tl.set_cell(0, [Key.from_degree(0, 1)])
    tl.set_cell(20, [Key.from_degree(0, 2)])  # 第二小节，不应被区间移调影响

    tl.transpose_region(0, 15, degree_steps=1)
    assert tl.cell_keys(0) == [Key.from_degree(0, 2)]
    assert tl.cell_keys(20) == [Key.from_degree(0, 2)], "区间外的音符不应改变"

    tl.transpose_all(octave_steps=1)
    assert tl.cell_keys(0) == [Key.from_degree(1, 2)]

    tl.transpose_all(octave_steps=1)
    assert tl.out_of_range_keys() == [Key.from_degree(2, 2)], "升到越界后应被识别出来"


def test_bar_count_grows_with_notes() -> None:
    tl = _timeline()
    tl.set_cell(33, [Key.from_degree(0, 1)])
    assert tl.bar_count() == 3
    assert tl.bar_of_beat(33) == 2


def test_undo_redo_and_dirty_flag() -> None:
    score = Score.empty()
    stack = UndoStack(limit=10)
    tl = Timeline(score)

    before = clone_notes(score.notes)
    tl.set_cell(0, [Key.from_degree(0, 1)])
    assert stack.push("插入音符", before, score.notes, 0, 1)
    assert stack.can_undo and not stack.can_redo
    assert stack.is_dirty

    assert stack.undo(score) == 0
    assert score.notes == []
    assert stack.can_redo
    assert not stack.is_dirty

    assert stack.redo(score) == 1
    assert len(score.notes) == 1
    assert stack.is_dirty

    stack.mark_saved()
    assert not stack.is_dirty

    # 无变化时不入栈
    snapshot = clone_notes(score.notes)
    assert not stack.push("空操作", snapshot, clone_notes(score.notes), 1, 1)

    # 新分支会丢弃 redo
    before2 = clone_notes(score.notes)
    tl.set_cell(4, [Key.from_degree(0, 2)])
    stack.push("插入音符", before2, score.notes, 1, 5)
    stack.undo(score)
    before3 = clone_notes(score.notes)
    tl.set_cell(8, [Key.from_degree(0, 3)])
    stack.push("插入音符", before3, score.notes, 1, 9)
    assert not stack.can_redo


def test_undo_merge_key_groups_a_gesture() -> None:
    """回归：一次「连续手势」（按住空格写和弦）的多笔编辑只占一步撤销。"""
    score = Score.empty()
    stack = UndoStack(limit=10)
    tl = Timeline(score)

    for key in (Key.from_degree(0, 1), Key.from_degree(0, 4), Key.from_degree(0, 5)):
        before = clone_notes(score.notes)
        tl.toggle_key(0, key)
        stack.push("和弦", before, score.notes, 0, 0, merge_key="chord-1")
    assert len(tl.cell_keys(0)) == 3

    assert stack.undo(score) == 0
    assert tl.cell_keys(0) == [], "整段和弦手势应当一步撤销"
    assert not stack.can_undo

    # 不同 merge_key 之间不会互相合并
    for merge in ("chord-2", "chord-3"):
        before = clone_notes(score.notes)
        tl.toggle_key(0, Key.from_degree(0, 1))
        stack.push("和弦", before, score.notes, 0, 0, merge_key=merge)
    stack.undo(score)
    assert len(tl.cell_keys(0)) == 1, "两个不同手势应当各占一步"
    stack.undo(score)
    assert tl.cell_keys(0) == []

    # 空 merge_key（普通编辑）永远不合并：两笔不同的编辑 = 两步
    for key in (Key.from_degree(0, 2), Key.from_degree(0, 5)):
        before = clone_notes(score.notes)
        tl.set_cell(4, [key])
        stack.push("插入", before, score.notes, 0, 0)
    stack.undo(score)
    assert tl.cell_keys(4) == [Key.from_degree(0, 2)], "普通编辑应当各占一步"
    stack.undo(score)
    assert tl.cell_keys(4) == []


def test_toggle_key_works_on_empty_cell() -> None:
    """回归：空格子里 toggle_key 也要能落第一个音（按住空格写和弦依赖它）。"""
    tl = _timeline()
    assert tl.toggle_key(4, Key.from_degree(0, 1)) is True
    assert tl.cell_keys(4) == [Key.from_degree(0, 1)]
    assert tl.toggle_key(4, Key.from_degree(0, 5)) is True
    assert set(tl.cell_keys(4)) == {Key.from_degree(0, 1), Key.from_degree(0, 5)}
    tl.toggle_key(4, Key.from_degree(0, 1))
    assert tl.toggle_key(4, Key.from_degree(0, 5)) is False
    assert tl.cell_keys(4) == []

    # 被延音占用的格子：先截断延音再落音
    tl.set_cell(8, [Key.from_degree(0, 1)], duration=4)   # 占 8..11
    assert tl.is_continuation(10)
    assert tl.toggle_key(10, Key.from_degree(0, 3)) is True
    assert tl.cell_keys(10) == [Key.from_degree(0, 3)]
    assert tl.duration_at(8) == 2, "延音应被截断到第 10 格之前"


def test_undo_limit() -> None:
    score = Score.empty()
    stack = UndoStack(limit=3)
    tl = Timeline(score)
    for i in range(6):
        before = clone_notes(score.notes)
        tl.set_cell(i, [Key.from_degree(0, 1)])
        stack.push("插入", before, score.notes, i, i + 1)
    count = 0
    while stack.can_undo:
        stack.undo(score)
        count += 1
        assert count <= 3, "撤销步数不能超过 limit"
    assert count == 3


def test_star_score_shape() -> None:
    """一个乐句的结构换算：7 个音（末音为二分音符）→ 32 格 / 2 小节 / 4 秒。"""
    score = Score.empty(title="小星星", bpm=120, time_signature=(4, 4), subdivision=4)
    melody = [1, 1, 5, 5, 6, 6, 5]
    slot = 0
    for index, degree in enumerate(melody):
        duration = 8 if index == len(melody) - 1 else 4
        score.notes.append(ScoreNote(float(slot), float(duration), [Key.from_degree(0, degree)]))
        slot += duration
    tl = Timeline(score)
    assert tl.total_slots == 32
    assert tl.bar_count() == 2
    assert abs(score.total_ms() - 4000.0) < 1e-6  # 32 格 × 125ms
    assert not [i for i in score.validate() if i.level == "error"]
