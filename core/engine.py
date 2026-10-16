# -*- coding: utf-8 -*-
"""演奏引擎：把曲谱展开成按键事件，并按严格节拍调度执行。

核心流程
--------
1. :func:`build_plan` 把 :class:`~core.model.Score` 展开成
   ``[(绝对时间ms, 按键, 按下/抬起)]`` 事件列表（纯函数，可单独测试）；
2. :class:`PlayEngine` 在独立线程里按事件表执行，采用
   :class:`core.clock.PrecisionClock` 的混合计时，目标单拍误差 < 5ms；
3. 状态机：``IDLE → COUNTDOWN → PLAYING ⇄ PAUSED → STOPPED``，
   任何路径（正常结束 / 异常 / 暂停 / 停止 / F10）都会释放所有按键，杜绝卡键。

线程安全
--------
``emergency_stop()`` 会被热键线程直接调用；所有后端调用都在 ``_io_lock`` 内，
且紧急停止会立刻向 ``DummyBackend`` 之外的真实后端发送 ``key_up``。
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from .backends import BackendError, KeyBackend
from .clock import PrecisionClock, TimerResolution, now
from .focus import FocusWatcher, foreground_info, is_target_foreground
from .model import Score, ScoreNote
from .notes import Key, KeyMap, key_sort_index

__all__ = [
    "EngineState",
    "EngineError",
    "PlayOptions",
    "KeyEvent",
    "PlayPlan",
    "build_plan",
    "EngineCallbacks",
    "PlayEngine",
]


class EngineError(Exception):
    """演奏引擎相关错误，消息面向用户（中文）。"""


class EngineState(str, Enum):
    IDLE = "idle"
    COUNTDOWN = "countdown"
    PLAYING = "playing"
    PAUSED = "paused"
    STOPPED = "stopped"
    ERROR = "error"

    @property
    def text(self) -> str:
        return {
            "idle": "就绪",
            "countdown": "倒计时",
            "playing": "演奏中",
            "paused": "已暂停",
            "stopped": "已停止",
            "error": "出错",
        }[self.value]


# --------------------------------------------------------------------------- #
# 参数与事件
# --------------------------------------------------------------------------- #

@dataclass
class PlayOptions:
    """演奏参数（对应设置页里的各项数值）。"""

    speed: float = 1.0                 # 变速 0.5 ~ 2.0
    key_hold_ms: float = 30.0          # 按下到抬起的间隔
    chord_stagger_ms: float = 1.5      # 和弦内各键的错开间隔
    min_key_interval_ms: float = 25.0  # 同一个键两次按下的最小间隔（防游戏节流）
    countdown_s: float = 3.0           # 演奏前倒计时
    focus_keyword: str = ""            # 目标窗口关键字（空 = 不检测）
    pause_on_focus_loss: bool = True   # 失焦自动暂停
    focus_poll_ms: float = 200.0       # 焦点轮询间隔
    spin_margin_ms: float = 3.0        # 计时忙等窗口
    tail_ms: float = 120.0             # 结束时等待最后一个音抬起的余量

    def validate(self) -> None:
        if not (0.25 <= self.speed <= 4.0):
            raise EngineError("变速倍率 %s 超出允许范围 0.5x ~ 2.0x" % self.speed)
        if self.key_hold_ms < 1:
            raise EngineError("按下-抬起间隔至少 1ms")
        if self.min_key_interval_ms < 0:
            raise EngineError("最小按键间隔不能为负数")
        if self.chord_stagger_ms < 0:
            raise EngineError("和弦错开间隔不能为负数")
        if self.countdown_s < 0:
            raise EngineError("倒计时不能为负数")


@dataclass
class KeyEvent:
    """一个待执行的按键动作。

    ``char`` 是**主键**（组合键会额外生成修饰键事件，修饰键事件的 ``char``
    就是 ``ctrl`` / ``alt`` / ``shift``）。
    """

    time_ms: float      # 相对演奏开始（倒计时结束）的毫秒数
    char: str           # 实际按键（主键或修饰键）
    is_down: bool       # True=按下，False=抬起
    key: Optional[Key] = None    # 来源音（便于界面高亮）
    beat: float = 0.0   # 来源音符起始格
    seq: int = 0        # 稳定排序用
    modifiers: Tuple[str, ...] = ()   # 该动作所属按键的修饰键（仅主键事件有值）

    @property
    def is_modifier(self) -> bool:
        return self.char in ("ctrl", "alt", "shift")

    def describe(self) -> str:
        return "%8.2fms %s %s" % (self.time_ms, "按下" if self.is_down else "抬起", self.char.upper())


@dataclass
class PlayPlan:
    """展开后的演奏计划。"""

    events: List[KeyEvent] = field(default_factory=list)
    duration_ms: float = 0.0        # 最后一个音结束的毫秒数
    slot_ms: float = 0.0            # 每格毫秒数
    slots_per_bar: int = 16
    total_slots: int = 0
    note_count: int = 0
    chord_count: int = 0
    merged_count: int = 0           # 因最小按键间隔被合并的同键连击数
    combo_count: int = 0            # 使用组合键（含修饰键）的音数
    modifier_count: int = 0         # 生成的修饰键事件数
    countdown_s: float = 3.0
    warnings: List[str] = field(default_factory=list)

    @property
    def event_count(self) -> int:
        return len(self.events)

    @property
    def bar_count(self) -> int:
        return max(1, (self.total_slots + self.slots_per_bar - 1) // self.slots_per_bar)

    @property
    def bar_ms(self) -> float:
        return self.slot_ms * self.slots_per_bar

    def bar_at(self, elapsed_ms: float) -> int:
        if self.bar_ms <= 0:
            return 0
        return max(0, min(self.bar_count - 1, int(elapsed_ms // self.bar_ms)))

    def total_ms_with_tail(self) -> float:
        return self.duration_ms

    def describe(self) -> str:
        return "共 %d 个音符（%d 个和弦）/ %d 个按键事件 / %d 小节 / 时长 %s" % (
            self.note_count,
            self.chord_count,
            self.event_count,
            self.bar_count,
            format_duration(self.duration_ms),
        )


def format_duration(ms: float) -> str:
    """毫秒 → ``m:ss``。"""
    total_seconds = max(0.0, float(ms)) / 1000.0
    minutes = int(total_seconds // 60)
    seconds = total_seconds - minutes * 60
    return "%d:%04.1f" % (minutes, seconds)


# --------------------------------------------------------------------------- #
# 事件展开（纯函数）
# --------------------------------------------------------------------------- #

def check_mapping(score: Score, keymap: KeyMap) -> List[Key]:
    """返回曲谱里在当前映射表下**没有按键**的音（演奏前校验用）。"""
    missing: List[Key] = []
    for note in score.notes:
        for key in note.keys:
            if keymap.stroke_for(key) is None and key not in missing:
                missing.append(key)
    return missing


def build_plan(score: Score, keymap: KeyMap, options: Optional[PlayOptions] = None) -> PlayPlan:
    """把曲谱展开为按键事件表（支持单键与组合键）。

    关键规则
    --------
    * 按下 → 抬起的间隔取 ``min(key_hold_ms, 音符时值 - 5ms)``，避免长按吞掉下一个音；
    * 和弦内多键按界面顺序错开 ``chord_stagger_ms``（默认 1.5ms）；
    * **同一个完整按键动作**（修饰键 + 主键）相邻两次按下间隔小于
      ``min_key_interval_ms`` 时合并为一次长按，绕开游戏对极短重复按键的节流；
    * **组合键**展开为「修饰键按下 → 主键按下 → 主键抬起 → 修饰键抬起」，
      并按**修饰键引用计数**合并：同一段时间内多个音共用 ``shift`` 时只按一次，
      最后一个使用者抬起后才释放（见 Chapter 9.5）；
    * 同一时刻的先后顺序固定为：修饰键按下 → 主键抬起 → 主键按下 → 修饰键抬起。
    """
    options = options or PlayOptions()
    options.validate()

    meta = score.meta
    slot_ms = meta.ms_per_slot(options.speed)
    plan = PlayPlan(
        slot_ms=slot_ms,
        slots_per_bar=meta.slots_per_bar,
        total_slots=int(round(max(score.total_slots(), 0.0))),
        countdown_s=options.countdown_s,
    )

    # 1) 展开成「每个完整按键动作的按下区间」
    groups: Dict[Tuple[str, Tuple[str, ...]], List[List[float]]] = {}
    missing: List[Key] = []
    seq = 0
    for note in sorted(score.notes, key=lambda n: n.beat):
        if not note.keys:
            continue
        plan.note_count += 1
        if len(note.keys) > 1:
            plan.chord_count += 1
        start_ms = float(note.beat) * slot_ms
        span_ms = float(note.duration) * slot_ms
        # 抬起时刻留 5ms 余量，防止和下一个音粘连
        hold = min(float(options.key_hold_ms), max(1.0, span_ms - 5.0))
        for index, key in enumerate(note.sorted_keys()):  # type: ignore[arg-type]
            stroke = keymap.stroke_for(key)
            if stroke is None:
                if key not in missing:
                    missing.append(key)
                continue
            if stroke.is_combo:
                plan.combo_count += 1
            down = start_ms + index * float(options.chord_stagger_ms)
            group = (stroke.main, stroke.modifiers)
            groups.setdefault(group, []).append([down, down + hold, seq])
            seq += 1

    if missing:
        plan.warnings.append(
            "有 %d 个音在当前映射下没有按键，已在演奏中跳过：%s"
            % (len(missing), "、".join(k.label() for k in missing))
        )

    # 2) 合并同一个按键动作的密集连击
    merged: List[Tuple[float, float, int, str, Tuple[str, ...]]] = []
    for (char, modifiers), items in groups.items():
        items.sort(key=lambda item: item[0])
        current = items[0]
        for nxt in items[1:]:
            if nxt[0] - current[1] < float(options.min_key_interval_ms):
                # 与上一次抬起的间隔过近：并入同一个长按
                current[1] = max(current[1], nxt[1])
                plan.merged_count += 1
            else:
                merged.append((current[0], current[1], int(current[2]), char, modifiers))
                current = nxt
        merged.append((current[0], current[1], int(current[2]), char, modifiers))

    # 3) 修饰键引用计数：把「需要某个修饰键的所有主键区间」合并成修饰键的按住区间
    lead = float(options.chord_stagger_ms)
    modifier_spans: Dict[str, List[List[float]]] = {}
    for down, up, _order, _char, modifiers in merged:
        for name in modifiers:
            modifier_spans.setdefault(name, []).append([max(0.0, down - lead), up + lead])
    # 相邻/重叠的修饰键区间合并（阈值取「最小按键间隔」与 20ms 的较大值，避免修饰键抖个不停）
    gap = max(float(options.min_key_interval_ms), 20.0)
    modifier_merged: List[Tuple[float, float, str]] = []
    for name, spans in modifier_spans.items():
        spans.sort(key=lambda item: item[0])
        current = spans[0]
        for nxt in spans[1:]:
            if nxt[0] - current[1] <= gap:
                current[1] = max(current[1], nxt[1])
            else:
                modifier_merged.append((current[0], current[1], name))
                current = nxt
        modifier_merged.append((current[0], current[1], name))

    # 4) 生成事件：修饰键按下/抬起 + 主键按下/抬起
    events: List[KeyEvent] = []
    for down, up, name in modifier_merged:
        events.append(KeyEvent(max(0.0, down), name, True, None, 0.0, -1))
        events.append(KeyEvent(up, name, False, None, 0.0, -1))
        plan.modifier_count += 2
    for down, up, order, char, modifiers in merged:
        events.append(KeyEvent(down, char, True, None, 0.0, order, modifiers))
        events.append(KeyEvent(up, char, False, None, 0.0, order, modifiers))

    # 给主键按下事件回填来源音信息（界面高亮用）
    lookup: Dict[Tuple[str, float], Key] = {}
    for note in score.notes:
        for key in note.keys:
            stroke = keymap.stroke_for(key)
            if stroke is not None:
                lookup[(stroke.main, round(float(note.beat) * slot_ms, 6))] = key
    for event in events:
        if event.is_down and not event.is_modifier:
            event.key = lookup.get((event.char, round(event.time_ms, 6)))

    # 同一时刻的固定顺序：修饰键按下 → 主键抬起 → 主键按下 → 修饰键抬起
    # （主键先抬后按：同一物理键绝不会出现「按着又按一次」；
    #   修饰键最早按下、最晚抬起：所有用到它的主键都在它按住期间完成）
    def _rank(event: KeyEvent) -> int:
        if event.is_modifier:
            return 0 if event.is_down else 3
        return 2 if event.is_down else 1

    events.sort(key=lambda e: (round(e.time_ms, 6), _rank(e), e.seq))
    plan.events = events

    if events:
        plan.duration_ms = max(event.time_ms for event in events) + float(options.tail_ms)
    else:
        plan.duration_ms = 0.0
    plan.countdown_s = float(options.countdown_s)
    return plan


# --------------------------------------------------------------------------- #
# 回调（GUI 适配层用，核心不依赖 Qt）
# --------------------------------------------------------------------------- #

@dataclass
class EngineCallbacks:
    """引擎向外通报状态变化的回调集合，全部可选。"""

    on_state: Optional[Callable[[EngineState], None]] = None
    on_countdown: Optional[Callable[[float], None]] = None
    on_progress: Optional[Callable[[float, float, int], None]] = None  # 已播 ms, 总 ms, 当前小节
    on_message: Optional[Callable[[str, str], None]] = None            # level, 文本
    on_finished: Optional[Callable[[str], None]] = None                # 结束原因

    def emit(self, name: str, *args) -> None:
        callback = getattr(self, name, None)
        if callback is None:
            return
        try:
            callback(*args)
        except Exception:
            # 回调异常绝不能影响演奏线程
            pass


# --------------------------------------------------------------------------- #
# 引擎
# --------------------------------------------------------------------------- #

class PlayEngine:
    """演奏引擎（线程安全，可脱离 GUI 使用）。"""

    def __init__(
        self,
        backend: KeyBackend,
        callbacks: Optional[EngineCallbacks] = None,
        clock: Optional[PrecisionClock] = None,
    ) -> None:
        self.backend = backend
        self.callbacks = callbacks or EngineCallbacks()
        self.clock = clock or PrecisionClock()
        self._state = EngineState.IDLE
        self._state_lock = threading.RLock()
        self._io_lock = threading.RLock()
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._pause_event = threading.Event()
        #: start() 之后立即置 True，线程收尾时清掉；用它消除「刚启动但还没进入
        #: PLAYING 状态」被误判为「没在演奏」的竞态（例如连续两次按 F8）。
        self._active = False
        self._resume_offset = 0.0
        self._plan: Optional[PlayPlan] = None
        self._options = PlayOptions()
        self._watcher: Optional[FocusWatcher] = None
        self._reason = ""
        self._auto_paused = False

    # ------------------------------------------------------------------ #
    # 状态
    # ------------------------------------------------------------------ #
    @property
    def state(self) -> EngineState:
        with self._state_lock:
            return self._state

    def _set_state(self, state: EngineState) -> None:
        with self._state_lock:
            if self._state == state:
                return
            self._state = state
        self.callbacks.emit("on_state", state)

    @property
    def is_busy(self) -> bool:
        return self._active or self.state in (EngineState.COUNTDOWN, EngineState.PLAYING, EngineState.PAUSED)

    @property
    def plan(self) -> Optional[PlayPlan]:
        return self._plan

    @property
    def finish_reason(self) -> str:
        return self._reason

    # ------------------------------------------------------------------ #
    # 启动 / 暂停 / 停止
    # ------------------------------------------------------------------ #
    def start(self, plan: PlayPlan, options: Optional[PlayOptions] = None, countdown: Optional[float] = None) -> None:
        """开始演奏（立即返回，实际在后台线程执行）。"""
        if self._thread is not None and self._thread.is_alive():
            if self.is_busy:
                raise EngineError("演奏已在进行中，请先停止")
            # 上一轮已经结束、线程只剩收尾：等它退出再开始，
            # 不要因为「线程还活着」就报「演奏已在进行中」（调用方会弹误导性的弹窗）
            if not self.wait(1.0):
                raise EngineError("上一次演奏还在收尾，请稍后再试")
        if not plan.events:
            raise EngineError("曲谱里没有任何可演奏的音符")

        self._plan = plan
        self._options = options or PlayOptions(countdown_s=plan.countdown_s)
        self._options.validate()
        seconds = self._options.countdown_s if countdown is None else float(countdown)
        self._stop_event.clear()
        self._pause_event.clear()
        self._resume_offset = 0.0
        self._reason = ""
        self._auto_paused = False
        self._watcher = FocusWatcher(
            self._options.focus_keyword,
            self._options.focus_poll_ms,
            on_lost=self._on_focus_lost,
            on_regained=self._on_focus_regained,
        )
        self.clock.spin_margin = max(0.0005, self._options.spin_margin_ms / 1000.0)
        try:
            self.backend.open()
        except BackendError as exc:
            self._reason = str(exc)
            self._set_state(EngineState.ERROR)
            raise
        self._thread = threading.Thread(
            target=self._run, args=(plan, seconds), name="play-engine", daemon=True
        )
        self._active = True
        self._thread.start()

    def pause(self) -> bool:
        """暂停演奏（线程会在下个事件边界让出）。"""
        if self.state not in (EngineState.PLAYING, EngineState.COUNTDOWN):
            return False
        self._pause_event.set()
        return True

    def resume(self) -> bool:
        if self.state != EngineState.PAUSED:
            return False
        self._auto_paused = False
        self._pause_event.clear()
        return True

    def toggle_pause(self) -> bool:
        if self.state == EngineState.PAUSED:
            return self.resume()
        return self.pause()

    def stop(self, reason: str = "用户停止") -> None:
        """请求停止；线程会在最近的事件边界退出并释放所有按键。"""
        self._reason = self._reason or reason
        self._stop_event.set()
        self._pause_event.clear()
        thread = self._thread
        if thread is not None and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=2.0)
        if self.state not in (EngineState.STOPPED, EngineState.ERROR):
            self._set_state(EngineState.STOPPED)

    def emergency_stop(self) -> None:
        """紧急停止（F10）：立即释放所有按键，不等待线程。"""
        self._reason = "紧急停止"
        self._stop_event.set()
        self._pause_event.clear()
        self._release_all()

    def wait(self, timeout: Optional[float] = None) -> bool:
        """等待演奏线程结束（测试用）。返回线程是否已结束。"""
        thread = self._thread
        if thread is None:
            return True
        thread.join(timeout=timeout)
        if not thread.is_alive():
            self._active = False
        return not thread.is_alive()

    def close(self) -> None:
        """释放资源（退出程序时调用）。"""
        self.stop("程序退出")
        self._release_all()
        try:
            self.backend.close()
        except Exception:  # pragma: no cover
            pass

    # ------------------------------------------------------------------ #
    # 线程主体
    # ------------------------------------------------------------------ #
    def _should_abort(self) -> bool:
        return self._stop_event.is_set() or self._pause_event.is_set()

    def _run(self, plan: PlayPlan, countdown_s: float) -> None:
        reason = "演奏完成"
        try:
            with TimerResolution():
                self._report_focus_state()
                if not self._run_countdown(countdown_s):
                    reason = self._reason or "已取消"
                    return
                reason = self._run_events(plan)
        except BackendError as exc:
            reason = "按键后端异常：%s" % exc
            self._set_state(EngineState.ERROR)
            self.callbacks.emit("on_message", "error", str(exc))
        except Exception as exc:  # pragma: no cover - 兜底，保证不卡键
            reason = "演奏异常：%s" % exc
            self._set_state(EngineState.ERROR)
            self.callbacks.emit("on_message", "error", reason)
        finally:
            self._release_all()
            self._active = False
            self._reason = self._reason or reason
            if self.state not in (EngineState.ERROR,):
                self._set_state(EngineState.STOPPED)
            self.callbacks.emit("on_message", "info", "已释放全部按键")
            self.callbacks.emit("on_finished", self._reason)

    def _report_focus_state(self) -> None:
        """演奏开始前把「程序看到的前台窗口」播报一次，便于用户核对关键字。

        关键字填错时不会有任何误暂停，但也意味着守护不会生效，
        所以这里必须给用户一个可见的反馈。
        """
        if self._watcher is None or not self._watcher.enabled:
            return
        hwnd, title, process = foreground_info()
        self.callbacks.emit(
            "on_message",
            "info",
            "焦点守护：关键字「%s」；当前前台窗口是「%s」(%s)。"
            "先切到游戏窗口后它才开始守护，之后你切走就会自动暂停"
            % (self._options.focus_keyword, title or "未知", process or "未知"),
        )

    def _run_countdown(self, seconds: float) -> bool:
        """3 秒倒计时，让用户切回游戏窗口。返回 False 表示被取消。"""
        if seconds <= 0:
            return True
        self._set_state(EngineState.COUNTDOWN)
        started = time.perf_counter()
        last_reported = -1
        while True:
            if self._stop_event.is_set():
                return False
            remaining = seconds - (time.perf_counter() - started)
            if remaining <= 0:
                break
            value = int(remaining) + (1 if remaining % 1 > 1e-6 else 0)
            if value != last_reported:
                last_reported = value
                self.callbacks.emit("on_countdown", float(value))
            time.sleep(0.02)
        self.callbacks.emit("on_countdown", 0.0)
        return True

    def _run_events(self, plan: PlayPlan) -> str:
        """按事件表演奏，返回结束原因。"""
        self._set_state(EngineState.PLAYING)
        base = time.perf_counter()
        offset = 0.0
        last_progress = -1.0
        total_ms = max(1.0, plan.duration_ms)
        index = 0

        while index < len(plan.events):
            if self._stop_event.is_set():
                return self._reason or "用户停止"
            if self._pause_event.is_set():
                if not self._handle_pause():
                    return self._reason or "用户停止"
                offset = self._resume_offset
                last_progress = -1.0
                continue

            event = plan.events[index]
            target = base + event.time_ms / 1000.0 + offset
            if not self.clock.wait_until(target, self._should_abort):
                # 被暂停或停止打断：不消费事件，回到循环顶部重新判断
                continue

            self._apply(event)
            index += 1

            # 进度上报（约 20Hz）
            elapsed_ms = (time.perf_counter() - base - offset) * 1000.0
            if elapsed_ms - last_progress >= 50.0 or index == len(plan.events):
                last_progress = elapsed_ms
                self.callbacks.emit("on_progress", min(elapsed_ms, total_ms), total_ms, plan.bar_at(elapsed_ms))

            # 焦点守护
            if self._watcher is not None and self._watcher.enabled:
                self._watcher.poll()

        # 演奏正常结束
        self.callbacks.emit("on_progress", total_ms, total_ms, plan.bar_count - 1)
        return self._reason or "演奏完成"

    def _handle_pause(self) -> bool:
        """阻塞直到继续或停止；返回 False 表示应当停止。

        因失焦而自动暂停时，会持续轮询焦点，回到游戏窗口后自动继续。
        """
        auto = self._auto_paused
        pause_started = time.perf_counter()
        self._release_all()
        self._set_state(EngineState.PAUSED)
        self.callbacks.emit(
            "on_message",
            "info",
            "已暂停（检测到离开游戏窗口）：切回游戏窗口会自动继续，也可以按 F9 继续"
            if auto
            else "已暂停：按 F9 继续",
        )
        while self._pause_event.is_set():
            if self._stop_event.is_set():
                return False
            if self._auto_paused and is_target_foreground(self._options.focus_keyword):
                self.callbacks.emit("on_message", "info", "已回到游戏窗口，自动继续")
                self._auto_paused = False
                self._pause_event.clear()
                break
            time.sleep(0.05)
        if self._stop_event.is_set():
            return False
        # 时间基准整体后移，保证恢复后节拍不漂移
        self._resume_offset += time.perf_counter() - pause_started
        self._set_state(EngineState.PLAYING)
        self.callbacks.emit("on_message", "info", "已继续")
        return True

    def _apply(self, event: KeyEvent) -> None:
        with self._io_lock:
            # 已经急停/停止时不再按下新键（收紧 F10 之后的竞态窗口）
            if event.is_down and self._stop_event.is_set():
                return
            try:
                if event.is_down:
                    self.backend.key_down(event.char)
                else:
                    self.backend.key_up(event.char)
            except BackendError as exc:
                raise
            except Exception as exc:
                raise BackendError("按键 %s 失败：%s" % (event.char, exc)) from exc

    def _release_all(self) -> None:
        with self._io_lock:
            try:
                self.backend.release_all()
            except Exception:  # pragma: no cover - 兜底
                pass

    # ------------------------------------------------------------------ #
    # 焦点
    # ------------------------------------------------------------------ #
    def _on_focus_lost(self, detail: str) -> None:
        self.callbacks.emit(
            "on_message", "warning", "检测到离开游戏窗口（当前：%s），已自动暂停" % detail
        )
        if self._options.pause_on_focus_loss:
            self._auto_paused = True
            self._pause_event.set()

    def _on_focus_regained(self, detail: str) -> None:
        self.callbacks.emit("on_message", "info", "已回到游戏窗口（%s）" % detail)
