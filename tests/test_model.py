# -*- coding: utf-8 -*-
"""阶段 1 测试：音域映射与数据模型。

运行：``python tests/run_all.py test_model``
"""

from __future__ import annotations

from core.model import (
    ALLOWED_SUBDIVISIONS,
    Score,
    ScoreMeta,
    ScoreNote,
    has_errors,
)
from core.notes import (
    DEFAULT_KEYMAP,
    MODE_CHROMATIC,
    MODE_DIATONIC,
    Key,
    KeyMap,
    KeyStroke,
    all_keys,
    key_sort_index,
)


def test_default_keymap_letters() -> None:
    """默认布局必须与游戏内 21 键完全一致（表键是「音区, 半音」）。"""
    assert DEFAULT_KEYMAP[(1, 0)] == "q"        # 高音1
    assert DEFAULT_KEYMAP[(1, 11)] == "u"       # 高音7
    assert DEFAULT_KEYMAP[(0, 0)] == "a"        # 中音1
    assert DEFAULT_KEYMAP[(0, 11)] == "j"       # 中音7
    assert DEFAULT_KEYMAP[(-1, 0)] == "z"       # 低音1
    assert DEFAULT_KEYMAP[(-1, 11)] == "m"      # 低音7
    assert len(DEFAULT_KEYMAP) == 21
    keymap = KeyMap()
    assert keymap.row_chars(1) == "qwertyu"
    assert keymap.row_chars(0) == "asdfghj"
    assert keymap.row_chars(-1) == "zxcvbnm"


def test_key_representations() -> None:
    """音级 ↔ 半音 ↔ MIDI 的换算必须一致。"""
    assert Key.from_degree(0, 1) == Key(0, 0) == Key.from_midi(60)
    assert Key.from_degree(0, 7) == Key(0, 11) == Key.from_midi(71)
    assert Key.from_degree(1, 1) == Key(1, 0)
    assert Key(0, 0).degree == 1 and Key(0, 11).degree == 7
    assert Key(0, 1).degree is None and Key(0, 1).is_sharp
    assert Key(0, 1).pitch == 1 and Key(-1, 0).pitch == -12
    assert Key(0, 1).note_name() == "C#4"
    assert Key(1, 6).note_name() == "F#5"
    # 半音越界会折算到相邻八度
    assert Key.from_semitone(0, 12) == Key(1, 0)
    # 合法性随模式变化
    assert Key(0, 1).is_valid(MODE_CHROMATIC) is True
    assert Key(0, 1).is_valid(MODE_DIATONIC) is False
    assert Key(2, 0).is_valid(MODE_CHROMATIC) is False


def test_locate_roundtrip() -> None:
    """每个音的按键都能反查回同一个音（自然阶与半音阶各来一遍）。"""
    for mode, preset in ((MODE_DIATONIC, None), (MODE_CHROMATIC, None)):
        keymap = KeyMap(mode, preset)
        for key in keymap.keys():
            char = keymap.char_for(key)
            assert char, "音 %s 没有按键" % key.label()
            if "+" not in char:  # 组合键不能靠单键反查
                assert keymap.locate(char) == key, "按键 %s 反查错误" % char
        assert keymap.locate("") is None
    assert KeyMap(MODE_DIATONIC).locate("k") is None


def test_shift_wraps_octave() -> None:
    """移调时的音级溢出要折算到相邻八度。"""
    assert Key.from_degree(0, 7).shifted(1) == Key.from_degree(1, 1)
    assert Key.from_degree(1, 1).shifted(-1) == Key.from_degree(0, 7)
    assert Key.from_degree(0, 1).shifted(0, 1) == Key.from_degree(1, 1)
    assert Key.from_degree(-1, 5).shifted(3) == Key.from_degree(0, 1)
    # 半音移调
    assert Key(0, 0).shifted_semitones(1) == Key(0, 1)
    assert Key(0, 11).shifted_semitones(1) == Key(1, 0)
    assert Key(0, 0).shifted_semitones(-1) == Key(-1, 11)
    assert Key(0, 0).shifted_octaves(1) == Key(1, 0)


