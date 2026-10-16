# -*- coding: utf-8 -*-
"""全局热键（F8 开始/停止、F9 暂停/继续、F10 紧急停止、Esc 退出演奏）。

实现要点
--------
* 基于 ``keyboard`` 库（Windows 下需要管理员权限，与游戏提权一致）；
* 库回调运行在**钩子线程**里，所以 :class:`HotkeyManager` 只负责转发，
  界面必须通过 Qt 信号切回主线程，绝不能在回调里直接操作控件；
* 支持两种注册组：
  - ``global``：程序启动即注册（F8/F9/F10）；
  - ``session``：只在演奏期间注册（Esc），避免平时抢走全局 Esc。
"""

from __future__ import annotations

import time
from typing import Callable, Dict, List, Optional, Tuple

__all__ = [
    "DEFAULT_HOTKEYS",
    "HOTKEY_LABELS",
    "normalize_hotkey",
    "find_conflicts",
    "HotkeyManager",
]

#: 默认快捷键
DEFAULT_HOTKEYS: Dict[str, str] = {
    "mode": "f7",        # 切换半音阶模式（开/关）
    "start": "f8",       # 开始 / 停止演奏
    "pause": "f9",       # 暂停 / 继续
    "emergency": "f10",  # 紧急停止（立即释放所有按键）
    "exit": "esc",       # 退出演奏模式（仅演奏期间生效）
}

#: 设置页显示名
HOTKEY_LABELS: Dict[str, str] = {
    "mode": "切换半音阶模式",
    "start": "开始 / 停止演奏",
    "pause": "暂停 / 继续",
    "emergency": "紧急停止（释放所有按键）",
    "exit": "退出演奏模式",
}

#: 只在演奏期间注册的快捷键（避免平时抢占系统按键）
SESSION_HOTKEYS = ("exit",)

_ALIASES = {
    "return": "enter",
    "esc": "esc",
    "escape": "esc",
    "del": "delete",
    "ins": "insert",
    "pgup": "page up",
    "pgdn": "page down",
    "space": "space",
    "空格": "space",
    "ctrl": "ctrl",
    "control": "ctrl",
    "win": "windows",
    "cmd": "windows",
}

_MODIFIERS = ("ctrl", "alt", "shift", "windows")


def normalize_hotkey(text: str) -> str:
    """规范化快捷键写法：``"F8"`` → ``"f8"``，``"Ctrl+Shift+P"`` → ``"ctrl+shift+p"``。"""
    raw = (text or "").strip().lower().replace("＋", "+")
    if not raw:
        return ""
    parts = [p.strip() for p in raw.replace(" ", "").split("+") if p.strip()]
    if not parts:
        return ""
    normalized: List[str] = []
    for part in parts:
        part = _ALIASES.get(part, part)
        if part not in _MODIFIERS and len(part) == 1 and part.isalpha():
            part = part  # 单字母保持小写
        normalized.append(part)
    # 修饰键统一放前面，保证 ctrl+p 与 p+ctrl 视为同一个键
    mods = [p for p in normalized if p in _MODIFIERS]
    others = [p for p in normalized if p not in _MODIFIERS]
    ordered = sorted(set(mods), key=_MODIFIERS.index) + others
    return "+".join(ordered)


def find_conflicts(bindings: Dict[str, str]) -> List[str]:
    """返回快捷键冲突说明（同一个键被多个功能占用）。"""
    seen: Dict[str, str] = {}
    problems: List[str] = []
    for name, combo in bindings.items():
        normalized = normalize_hotkey(combo)
        if not normalized:
            problems.append("%s 没有设置快捷键" % HOTKEY_LABELS.get(name, name))
            continue
        if normalized in seen:
            problems.append(
                "快捷键 %s 被「%s」和「%s」同时占用"
                % (normalized, HOTKEY_LABELS.get(seen[normalized], seen[normalized]), HOTKEY_LABELS.get(name, name))
            )
        else:
            seen[normalized] = name
    return problems


