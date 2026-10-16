# -*- coding: utf-8 -*-
"""半音阶模式验收测试（对应 PROMPT.md 第 9.8 节的 9 项清单）。

1. 预设 36 键与文档表格逐项一致
2. 组合键规范化 / 非法组合被拒
3. 映射冲突检测
4. 组合键展开与修饰键引用计数（含急停后无残留）
5. Shift 大小写（主键必须是小写键码）
6. 模式切换：自然→半音无损；半音→自然三种策略
7. JSON 1.0/1.1 兼容、三种键写法优先级、越界报错、降级字段
8. 文本谱半音写法往返
9. 半音阶 + 和弦 + 组合键的计时精度
"""

from __future__ import annotations

import os

from core.backends import DummyBackend
from core.clock import JitterTracker
from core.engine import (
    EngineCallbacks,
    EngineState,
    PlayEngine,
    PlayOptions,
    build_plan,
    check_mapping,
)
from core.model import (
    SCORE_VERSION,
    Score,
    ScoreNote,
    ScoreError,
)
from core.notes import (
    DEGREE_TO_SEMITONE,
    MODE_CHROMATIC,
    MODE_DIATONIC,
    PRESET_CHROMATIC,
    PRESET_FF14,
    Key,
    KeyMap,
    KeyStroke,
    chromatic_keys,
    default_preset,
    diatonic_keys,
    preset_mapping,
)
from core.timeline import Timeline
from scoreio import json_io, text_io

# --------------------------------------------------------------------------- #
# 1) 预设表与文档一致
# --------------------------------------------------------------------------- #

#: 预设 A：白键沿用自然阶，黑键全单键（文档 2.3 / 9.3）
CHROMATIC_36_EXPECTED = {
    1: {"q": 0, "2": 1, "w": 2, "3": 3, "e": 4, "r": 5, "5": 6, "t": 7, "6": 8, "y": 9, "7": 10, "u": 11},
    0: {"a": 0, "1": 1, "s": 2, "4": 3, "d": 4, "f": 5, "8": 6, "g": 7, "9": 8, "h": 9, "0": 10, "j": 11},
    -1: {"z": 0, "i": 1, "x": 2, "o": 3, "c": 4, "v": 5, "p": 6, "b": 7, "k": 8, "n": 9, "l": 10, "m": 11},
}

#: 预设 B：FF14 风格，一排 12 键钢琴式交错 × 三档修饰键
FF14_BASE = "zsxdcvgbhnjm"


def test_preset_chromatic_36_matches_doc() -> None:
    keymap = KeyMap(MODE_CHROMATIC, PRESET_CHROMATIC)
    assert len(keymap.keys()) == 36
    for octave, expected in CHROMATIC_36_EXPECTED.items():
        for char, semitone in expected.items():
            assert keymap.char_for(Key(octave, semitone)) == char, (octave, semitone, char)
    # 21 个自然音键位必须与自然阶模式完全一致（切模式不改变手感）
    diatonic = KeyMap(MODE_DIATONIC)
    for key in diatonic_keys():
        assert keymap.char_for(key) == diatonic.char_for(key), key.label()
    assert keymap.validate() == []


def test_preset_ff14_matches_doc() -> None:
    keymap = KeyMap(MODE_CHROMATIC, PRESET_FF14)
    assert len(keymap.keys()) == 36
    for octave, modifier in ((-1, "ctrl"), (0, ""), (1, "shift")):
        for semitone, char in enumerate(FF14_BASE):
            expected = "%s+%s" % (modifier, char) if modifier else char
            assert keymap.char_for(Key(octave, semitone)) == expected, (octave, semitone)
    assert keymap.validate() == []
    assert keymap.row_chars(0) == FF14_BASE


# --------------------------------------------------------------------------- #
# 2) 组合键规范化
# --------------------------------------------------------------------------- #

def test_stroke_normalization() -> None:
    assert KeyStroke.parse("Ctrl+Shift+z").text() == "ctrl+shift+z"
    assert KeyStroke.parse("shift+ctrl+Z").text() == "ctrl+shift+z"
    assert KeyStroke.parse(" SHIFT + CTRL + z ").text() == "ctrl+shift+z"
    assert KeyStroke.parse("Alt+1").text() == "alt+1"
    assert KeyStroke.parse("f7").text() == "f7"
    assert KeyStroke.parse("ctrl+shift+z").display() == "C+⇧Z"

    for bad in ("", "ctrl", "shift+alt", "ctrl+shift", "abcdef", "ctrl+##", "z+1"):
        try:
            KeyStroke.parse(bad)
            raise AssertionError("应当拒绝 %r" % bad)
        except ValueError:
            pass


