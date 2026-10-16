# -*- coding: utf-8 -*-
"""阶段 2 测试：事件展开、计时精度、暂停/急停、卡键兜底。

计时精度用 :class:`~core.backends.DummyBackend` 记录真实时间戳来验证，
不会碰你的键盘；如果你在开发机上跑测试，可以放心离开键盘。
"""

from __future__ import annotations

import os
import time

from core.backends import DummyBackend, create_backend, probe_backends
from core.clock import JitterTracker, PrecisionClock, TimerResolution
from core.engine import (
    EngineCallbacks,
    EngineError,
    EngineState,
    PlayEngine,
    PlayOptions,
    build_plan,
    format_duration,
)
from core.focus import matches_keyword, split_keywords
from core.model import Score, ScoreNote
from core.notes import Key, KeyMap


# --------------------------------------------------------------------------- #
# 辅助
# --------------------------------------------------------------------------- #

#: 计时抖动的断言门槛（毫秒）。本机默认按验收标准 5ms / 2ms；
#: 共享 CI runner 的调度噪音远大于本机，用环境变量放宽（见 .github/workflows/tests.yml）。
JITTER_P99_MS = float(os.environ.get("WUWA_TEST_JITTER_P99_MS", "5"))
JITTER_MEAN_MS = float(os.environ.get("WUWA_TEST_JITTER_MEAN_MS", "2"))


def _score_with_notes(notes, **meta_kwargs) -> Score:
    score = Score.empty(title="测试", **meta_kwargs)
    score.notes = list(notes)
    return score


def _cycle_keys(count: int):
    """依次循环使用 21 个自然音，避免相邻音符同键而被合并。"""
    from core.notes import MODE_DIATONIC, all_keys

    keys = all_keys(MODE_DIATONIC)
    return [keys[i % len(keys)] for i in range(count)]


# --------------------------------------------------------------------------- #
# 事件展开
# --------------------------------------------------------------------------- #

def test_basic_expansion_timing() -> None:
    """120BPM + 每拍 4 格 → 每格 125ms；按下-抬起默认 30ms。"""
    score = _score_with_notes(
        [ScoreNote(0, 1, [Key.from_degree(0, 1)]), ScoreNote(1, 1, [Key.from_degree(0, 2)])],
        bpm=120,
        subdivision=4,
    )
    plan = build_plan(score, KeyMap(), PlayOptions())
    assert abs(plan.slot_ms - 125.0) < 1e-9
    assert plan.event_count == 4
    assert plan.note_count == 2
    assert plan.chord_count == 0

    down_a, up_a, down_s, up_s = plan.events
    assert (down_a.char, down_a.time_ms, down_a.is_down) == ("a", 0.0, True)
    assert (up_a.char, up_a.time_ms, up_a.is_down) == ("a", 30.0, False)
    assert (down_s.char, down_s.time_ms) == ("s", 125.0)
    assert (up_s.char, up_s.time_ms) == ("s", 155.0)
    assert abs(plan.duration_ms - (155.0 + 120.0)) < 1e-9


def test_hold_never_exceeds_note_span() -> None:
    """极短的音（1/32 格）必须自动缩短按住时间，避免吞掉下一个音。"""
    score = _score_with_notes([ScoreNote(0, 1, [Key.from_degree(0, 1)])], bpm=120, subdivision=32)
    plan = build_plan(score, KeyMap(), PlayOptions())
    assert abs(plan.slot_ms - 15.625) < 1e-9
    down, up = plan.events
    assert abs((up.time_ms - down.time_ms) - 10.625) < 1e-9, "按住时间应为 时值-5ms"


def test_chord_stagger_and_order() -> None:
    """和弦内多键按界面顺序（高音在前）错开 1.5ms。"""
    score = _score_with_notes([ScoreNote(0, 2, [Key.from_degree(0, 1), Key.from_degree(1, 3)])], bpm=120, subdivision=4)
    plan = build_plan(score, KeyMap(), PlayOptions(chord_stagger_ms=2.0))
    assert plan.chord_count == 1
    downs = [e for e in plan.events if e.is_down]
    assert [e.char for e in downs] == ["e", "a"], "高音3(e) 应先于 中音1(a)"
    assert abs(downs[1].time_ms - downs[0].time_ms - 2.0) < 1e-9