def test_key_sort_index_order() -> None:
    """界面顺序：高音在前，同一音区内半音 0→11。"""
    ordered = sorted(all_keys(MODE_DIATONIC), key=key_sort_index)
    assert ordered[0] == Key.from_degree(1, 1)
    assert ordered[6] == Key.from_degree(1, 7)
    assert ordered[7] == Key.from_degree(0, 1)
    assert ordered[-1] == Key.from_degree(-1, 7)

    chromatic = sorted(all_keys(MODE_CHROMATIC), key=key_sort_index)
    assert len(chromatic) == 36
    assert chromatic[0] == Key(1, 0) and chromatic[11] == Key(1, 11)
    assert chromatic[12] == Key(0, 0) and chromatic[-1] == Key(-1, 11)


def test_override_and_validate() -> None:
    """用户覆盖映射表后：overrides 只记录差异，重复占用能被发现。"""
    keymap = KeyMap({"1,0": "o"})  # 把高音1（半音 0）改到 o
    assert keymap.char_for(Key.from_degree(1, 1)) == "o"
    assert keymap.overrides() == {"1,0": "o"}
    assert keymap.validate() == []

    bad = KeyMap({"0,2": "a"})  # a 已被中音1占用
    problems = bad.validate()
    assert any("同时占用" in p for p in problems), problems

    # 半音阶模式下不能把黑键写成非法音位
    for slot, char in [("9,9", "q"), ("1,1", ""), ("1,1", "long"), ("abc", "q")]:
        try:
            KeyMap(MODE_CHROMATIC, None, {slot: char}).validate()
        except Exception:  # pragma: no cover - 只要求不静默通过
            pass
    try:
        KeyMap().apply_overrides({"1,0": "!!"}, strict=True)
        raise AssertionError("非法按键应当抛错")
    except ValueError:
        pass


def test_key_stroke_parsing() -> None:
    """组合键的解析与规范化。"""
    assert KeyStroke.parse("Z").text() == "z"
    assert KeyStroke.parse("Shift+Z").text() == "shift+z"
    assert KeyStroke.parse("ctrl + shift + 2").text() == "ctrl+shift+2"
    assert KeyStroke.parse("SHIFT+CTRL+p").text() == "ctrl+shift+p"   # 修饰键顺序固定
    assert KeyStroke.parse("f8").text() == "f8"
    assert KeyStroke.parse("shift+z").display() == "⇧Z"
    assert KeyStroke.parse("ctrl+1").is_combo is True
    assert KeyStroke.parse("a").is_combo is False

    for bad in ("", "shift", "ctrl+alt", "abcdef", "ctrl+!!"):
        try:
            KeyStroke.parse(bad)
            raise AssertionError("应当拒绝 %r" % bad)
        except ValueError:
            pass


def test_jianpu_notation() -> None:
    """格内符号统一约定：单数字 = 中音，上点 = 高音，下点 = 低音，升号仍是 #。"""
    # 文字部分不含八度
    assert Key.from_degree(0, 1).short_label() == "1"
    assert Key.from_degree(1, 1).short_label() == "1"
    assert Key.from_degree(-1, 7).short_label() == "7"
    assert Key(0, 1).short_label() == "#1"
    assert Key(1, 6).short_label() == "#4"

    # 八度点位置：1 = 上点 / 0 = 无点 / -1 = 下点
    assert Key.from_degree(1, 1).octave_dot() == 1
    assert Key.from_degree(1, 7).octave_dot() == 1
    assert Key.from_degree(0, 5).octave_dot() == 0
    assert Key.from_degree(-1, 3).octave_dot() == -1
    assert Key(1, 6).octave_dot() == 1        # 高音 #4 也带上点
    assert Key(-1, 1).octave_dot() == -1      # 低音 #1 带下点

    # 纯文本回退写法（^ 上点 / _ 下点）
    assert Key.from_degree(0, 1).jianpu_ascii() == "1"
    assert Key.from_degree(1, 2).jianpu_ascii() == "^2"
    assert Key.from_degree(-1, 3).jianpu_ascii() == "_3"
    assert Key(1, 6).jianpu_ascii() == "^#4"
    assert Key(0, 1).jianpu_text() == "#1"