# --------------------------------------------------------------------------- #
# 3) 冲突检测
# --------------------------------------------------------------------------- #

def test_conflict_detection() -> None:
    # 同一按键绑两个音 → 报冲突
    keymap = KeyMap(MODE_CHROMATIC, PRESET_CHROMATIC)
    keymap.apply_overrides({"0,1": "a"}, strict=False)   # a 已被中音1占用
    problems = keymap.validate()
    assert any("同时占用" in p for p in problems), problems

    # 组合键与它的前缀键不算冲突（1 与 ctrl+1 是两回事）
    ok = KeyMap(MODE_CHROMATIC, PRESET_CHROMATIC)
    ok.apply_overrides({"1,0": "ctrl+1"})
    assert ok.validate() == []

    # 非法音位被拒绝
    try:
        KeyMap(MODE_CHROMATIC, PRESET_CHROMATIC).apply_overrides({"0,12": "q"}, strict=True)
        raise AssertionError("半音 12 应当被拒绝")
    except ValueError:
        pass
    # 自然阶模式下黑键音位不能绑定
    try:
        KeyMap(MODE_DIATONIC).apply_overrides({"0,1": "q"}, strict=True)
        raise AssertionError("自然阶模式不应接受黑键音位")
    except ValueError:
        pass


# --------------------------------------------------------------------------- #
# 4) 组合键展开与修饰键引用计数
# --------------------------------------------------------------------------- #

def _single_note_score(key: Key, mode: str = MODE_CHROMATIC) -> Score:
    score = Score.empty(bpm=120, subdivision=4, mode=mode)
    score.notes = [ScoreNote(0.0, 4.0, [key])]
    return score


def _run(plan, backend: DummyBackend, **option_kwargs) -> PlayEngine:
    engine = PlayEngine(backend, EngineCallbacks())
    engine.start(plan, PlayOptions(countdown_s=0, **option_kwargs), countdown=0)
    assert engine.wait(20.0), "演奏没有在预期时间内结束"
    return engine


def test_combo_expansion_order() -> None:
    """单音组合键 → 4 个事件，顺序：修饰键按下 → 主键按下 → 主键抬起 → 修饰键抬起。"""
    keymap = KeyMap(MODE_CHROMATIC, PRESET_FF14)
    plan = build_plan(_single_note_score(Key(-1, 0)), keymap, PlayOptions())   # 低音1 = ctrl+z
    assert plan.combo_count == 1
    assert plan.event_count == 4, [(e.char, e.is_down, e.time_ms) for e in plan.events]
    sequence = [(event.char, event.is_down) for event in plan.events]
    assert sequence == [("ctrl", True), ("z", True), ("z", False), ("ctrl", False)], sequence
    # 第一个音从 0ms 开始，修饰键提前量被夹到 0，因此这里是「不晚于」
    assert plan.events[0].time_ms <= plan.events[1].time_ms
    assert plan.events[3].time_ms > plan.events[2].time_ms     # 修饰键最后抬
    assert plan.modifier_count == 2

    # 第二个音（不在 0ms）能看出提前量
    score = Score.empty(bpm=120, subdivision=4, mode=MODE_CHROMATIC)
    score.notes = [ScoreNote(4.0, 4.0, [Key(-1, 0)])]
    plan2 = build_plan(score, keymap, PlayOptions(chord_stagger_ms=2.0))
    assert plan2.events[0].char == "ctrl" and plan2.events[1].char == "z"
    assert plan2.events[0].time_ms < plan2.events[1].time_ms


