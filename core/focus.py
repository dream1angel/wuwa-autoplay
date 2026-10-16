# -*- coding: utf-8 -*-
"""前台窗口检测（失焦自动暂停用）。

只用 ``ctypes`` 调 user32 / kernel32，不引入 pywin32，减少打包体积与依赖冲突。
匹配规则：关键字与「窗口标题」或「进程可执行文件名」任一命中即算在目标窗口内；
关键字支持用 ``|`` 分隔多个候选，例如 ``Wuthering Waves|鸣潮|Client-Win64-Shipping``。
"""

from __future__ import annotations

import ctypes
import os
import sys
import time
from ctypes import wintypes
from typing import Callable, List, Optional, Tuple

__all__ = [
    "IS_WINDOWS",
    "foreground_window",
    "window_title",
    "window_process_name",
    "foreground_info",
    "split_keywords",
    "matches_keyword",
    "is_target_foreground",
    "FocusWatcher",
]

IS_WINDOWS = sys.platform == "win32"

if IS_WINDOWS:  # pragma: no cover - 平台相关
    _user32 = ctypes.WinDLL("user32", use_last_error=True)
    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
else:  # pragma: no cover - 开发机可能是别的系统
    _user32 = None
    _kernel32 = None
    _PROCESS_QUERY_LIMITED_INFORMATION = 0


def foreground_window() -> int:
    """返回前台窗口句柄；非 Windows 返回 0。"""
    if not IS_WINDOWS or _user32 is None:
        return 0
    try:
        return int(_user32.GetForegroundWindow())
    except Exception:  # pragma: no cover
        return 0


def window_title(hwnd: int) -> str:
    """返回窗口标题（失败返回空串）。"""
    if not IS_WINDOWS or _user32 is None or not hwnd:
        return ""
    try:
        length = int(_user32.GetWindowTextLengthW(wintypes.HWND(hwnd)))
        buffer = ctypes.create_unicode_buffer(max(1, length + 1))
        _user32.GetWindowTextW(wintypes.HWND(hwnd), buffer, len(buffer))
        return buffer.value or ""
    except Exception:  # pragma: no cover
        return ""


def window_process_name(hwnd: int) -> str:
    """返回窗口所属进程的可执行文件名（如 ``Client-Win64-Shipping.exe``）。"""
    if not IS_WINDOWS or _user32 is None or _kernel32 is None or not hwnd:
        return ""
    try:
        pid = wintypes.DWORD(0)
        _user32.GetWindowThreadProcessId(wintypes.HWND(hwnd), ctypes.byref(pid))
        if not pid.value:
            return ""
        handle = _kernel32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value)
        if not handle:
            return ""
        try:
            size = wintypes.DWORD(1024)
            buffer = ctypes.create_unicode_buffer(1024)
            if _kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
                return os.path.basename(buffer.value or "")
        finally:
            _kernel32.CloseHandle(handle)
    except Exception:  # pragma: no cover
        return ""
    return ""


def foreground_info() -> Tuple[int, str, str]:
    """一次性返回当前前台窗口的 (句柄, 标题, 进程名)。"""
    hwnd = foreground_window()
    return hwnd, window_title(hwnd), window_process_name(hwnd)


def split_keywords(keyword: str) -> List[str]:
    """把 ``"A|B;C"`` 拆成小写关键字列表。"""
    if not keyword:
        return []
    raw = keyword.replace("；", ";").replace("｜", "|")
    parts: List[str] = []
    for chunk in raw.replace(";", "|").split("|"):
        text = chunk.strip().lower()
        if text:
            parts.append(text)
    return parts


def matches_keyword(keyword: str, title: str = "", process: str = "") -> bool:
    """关键字是否命中标题或进程名。

    关键字为空时视为「不限制窗口」，直接返回 True。
    """
    keywords = split_keywords(keyword)
    if not keywords:
        return True
    haystack = ("%s %s" % (title, process)).lower()
    return any(word in haystack for word in keywords)


def is_target_foreground(keyword: str) -> bool:
    """当前前台窗口是否匹配关键字。无法检测时返回 True（不阻断演奏）。"""
    if not keyword:
        return True
    if not IS_WINDOWS:  # pragma: no cover - 非 Windows 开发环境
        return True
    hwnd, title, process = foreground_info()
    if not hwnd and not title:
        return True
    return matches_keyword(keyword, title, process)


class FocusWatcher:
    """按固定间隔轮询前台窗口，检测焦点进出目标窗口。

    ``on_lost`` / ``on_regained`` 只在状态变化时触发一次。

    **布防（armed）机制**：只有「至少看到过一次目标窗口」之后，
    离开目标窗口才会触发 ``on_lost``。
    这样做的原因（实测 bug）：
    程序启动演奏时会先最小化自己，前台窗口往往还不是游戏；如果这时就判定
    「失去了焦点」，会在第一个音之后立刻误暂停，而且用户手动继续后
    :class:`FocusWatcher` 的状态不再跳变，反而再也不会自动暂停了。
    另外关键字填错时也不会误暂停，而是安静地不生效。

    可注入 ``probe``（返回 ``(是否命中, 窗口描述)``）以便脱离真实桌面做单元测试。
    """

    def __init__(
        self,
        keyword: str,
        interval_ms: float = 200.0,
        on_lost: Optional[Callable[[str], None]] = None,
        on_regained: Optional[Callable[[str], None]] = None,
        probe: Optional[Callable[[], Tuple[bool, str]]] = None,
    ) -> None:
        self.keyword = keyword or ""
        self.interval_ms = max(50.0, float(interval_ms))
        self.on_lost = on_lost
        self.on_regained = on_regained
        self._probe = probe
        self._next_check = 0.0
        self._in_target = True
        self._armed = False

    @property
    def enabled(self) -> bool:
        return bool(self.keyword) and (IS_WINDOWS or self._probe is not None)

    @property
    def armed(self) -> bool:
        """是否已经见过目标窗口（没见过就不会因为「离开」而暂停）。"""
        return self._armed

    def reset(self) -> None:
        self._next_check = 0.0
        self._in_target = True
        self._armed = False

    def _sample(self) -> Tuple[bool, str]:
        """取一次当前前台窗口状态：(是否在目标窗口内, 窗口描述)。"""
        if self._probe is not None:
            return self._probe()
        hwnd, title, process = foreground_info()
        if not hwnd and not title:
            # 读不到窗口信息（权限受限等）时按「在目标窗口内」处理，宁可不暂停也不误暂停
            return True, ""
        return matches_keyword(self.keyword, title, process), (title or process or "未知窗口")

    def poll(self, force: bool = False) -> bool:
        """到点才真正检测，返回当前是否在目标窗口。"""
        if not self.enabled:
            return True
        stamp = time.perf_counter()
        if not force and stamp < self._next_check:
            return self._in_target
        self._next_check = stamp + self.interval_ms / 1000.0

        armed_before = self._armed
        current, detail = self._sample()
        if current:
            self._armed = True
        if current != self._in_target:
            self._in_target = current
            if current:
                # 只有「布防过」才提示「回来了」，避免首次识别成功就报一句莫名其妙的话
                if armed_before and self.on_regained:
                    self.on_regained(detail)
            elif armed_before:
                if self.on_lost:
                    self.on_lost(detail)
        return self._in_target

    def describe(self) -> str:
        if not self.enabled:
            return "未启用焦点检测"
        if not self._armed:
            return "焦点关键字：%s（还没看到过游戏窗口，暂不会自动暂停）" % self.keyword
        return "焦点关键字：%s（已在守护）" % self.keyword