def test_min_key_interval_merges_repeats() -> None:
    """同一个键的密集连击会被合并成一次长按（绕开游戏节流）。"""
    score = _score_with_notes(
        [ScoreNote(0, 1, [Key.from_degree(0, 1)]), ScoreNote(1, 1, [Key.from_degree(0, 1)])],
        bpm=120,
        subdivision=4,
    )
    normal = build_plan(score, KeyMap(), PlayOptions(min_key_interval_ms=25))
    assert normal.merged_count == 0
    assert [e.is_down for e in normal.events] == [True, False, True, False]

    merged = build_plan(score, KeyMap(), PlayOptions(min_key_interval_ms=200))
    assert merged.merged_count == 1
    assert [e.is_down for e in merged.events] == [True, False], "连击应合并为一次长按"
    assert merged.events[1].time_ms > normal.events[1].time_ms


def test_up_before_down_at_same_time() -> None:
    """同一时刻先抬起再按下，绝不出现同键叠按。

    构造：第一音按住 495ms（时值 4 格 = 500ms，抬起留 5ms 余量），
    第二音起点恰好落在 3.96 格（= 495ms），于是两个事件同时刻。
    """
    score = _score_with_notes(
        [ScoreNote(0, 4, [Key.from_degree(0, 1)]), ScoreNote(3.96, 1, [Key.from_degree(0, 1)])],
        bpm=120,
        subdivision=4,
    )
    plan = build_plan(score, KeyMap(), PlayOptions(key_hold_ms=495, min_key_interval_ms=0))
    same_time = [e for e in plan.events if abs(e.time_ms - 495.0) < 1e-6]
    assert len(same_time) == 2, [(e.char, e.time_ms, e.is_down) for e in plan.events]
    assert same_time[0].is_down is False, "应先抬后按"
    assert same_time[1].is_down is True


def test_unmapped_key_warning() -> None:
    """音没有绑定按键时给出警告而不是崩溃。"""
    from core.notes import MODE_DIATONIC

    keymap = KeyMap(MODE_DIATONIC)
    keymap._strokes.pop((1, 4))  # 故意打断「高音3」的映射（半音 4）
    score = _score_with_notes([ScoreNote(0, 1, [Key.from_degree(1, 3)])], bpm=120, subdivision=4)
    plan = build_plan(score, keymap, PlayOptions())
    assert plan.event_count == 0
    assert plan.warnings and "没有按键" in plan.warnings[0]


def test_options_validation() -> None:
    for bad in [dict(speed=9.0), dict(key_hold_ms=0), dict(min_key_interval_ms=-1), dict(countdown_s=-5)]:
        try:
            build_plan(_score_with_notes([ScoreNote(0, 1, [Key.from_degree(0, 1)])]), KeyMap(), PlayOptions(**bad))
            raise AssertionError("非法参数应当报错：%r" % bad)
        except EngineError:
            pass


# --------------------------------------------------------------------------- #
# 真实调度精度
# --------------------------------------------------------------------------- #

def test_scheduling_accuracy() -> None:
    """用 DummyBackend 实测调度误差：均值 < 2ms，p99 < 5ms。"""
    keys = _cycle_keys(80)
    notes = [ScoreNote(float(index), 1, [keys[index]]) for index in range(80)]
    score = _score_with_notes(notes, bpm=120, subdivision=32)
    plan = build_plan(score, KeyMap(), PlayOptions(min_key_interval_ms=0))

    backend = DummyBackend()
    engine = PlayEngine(backend, EngineCallbacks())
    engine.start(plan, PlayOptions(countdown_s=0, min_key_interval_ms=0), countdown=0)
    assert engine.wait(20.0)

    assert len(backend.events) == plan.event_count, "事件必须全部执行完"
    tracker = JitterTracker("实测调度")
    base = backend.events[0][0]
    for (stamp, char, is_down), expected in zip(backend.events, plan.events):
        tracker.add_relative(expected.time_ms, (stamp - base) * 1000.0)

    # 首个事件作为基准点，误差必然为 0，不参与统计
    tracker.samples.pop(0)
    print("    " + tracker.report())
    assert tracker.mean_ms() < JITTER_MEAN_MS, "平均误差过大：%s" % tracker.report()
    assert tracker.percentile_ms(99.0) < JITTER_P99_MS, "p99 误差超门槛：%s" % tracker.report()
    assert engine.state == EngineState.STOPPED
    assert backend.pressed == [], "演奏结束后不应残留按下的键"
    assert engine.finish_reason == "演奏完成"