def test_shared_modifier_pressed_once() -> None:
    """和弦里两个音共用一个 Shift → Shift 只按一次，最后才抬。"""
    keymap = KeyMap(MODE_CHROMATIC, PRESET_FF14)
    score = Score.empty(bpm=120, subdivision=4, mode=MODE_CHROMATIC)
    score.notes = [ScoreNote(0.0, 4.0, [Key(1, 0), Key(1, 2)])]   # 高音1 + 高音2，都是 shift+*
    plan = build_plan(score, keymap, PlayOptions())

    shift_events = [event for event in plan.events if event.char == "shift"]
    assert len(shift_events) == 2, [(e.char, e.is_down, e.time_ms) for e in plan.events]
    assert shift_events[0].is_down and not shift_events[1].is_down
    # Shift 必须覆盖住两个主键的整个按下区间
    main_spans = [(event.time_ms, event.is_down) for event in plan.events if event.char in ("z", "x")]
    assert shift_events[0].time_ms <= min(t for t, is_down in main_spans if is_down)
    assert shift_events[1].time_ms >= max(t for t, is_down in main_spans if not is_down)

    backend = DummyBackend()
    _run(plan, backend)
    downs = [char for _t, char, is_down in backend.events if is_down]
    assert downs.count("shift") == 1, downs
    assert backend.pressed == []


def test_adjacent_notes_share_modifier_interval() -> None:
    """密集的高音音符（同一 Shift 档）应合并成一段修饰键按住区间，避免反复按放。"""
    keymap = KeyMap(MODE_CHROMATIC, PRESET_FF14)
    score = Score.empty(bpm=120, subdivision=32, mode=MODE_CHROMATIC)
    score.notes = [
        ScoreNote(0.0, 1.0, [Key(1, 0)]),
        ScoreNote(1.0, 1.0, [Key(1, 2)]),
        ScoreNote(2.0, 1.0, [Key(1, 4)]),
    ]
    plan = build_plan(score, keymap, PlayOptions(key_hold_ms=10, min_key_interval_ms=0))
    shift_events = [event for event in plan.events if event.char == "shift"]
    assert len(shift_events) == 2, [(e.char, e.is_down, e.time_ms) for e in plan.events]
    assert shift_events[0].is_down and not shift_events[1].is_down


def test_emergency_stop_releases_modifiers() -> None:
    """组合键长按期间 F10：主键与修饰键都必须被释放。"""
    keymap = KeyMap(MODE_CHROMATIC, PRESET_FF14)
    plan = build_plan(_single_note_score(Key(-1, 0)), keymap, PlayOptions(key_hold_ms=1000))
    backend = DummyBackend()
    engine = PlayEngine(backend, EngineCallbacks())
    engine.start(plan, PlayOptions(countdown_s=0, key_hold_ms=1000), countdown=0)

    import time

    time.sleep(0.15)
    assert set(backend.pressed) == {"ctrl", "z"}, backend.pressed
    engine.emergency_stop()
    assert backend.pressed == [], "急停后不允许残留任何按键（含修饰键）"
    engine.wait(5.0)
    assert engine.state == EngineState.STOPPED


def test_release_order_main_before_modifier() -> None:
    """释放顺序：先主键后修饰键。"""
    backend = DummyBackend()
    backend.key_down("ctrl")
    backend.key_down("z")
    backend.release_all()
    assert [char for _t, char, _d in backend.events][-2:] == ["z", "ctrl"]


# --------------------------------------------------------------------------- #
# 5) Shift 大小写
# --------------------------------------------------------------------------- #

def test_main_key_is_lowercase() -> None:
    keymap = KeyMap(MODE_CHROMATIC, PRESET_FF14)
    score = Score.empty(bpm=120, subdivision=4, mode=MODE_CHROMATIC)
    score.notes = [ScoreNote(0.0, 1.0, [Key(1, 0), Key(-1, 5)])]
    plan = build_plan(score, keymap, PlayOptions())
    for event in plan.events:
        if not event.is_modifier:
            assert event.char == event.char.lower(), event.char
            assert event.char not in ("ctrl", "alt", "shift")


# --------------------------------------------------------------------------- #
# 6) 模式切换
# --------------------------------------------------------------------------- #