class HotkeyManager:
    """封装 ``keyboard`` 库的注册/注销。

    **去抖（debounce）**：``keyboard`` 的单键热键是在**每一个 KEY_DOWN** 上回调的，
    包括 Windows 的按键自动重复；按 F8 稍微久一点、或按键抖动，回调就会连着来好几次。
    不去抖的话「开始/停止」会被切成一团（实测表现为：嵌套开始演奏 → 弹出
    「演奏已在进行中」的模态框）。所以同一个热键在 ``debounce_s`` 内只认第一次。
    """

    #: 同一个热键需要「安静」这么久才认下一次触发（秒）
    debounce_s = 0.3

    def __init__(self, on_message: Optional[Callable[[str, str], None]] = None) -> None:
        self.on_message = on_message
        self._module = None
        self._global_handles: List[object] = []
        self._session_handles: List[object] = []
        self._bindings: Dict[str, str] = {}
        self._last_fire: Dict[str, float] = {}

    # ------------------------------------------------------------------ #
    # 可用性
    # ------------------------------------------------------------------ #
    @staticmethod
    def probe() -> Tuple[bool, str]:
        try:
            import keyboard  # noqa: F401
        except Exception as exc:
            return False, "未安装 keyboard 库（pip install keyboard）：%s" % exc
        return True, ""

    def _report(self, level: str, text: str) -> None:
        if self.on_message:
            try:
                self.on_message(level, text)
            except Exception:
                pass

    # ------------------------------------------------------------------ #
    # 注册
    # ------------------------------------------------------------------ #
    def register(self, bindings: Dict[str, Callable[[], None]]) -> List[str]:
        """注册全局组快捷键，返回失败项说明。"""
        failures: List[str] = []
        ok, message = self.probe()
        if not ok:
            return [message]
        import keyboard

        self._module = keyboard
        for name, combo in self._bindings.items():
            if name in SESSION_HOTKEYS:
                continue
            callback = bindings.get(name)
            if callback is None or not combo:
                continue
            try:
                handle = keyboard.add_hotkey(
                    combo, self._wrap(name, callback), suppress=False, trigger_on_release=False
                )
                self._global_handles.append(handle)
            except Exception as exc:
                failures.append("快捷键 %s(%s) 注册失败：%s" % (HOTKEY_LABELS.get(name, name), combo, exc))
        return failures

    def set_bindings(self, bindings: Dict[str, str]) -> None:
        """更新快捷键配置（需要重新调用 :meth:`register` 生效）。"""
        self._bindings = {name: normalize_hotkey(combo) for name, combo in bindings.items()}

    def register_session(self, bindings: Dict[str, Callable[[], None]]) -> List[str]:
        """注册「仅演奏期间生效」的快捷键（Esc 退出演奏）。"""
        failures: List[str] = []
        if self._module is None:
            return failures
        self.unregister_session()
        for name in SESSION_HOTKEYS:
            combo = self._bindings.get(name, "")
            callback = bindings.get(name)
            if not combo or callback is None:
                continue
            try:
                handle = self._module.add_hotkey(combo, self._wrap(name, callback), suppress=False)
                self._session_handles.append(handle)
            except Exception as exc:
                failures.append("快捷键 %s(%s) 注册失败：%s" % (HOTKEY_LABELS.get(name, name), combo, exc))
        return failures

    def unregister_session(self) -> None:
        if self._module is None:
            self._session_handles.clear()
            return
        for handle in self._session_handles:
            try:
                self._module.remove_hotkey(handle)
            except Exception:
                pass
        self._session_handles.clear()

    def unregister_all(self) -> None:
        self.unregister_session()
        if self._module is None:
            self._global_handles.clear()
            return
        for handle in self._global_handles:
            try:
                self._module.remove_hotkey(handle)
            except Exception:
                pass
        self._global_handles.clear()

    # ------------------------------------------------------------------ #
    # 内部
    # ------------------------------------------------------------------ #
    def _wrap(self, name: str, callback: Callable[[], None]) -> Callable[[], None]:
        """包装回调：① 吞掉异常，② 吸收自动重复（连发）。

        去抖语义：**只有当距离上一次热键事件 ≥ debounce_s（也就是安静了这么久）
        才认这次触发**。按住 F8 时自动重复约每 33ms 来一次，会把时间戳一直刷新，
        于是整段按住期间只会触发一次；松手后再按（间隔超过 debounce_s）立刻生效。
        """

        def runner() -> None:
            stamp = time.perf_counter()
            previous = self._last_fire.get(name)
            self._last_fire[name] = stamp
            if previous is not None and stamp - previous < self.debounce_s:
                return
            try:
                callback()
            except Exception as exc:  # pragma: no cover - 防御式
                self._report("error", "快捷键 %s 处理出错：%s" % (name, exc))

        return runner

    def reset_debounce(self, name: Optional[str] = None) -> None:
        """清掉去抖时间戳（测试用，也让「下一次按键立即生效」）。"""
        if name is None:
            self._last_fire.clear()
        else:
            self._last_fire.pop(name, None)