def test_precision_clock_wait() -> None:
    """PrecisionClock 的自旋等待误差应远小于 15.6ms 的系统睡眠粒度。

    断言用 p99（最大误差可能被操作系统调度的一瞬间抖动放大，
    在打包/编译同时进行的机器上不适合作为硬性门槛）。
    """
    clock = PrecisionClock()
    with TimerResolution():
        tracker = JitterTracker("时钟")
        for _ in range(20):
            target = time.perf_counter() + 0.004
            clock.wait_until(target)
            tracker.add(target, time.perf_counter())
    print("    " + tracker.report())
    # 门槛比引擎调度测试宽：这里只验证「不依赖 time.sleep 的 15.6ms 粒度」
    assert tracker.percentile_ms(99.0) < max(8.0, JITTER_P99_MS), tracker.report()
    assert tracker.mean_ms() < max(2.0, JITTER_MEAN_MS), tracker.report()

    # 可被打断
    assert clock.wait_until(time.perf_counter() + 1.0, should_abort=lambda: True) is False


# --------------------------------------------------------------------------- #
# 暂停 / 继续 / 急停
# --------------------------------------------------------------------------- #

def test_pause_and_resume() -> None:
    score = _score_with_notes(
        [ScoreNote(float(i), 1, [Key.from_degree(0, 1 + i % 7)]) for i in range(40)],
        bpm=120,
        subdivision=8,
    )
    plan = build_plan(score, KeyMap(), PlayOptions(min_key_interval_ms=0))
    backend = DummyBackend()
    engine = PlayEngine(backend, EngineCallbacks())
    engine.start(plan, PlayOptions(countdown_s=0, min_key_interval_ms=0), countdown=0)
    assert engine.wait(0.1) is False, "40 音不应立刻结束"

    time.sleep(0.05)
    assert engine.pause() is True
    time.sleep(0.05)
    assert engine.state == EngineState.PAUSED, "应进入暂停状态"
    assert backend.pressed == [], "暂停时必须松开所有按键"

    time.sleep(0.15)  # 暂停期间不应产生任何新事件
    paused_events = len(backend.events)
    time.sleep(0.1)
    assert len(backend.events) == paused_events, "暂停期间不应继续发按键"

    started_pause = time.perf_counter()
    assert engine.resume() is True
    assert engine.wait(20.0)
    paused_duration = time.perf_counter() - started_pause
    assert engine.state == EngineState.STOPPED
    assert paused_duration > plan.duration_ms / 1000.0 * 0.5, "恢复后仍需演奏剩余部分"
    # 暂停时引擎会额外补发一次 key_up（安全兜底），因此事件数允许略多于计划；
    # 但所有「按下」必须与计划完全一致，说明一个音都没漏。
    executed_downs = [char for _, char, is_down in backend.events if is_down]
    planned_downs = [event.char for event in plan.events if event.is_down]
    assert executed_downs == planned_downs, "暂停恢复后必须完整弹完全部音符"
    assert backend.pressed == []