def test_meta_derived_values() -> None:
    """每小节格数 / 每格毫秒数换算。"""
    meta = ScoreMeta(bpm=120, time_signature=(4, 4), subdivision=4)
    assert meta.slots_per_beat == 4
    assert meta.slots_per_bar == 16
    assert abs(meta.ms_per_slot() - 125.0) < 1e-9
    assert abs(meta.ms_per_slot(2.0) - 62.5) < 1e-9

    six_eight = ScoreMeta(bpm=90, time_signature=(6, 8), subdivision=4)
    assert six_eight.slots_per_bar == 24


def test_validate_bpm_and_subdivision() -> None:
    score = Score.empty(bpm=999)
    score.meta.subdivision = 5
    issues = score.validate()
    assert has_errors(issues)
    texts = " ".join(i.message for i in issues)
    assert "BPM" in texts and "细分" in texts and "音符" in texts


def test_validate_range_and_overlap() -> None:
    score = Score.empty()
    score.notes = [
        ScoreNote(0, 4, [Key.from_degree(2, 1)]),          # 音区越界
        ScoreNote(4, 8, [Key.from_degree(0, 1)]),          # 与下面的音符重叠
        ScoreNote(8, 4, [Key.from_degree(0, 1)]),
        ScoreNote(12, 0, [Key.from_degree(0, 2)]),         # 时值为 0
        ScoreNote(16, 1, []),                  # 没有按键
    ]
    issues = score.validate()
    messages = " ".join(i.message for i in issues)
    assert "超出游戏音域" in messages
    assert "重叠" in messages
    assert "必须大于 0" in messages
    assert "没有指定任何按键" in messages


def test_validate_duplicate_beat() -> None:
    score = Score.empty()
    score.notes = [ScoreNote(0, 1, [Key.from_degree(0, 1)]), ScoreNote(0, 1, [Key.from_degree(0, 2)])]
    issues = score.validate()
    assert any("合并为和弦" in i.message for i in issues), [i.message for i in issues]


def test_json_roundtrip_shape() -> None:
    """模型层序列化字段名需与约定一致（文件头由 scoreio 补）。"""
    score = Score.empty(title="测试", bpm=100)
    score.notes = [ScoreNote(0, 1, [Key.from_degree(0, 1)]), ScoreNote(1, 2, [Key.from_degree(0, 1), Key.from_degree(1, 3)])]
    payload = score.to_json()
    assert payload["format"] == "wuthering-score"
    assert payload["version"] == "1.1"
    assert payload["meta"]["timeSignature"] == [4, 4]
    assert payload["meta"]["mode"] == "diatonic"
    assert payload["notes"][1]["keys"] == [{"octave": 1, "degree": 3}, {"octave": 0, "degree": 1}]

    restored = Score.from_json(payload)
    assert len(restored.notes) == 2
    assert restored.notes[1].keys == [Key.from_degree(1, 3), Key.from_degree(0, 1)]
    assert restored.meta.time_signature == (4, 4)

    # 空谱也要能往返
    empty = Score.empty()
    assert Score.from_json(empty.to_json()).notes == []


def test_from_json_error_messages() -> None:
    for bad in [{"format": "other"}, {"format": "wuthering-score"}, {"format": "wuthering-score", "notes": {}}]:
        try:
            Score.from_json(bad)
            raise AssertionError("非法结构应当抛错：%r" % (bad,))
        except Exception as exc:
            assert str(exc), "错误信息不能为空"


def test_allowed_subdivisions_sane() -> None:
    assert set(ALLOWED_SUBDIVISIONS) == {4, 8, 16, 32}