def test_diatonic_to_chromatic_is_lossless() -> None:
    """自然阶 → 半音阶：所有音的 pitch 不变（degree → semitone 一一对应）。"""
    score = Score.empty(mode=MODE_DIATONIC)
    score.notes = [ScoreNote(float(i), 1.0, [Key.from_degree(0, i % 7 + 1)]) for i in range(7)]
    before = [[key.pitch for key in note.keys] for note in score.notes]

    score.meta.mode = MODE_CHROMATIC
    after = [[key.pitch for key in note.keys] for note in score.notes]
    assert before == after
    keymap = KeyMap(MODE_CHROMATIC, default_preset(MODE_CHROMATIC))
    assert check_mapping(score, keymap) == []
    # degree 也仍然可读
    for note, pitches in zip(score.notes, before):
        for key, pitch in zip(note.keys, pitches):
            assert key.pitch == pitch
            assert key.is_sharp is (key.semitone not in DEGREE_TO_SEMITONE.values())


def test_chromatic_to_diatonic_nearest() -> None:
    """半音阶 → 自然阶（就近归入）：黑键降到下方自然音。"""
    score = Score.empty(mode=MODE_CHROMATIC)
    score.notes = [
        ScoreNote(0.0, 1.0, [Key(0, 1)]),      # 中音#1 → 中音1
        ScoreNote(1.0, 1.0, [Key(0, 3)]),      # 中音#2 → 中音2
        ScoreNote(2.0, 1.0, [Key(1, 6)]),      # 高音#4 → 高音4
        ScoreNote(3.0, 1.0, [Key(0, 4)]),      # 自然音不变
    ]
    timeline = Timeline(score)
    assert len(score.sharp_keys()) == 3
    changed = timeline.downgrade_sharps()
    assert changed == 3
    assert score.sharp_keys() == []
    assert [note.keys[0] for note in score.notes] == [
        Key.from_degree(0, 1),
        Key.from_degree(0, 2),
        Key.from_degree(1, 4),
        Key.from_degree(0, 3),
    ]


def test_chromatic_to_diatonic_keep_disabled_flags_error() -> None:
    """半音阶 → 自然阶（保留黑键）：校验必须报错，演奏前会被拦住。"""
    score = Score.empty(mode=MODE_DIATONIC)
    score.notes = [ScoreNote(0.0, 1.0, [Key(0, 1)])]
    issues = score.validate(KeyMap(MODE_DIATONIC))
    errors = [item for item in issues if item.level == "error"]
    assert errors and "半音阶模式" in errors[0].message, [str(i) for i in issues]

    # 切回半音阶模式后就没有问题了
    score.meta.mode = MODE_CHROMATIC
    issues = score.validate(KeyMap(MODE_CHROMATIC, PRESET_CHROMATIC))
    assert not [item for item in issues if item.level == "error"], [str(i) for i in issues]


def test_diatonic_mode_rejects_sharp_keys() -> None:
    score = Score.empty(mode=MODE_DIATONIC)
    score.notes = [ScoreNote(0.0, 1.0, [Key(0, 6)])]   # #4 在黑键范围
    issues = score.validate(KeyMap(MODE_DIATONIC))
    assert any("超出游戏音域" in item.message or "半音" in item.message for item in issues), [
        str(i) for i in issues
    ]


# --------------------------------------------------------------------------- #
# 7) JSON 兼容
# --------------------------------------------------------------------------- #

def test_json_v10_is_diatonic() -> None:
    payload = {
        "format": "wuthering-score",
        "version": "1.0",
        "meta": {"title": "老曲谱", "bpm": 120, "timeSignature": [4, 4], "subdivision": 4},
        "notes": [{"beat": 0, "duration": 1, "keys": [{"octave": 0, "degree": 1}]}],
    }
    score = json_io.loads_score(json_io.json.dumps(payload))
    assert score.meta.mode == MODE_DIATONIC
    assert score.notes[0].keys == [Key.from_degree(0, 1)]


def test_json_v11_key_priority() -> None:
    """三种写法优先级：midi > semitone > degree。"""
    assert Key.from_json({"octave": 0, "degree": 1}) == Key(0, 0)
    assert Key.from_json({"octave": 0, "semitone": 1}) == Key(0, 1)
    assert Key.from_json({"octave": 0, "degree": 1, "semitone": 1}) == Key(0, 1)
    assert Key.from_json({"octave": 0, "semitone": 1, "midi": 62}) == Key(0, 2)
    assert Key.from_json({"midi": 60}) == Key(0, 0)

    for bad in ({"octave": 0}, {"octave": 0, "semitone": "x"}, "not-a-dict"):
        try:
            Key.from_json(bad)
            raise AssertionError("应当报错：%r" % (bad,))
        except ValueError:
            pass