def test_emergency_stop_releases_keys_immediately() -> None:
    """长按期间按 F10 必须立刻松开按键。"""
    score = _score_with_notes([ScoreNote(0, 32, [Key.from_degree(0, 1)])], bpm=120, subdivision=4)
    plan = build_plan(score, KeyMap(), PlayOptions(key_hold_ms=1000))
    backend = DummyBackend()
    engine = PlayEngine(backend, EngineCallbacks())
    engine.start(plan, PlayOptions(countdown_s=0, key_hold_ms=1000), countdown=0)

    time.sleep(0.15)
    assert backend.pressed == ["a"], "此时应处于长按状态（用来验证急停有效）"
    engine.emergency_stop()
    assert backend.pressed == [], "急停必须立即释放全部按键"
    engine.wait(5.0)
    assert engine.state == EngineState.STOPPED
    assert engine.finish_reason == "紧急停止"


def test_stop_mid_play() -> None:
    score = _score_with_notes(
        [ScoreNote(float(i), 1, [Key.from_degree(0, 1 + i % 7)]) for i in range(200)],
        bpm=120,
        subdivision=4,
    )
    plan = build_plan(score, KeyMap(), PlayOptions(min_key_interval_ms=0))
    backend = DummyBackend()
    engine = PlayEngine(backend, EngineCallbacks())
    engine.start(plan, PlayOptions(countdown_s=0, min_key_interval_ms=0), countdown=0)
    time.sleep(0.3)
    engine.stop("测试停止")
    assert engine.state == EngineState.STOPPED
    assert backend.pressed == []
    assert len(backend.events) < plan.event_count, "停止后不应继续发按键"


def test_countdown_can_be_cancelled() -> None:
    score = _score_with_notes([ScoreNote(0, 1, [Key.from_degree(0, 1)])], bpm=120, subdivision=4)
    plan = build_plan(score, KeyMap(), PlayOptions(countdown_s=3))
    backend = DummyBackend()
    engine = PlayEngine(backend, EngineCallbacks())
    engine.start(plan, PlayOptions(countdown_s=3))
    time.sleep(0.1)
    assert engine.state == EngineState.COUNTDOWN
    engine.stop("取消")
    assert engine.state == EngineState.STOPPED
    assert backend.events == []


def test_start_rejects_empty_plan() -> None:
    empty = Score.empty()
    plan = build_plan(empty, KeyMap(), PlayOptions())
    engine = PlayEngine(DummyBackend(), EngineCallbacks())
    try:
        engine.start(plan, PlayOptions(countdown_s=0))
        raise AssertionError("空曲谱不应开始演奏")
    except EngineError:
        pass


def test_state_callback_reports_sequence() -> None:
    seen = []
    callbacks = EngineCallbacks(on_state=lambda s: seen.append(s))
    score = _score_with_notes([ScoreNote(0, 1, [Key.from_degree(0, 1)])], bpm=120, subdivision=4)
    plan = build_plan(score, KeyMap(), PlayOptions())
    engine = PlayEngine(DummyBackend(), callbacks)
    engine.start(plan, PlayOptions(countdown_s=1), countdown=0)
    assert engine.wait(10.0)
    assert EngineState.PLAYING in seen
    assert seen[-1] == EngineState.STOPPED


# --------------------------------------------------------------------------- #
# 后端与焦点辅助
# --------------------------------------------------------------------------- #

def test_backend_factory_and_probe() -> None:
    kinds = probe_backends()
    assert "pydirectinput" in kinds and "interception" in kinds and "dummy" in kinds
    assert kinds["dummy"] == (True, ""), "演练后端必须始终可用"

    dummy = create_backend("dummy")
    assert isinstance(dummy, DummyBackend)
    dummy.key_down("a")
    dummy.key_up("a")
    assert dummy.commands() == [("a", True), ("a", False)]
    assert dummy.pressed == []

    try:
        create_backend("不存在的后端")
        raise AssertionError("未知后端应当报错")
    except Exception as exc:
        assert "未知的按键后端" in str(exc)


def test_focus_keyword_matching() -> None:
    assert split_keywords("Wuthering Waves|鸣潮;Client-Win64") == [
        "wuthering waves",
        "鸣潮",
        "client-win64",
    ]
    assert matches_keyword("", "任意窗口", "") is True
    assert matches_keyword("鸣潮", "鸣潮", "") is True
    assert matches_keyword("Wuthering Waves", "Wuthering Waves 1.0", "") is True
    assert matches_keyword("Client-Win64", "", "Client-Win64-Shipping.exe") is True
    assert matches_keyword("鸣潮", "记事本", "notepad.exe") is False


