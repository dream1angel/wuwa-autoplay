# -*- coding: utf-8 -*-
"""把 core 的回调桥接成 Qt 信号（跨线程安全）。

演奏引擎跑在独立线程里，它发出的回调若直接碰控件会导致崩溃；
这里统一转成 Qt 信号：Qt 会自动以队列方式投递到主线程执行。
"""

from __future__ import annotations

from PySide6.QtCore import QObject, Signal

from core.engine import EngineCallbacks

__all__ = ["EngineBridge", "HotkeyBridge"]


class EngineBridge(QObject):
    """引擎 → 界面。"""

    stateChanged = Signal(str)                 # EngineState.value
    countdownTick = Signal(float)              # 剩余秒数（0 = 开始演奏）
    progressTick = Signal(float, float, int)   # 已播 ms / 总 ms / 当前小节
    messagePosted = Signal(str, str)           # level, 文本
    finished = Signal(str)                     # 结束原因

    def callbacks(self) -> EngineCallbacks:
        """返回可直接交给 :class:`core.engine.PlayEngine` 的回调集合。"""
        return EngineCallbacks(
            on_state=lambda state: self.stateChanged.emit(state.value),
            on_countdown=lambda value: self.countdownTick.emit(float(value)),
            on_progress=lambda elapsed, total, bar: self.progressTick.emit(float(elapsed), float(total), int(bar)),
            on_message=lambda level, text: self.messagePosted.emit(str(level), str(text)),
            on_finished=lambda reason: self.finished.emit(str(reason)),
        )


class HotkeyBridge(QObject):
    """global 热键（钩子线程）→ 界面主线程。"""

    triggered = Signal(str)  # 功能名：start / pause / emergency / exit

    def make_callback(self, name: str):
        """生成给 :class:`core.hotkeys.HotkeyManager` 用的回调。"""
        return lambda: self.triggered.emit(name)