def test_json_chromatic_roundtrip_and_downgrade() -> None:
    score = Score.empty(title="半音", bpm=120, subdivision=4, mode=MODE_CHROMATIC)
    score.notes = [
        ScoreNote(0.0, 1.0, [Key(0, 0)]),
        ScoreNote(1.0, 1.0, [Key(0, 1)]),          # 黑键
        ScoreNote(2.0, 2.0, [Key(1, 6), Key(0, 9)]),  # 和弦含黑键
        ScoreNote(4.0, 1.0, [Key(-1, 11)]),
    ]
    payload = score.to_json()
    assert payload["version"] == SCORE_VERSION == "1.1"
    assert payload["meta"]["mode"] == "chromatic"
    assert payload["meta"]["referenceMidi"] == 60
    # 黑键额外带一个近似 degree（给旧版程序降级读）
    black = payload["notes"][1]["keys"][0]
    assert black["semitone"] == 1 and black["degree"] == 1
    natural = payload["notes"][0]["keys"][0]
    assert "degree" not in natural

    restored = json_io.loads_score(json_io.dumps_score(score))
    assert restored.meta.mode == MODE_CHROMATIC
    assert [[k for k in note.keys] for note in restored.notes] == [
        [Key(0, 0)],
        [Key(0, 1)],
        [Key(1, 6), Key(0, 9)],    # 和弦按界面顺序（高音在前）序列化
        [Key(-1, 11)],
    ]

    # 关掉降级字段也能往返
    payload2 = score.to_json(downgrade=False)
    assert "degree" not in payload2["notes"][1]["keys"][0]


def test_json_out_of_range_and_bad_mode() -> None:
    payload = {
        "format": "wuthering-score",
        "version": "1.1",
        "meta": {"title": "坏谱", "mode": "chromatic"},
        "notes": [{"beat": 0, "duration": 1, "keys": [{"octave": 0, "semitone": 12}]}],
    }
    score = json_io.loads_score(json_io.json.dumps(payload))
    issues = score.validate(KeyMap(MODE_CHROMATIC, PRESET_CHROMATIC))
    assert any("超出游戏音域" in item.message for item in issues), [str(i) for i in issues]

    bad_mode = dict(payload)
    bad_mode["meta"] = {"mode": "pentatonic"}
    try:
        json_io.loads_score(json_io.json.dumps(bad_mode))
        raise AssertionError("非法模式应当报错")
    except Exception as exc:
        assert "mode" in str(exc)


# --------------------------------------------------------------------------- #
# 8) 文本谱半音写法
# --------------------------------------------------------------------------- #

def test_text_chromatic_notation_roundtrip() -> None:
    score = Score.empty(title="半音文本", bpm=100, subdivision=4, mode=MODE_CHROMATIC)
    score.notes = [
        ScoreNote(0.0, 1.0, [Key(0, 1)]),                   # 中音#1 → #1
        ScoreNote(1.0, 3.0, [Key(1, 1), Key(0, 4)]),        # 高音#1 + 中音3，含延音
        ScoreNote(4.0, 1.0, [Key(-1, 6)]),                  # 低音#4 → -#4
        ScoreNote(5.0, 1.0, [Key(0, 0)]),
    ]
    score.sort()
    text = text_io.dumps_text(score)
    assert "#1" in text and "+#1" in text and "-#4" in text
    assert "# mode: chromatic" in text

    again = text_io.loads_text(text)
    assert again.meta.mode == MODE_CHROMATIC
    assert len(again.notes) == len(score.notes), text
    for original, parsed in zip(score.notes, again.notes):
        assert parsed.beat == original.beat
        assert parsed.duration == original.duration
        assert sorted(parsed.keys) == sorted(original.keys), (original.keys, parsed.keys)


def test_text_note_names_and_sharps() -> None:
    score = text_io.loads_text("C4 D#4 Eb4 F#4 A4 C#5 | #1 +#1 -#4 0")
    keys = [note.keys[0] for note in score.notes]
    assert keys == [
        Key(0, 0),   # C4
        Key(0, 3),   # D#4
        Key(0, 3),   # Eb4 = D#4
        Key(0, 6),   # F#4
        Key(0, 9),   # A4
        Key(1, 1),   # C#5
        Key(0, 1),   # #1 中音
        Key(1, 1),   # +#1 高音
        Key(-1, 6),  # -#4 低音
    ]
    assert score.meta.mode == MODE_CHROMATIC   # 含黑键 → 自动切到半音阶