def test_format_duration() -> None:
    assert format_duration(0) == "0:00.0"
    assert format_duration(62000) == "1:02.0"
    assert format_duration(302499) == "5:02.5"


def test_engine_ignores_focus_when_keyword_empty() -> None:
    """未配置窗口关键字时不做焦点检测（避免误暂停）。"""
    score = _score_with_notes([ScoreNote(0, 1, [Key.from_degree(0, 1)])], bpm=120, subdivision=4)
    plan = build_plan(score, KeyMap(), PlayOptions())
    backend = DummyBackend()
    engine = PlayEngine(backend, EngineCallbacks())
    engine.start(plan, PlayOptions(countdown_s=0, focus_keyword="", pause_on_focus_loss=True), countdown=0)
    assert engine.wait(10.0)
    assert len(backend.events) == 2


# --------------------------------------------------------------------------- #
# 焦点守护布防（回归：曾经 F8 之后只响第一个音就要按 F9 才继续）
# --------------------------------------------------------------------------- #

def test_focus_watcher_needs_arming() -> None:
    """没见过游戏窗口之前，绝不能因为「失去焦点」而暂停。"""
    from core.focus import FocusWatcher

    events = []
    sequence = [False, False, True, True, False, True]
    cursor = {"index": 0}

    def probe():
        value = sequence[min(cursor["index"], len(sequence) - 1)]
        cursor["index"] += 1
        return value, ("鸣潮" if value else "记事本")

    watcher = FocusWatcher(
        "鸣潮",
        interval_ms=1,
        on_lost=lambda detail: events.append(("lost", detail)),
        on_regained=lambda detail: events.append(("back", detail)),
        probe=probe,
    )
    assert watcher.enabled and not watcher.armed
    assert "暂不会自动暂停" in watcher.describe()

    # 前两次都没命中：不布防、不报「失去焦点」
    watcher.poll(force=True)
    watcher.poll(force=True)
    assert events == [], events
    assert not watcher.armed

    # 第一次看到游戏窗口：布防，但不报「回来了」
    watcher.poll(force=True)
    assert watcher.armed
    assert events == [], events
    assert "已在守护" in watcher.describe()

    # 仍在游戏里：无跳变
    watcher.poll(force=True)
    assert events == []

    # 布防之后离开 → 报一次失去焦点
    watcher.poll(force=True)
    assert events == [("lost", "记事本")], events

    # 回来 → 报一次回到游戏
    watcher.poll(force=True)
    assert [item[0] for item in events] == ["lost", "back"], events


def test_engine_plays_through_when_window_never_matches() -> None:
    """回归：关键字跟实际窗口对不上时，整首必须弹完，不能只弹第一个音。

    修复前：第一个音之后首次轮询就判定「失去焦点」→ 自动暂停，需要按 F9 才继续。
    """
    keys = _cycle_keys(12)
    notes = [ScoreNote(float(index), 1, [keys[index]]) for index in range(12)]
    score = _score_with_notes(notes, bpm=120, subdivision=16)
    plan = build_plan(score, KeyMap(), PlayOptions(min_key_interval_ms=0))
    assert plan.event_count == 24

    backend = DummyBackend()
    engine = PlayEngine(backend, EngineCallbacks())
    options = PlayOptions(
        countdown_s=0,
        min_key_interval_ms=0,
        focus_keyword="绝对不存在的窗口名ZZZ",
        pause_on_focus_loss=True,
        focus_poll_ms=30.0,
    )
    engine.start(plan, options, countdown=0)
    assert engine.wait(20.0), "演奏被卡住了（焦点误判导致的暂停）"
    assert engine.state == EngineState.STOPPED
    assert len(backend.events) == plan.event_count, "不应因为焦点判断而中断演奏"
    assert backend.pressed == []
    assert engine.finish_reason == "演奏完成"