def test_text_diatonic_letters_still_work() -> None:
    score = text_io.loads_text("q w e - a s d 0")
    assert score.meta.mode == MODE_DIATONIC
    assert [note.keys[0] for note in score.notes][:3] == [
        Key.from_degree(1, 1),
        Key.from_degree(1, 2),
        Key.from_degree(1, 3),
    ]
    assert score.notes[2].duration == 2   # "e -"


# --------------------------------------------------------------------------- #
# 9) 计时精度（半音阶 + 和弦 + 组合键）
# --------------------------------------------------------------------------- #

def test_chromatic_combo_timing_accuracy() -> None:
    keymap = KeyMap(MODE_CHROMATIC, PRESET_FF14)
    score = Score.empty(bpm=120, subdivision=32, mode=MODE_CHROMATIC)
    notes = []
    for index in range(60):
        octave = (1, 0, -1)[index % 3]
        notes.append(ScoreNote(float(index), 1.0, [Key(octave, index % 12)]))
    notes.append(ScoreNote(60.0, 4.0, [Key(1, 0), Key(0, 6)]))   # 收尾来一个和弦
    score.notes = notes

    plan = build_plan(score, keymap, PlayOptions(min_key_interval_ms=0))
    assert plan.combo_count > 0, "高音/低音两档是组合键，测试应覆盖到组合键路径"
    assert plan.event_count > 0

    backend = DummyBackend()
    engine = PlayEngine(backend, EngineCallbacks())
    engine.start(plan, PlayOptions(countdown_s=0, min_key_interval_ms=0), countdown=0)
    assert engine.wait(30.0)
    assert len(backend.events) == plan.event_count

    tracker = JitterTracker("半音阶调度")
    base = backend.events[0][0]
    for (stamp, _char, _is_down), expected in zip(backend.events, plan.events):
        tracker.add_relative(expected.time_ms, (stamp - base) * 1000.0)
    tracker.samples.pop(0)
    print("    " + tracker.report())
    # 门槛与本机验收标准一致（5ms / 2ms）；CI 上用环境变量放宽
    assert tracker.mean_ms() < float(os.environ.get("WUWA_TEST_JITTER_MEAN_MS", "2")), tracker.report()
    assert tracker.percentile_ms(99.0) < float(
        os.environ.get("WUWA_TEST_JITTER_P99_MS", "5")
    ), tracker.report()
    assert backend.pressed == []


def test_ff14_preset_full_range_plays() -> None:
    """FF14 预设下 36 个音都能展开出事件（不低于 4 个事件/音，组合键更多）。"""
    keymap = KeyMap(MODE_CHROMATIC, PRESET_FF14)
    score = Score.empty(bpm=120, subdivision=32, mode=MODE_CHROMATIC)
    score.notes = [ScoreNote(float(i), 1.0, [key]) for i, key in enumerate(chromatic_keys())]
    plan = build_plan(score, keymap, PlayOptions())
    assert plan.note_count == 36
    assert check_mapping(score, keymap) == []
    assert plan.combo_count == 24          # 低音与高音两档（12+12）用组合键
    assert plan.modifier_count > 0
    assert all(not event.char.isupper() for event in plan.events)


def test_chromatic_scale_example_plays_all_keys() -> None:
    """示例曲《半音阶练习》：72 个音符覆盖 36 键上下行。"""
    import os

    path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "examples", "半音阶练习.score.json"
    )
    score = json_io.load_score(path)
    assert score.meta.mode == MODE_CHROMATIC
    keymap = KeyMap(MODE_CHROMATIC, PRESET_CHROMATIC)
    errors = [item for item in score.validate(keymap) if item.level == "error"]
    assert errors == [], [str(item) for item in errors]
    assert check_mapping(score, keymap) == []
    plan = build_plan(score, keymap, PlayOptions())
    assert plan.note_count == 72
    assert plan.combo_count == 0
    assert plan.event_count == 144
    pitches = sorted({key.pitch for note in score.notes for key in note.keys})
    assert pitches == list(range(-12, 24))   # 36 个半音一个不漏
