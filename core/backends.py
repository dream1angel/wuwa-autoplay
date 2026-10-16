# -*- coding: utf-8 -*-
"""按键后端抽象层。

三种实现
--------
* :class:`PydirectinputBackend`（默认）
  基于 ``SendInput`` + **扫描码**（KEYEVENTF_SCANCODE），兼容使用 DirectInput
  读键的游戏；比 pyautogui 可靠得多。
* :class:`InterceptionBackend`（备选开关）
  驱动级模拟，需要额外安装 Interception 驱动与 ``interception`` 包；
  未安装时 :meth:`available` 返回 False，界面上给出提示并自动回退，不会崩。
* :class:`DummyBackend`
  只记录事件时间戳，不碰键盘。用于「演练模式」以及计时精度的自动化测试。

统一约定：``key_down`` / ``key_up`` 的 ``char`` 都是小写按键名（``q``、``f1``…），
由 :class:`core.notes.KeyMap` 从「音区 + 音级」推导而来。
"""

from __future__ import annotations

import threading
import time
from abc import ABC, abstractmethod
from typing import Dict, Iterable, List, Optional, Tuple

__all__ = [
    "BackendError",
    "KeyBackend",
    "PydirectinputBackend",
    "InterceptionBackend",
    "DummyBackend",
    "BACKEND_CLASSES",
    "create_backend",
    "probe_backends",
    "backend_choices",
]


class BackendError(Exception):
    """按键后端不可用 / 初始化失败，消息面向用户（中文）。"""


class KeyBackend(ABC):
    """按键后端接口。"""

    #: 内部标识（写进 settings.json）
    name = "base"
    #: 界面显示名
    display_name = "基础后端"
    #: 是否需要管理员权限
    needs_admin = True

    def __init__(self) -> None:
        self._pressed: set = set()
        self._lock = threading.RLock()

    # ---- 能力探测 ------------------------------------------------------ #
    @classmethod
    def available(cls) -> Tuple[bool, str]:
        """返回 (是否可用, 说明)。"""
        return True, ""

    # ---- 生命周期 ------------------------------------------------------ #
    def open(self) -> None:
        """初始化（加载库、申请驱动句柄等）。失败时抛 :class:`BackendError`。"""

    def close(self) -> None:
        """释放资源，并尽力松开所有按下的键。"""
        self.release_all()

    # ---- 按键 ---------------------------------------------------------- #
    @abstractmethod
    def key_down(self, char: str) -> None:
        """按下按键。"""

    @abstractmethod
    def key_up(self, char: str) -> None:
        """抬起按键。"""

    # ---- 卡键兜底 ------------------------------------------------------ #
    @property
    def pressed(self) -> List[str]:
        with self._lock:
            return sorted(self._pressed)

    def release_all(self, extra: Optional[Iterable[str]] = None) -> None:
        """立即释放所有已按下的键（F10 紧急停止 / 异常退出时调用）。

        释放顺序有讲究：先抬**主键**，再按 ``ctrl → alt → shift`` 逆序抬修饰键。
        反过来的话，游戏可能在修饰键已经松开的瞬间把主键当成另一个键位。
        """
        with self._lock:
            targets = set(self._pressed)
            if extra:
                targets.update(extra)
        modifiers = [name for name in ("ctrl", "alt", "shift") if name in targets]
        mains = sorted(target for target in targets if target not in ("ctrl", "alt", "shift"))
        errors: List[str] = []
        for char in mains + modifiers:
            try:
                self.key_up(char)
            except Exception as exc:  # pragma: no cover - 兜底路径
                errors.append("%s: %s" % (char, exc))
        with self._lock:
            self._pressed.clear()
        if errors:  # pragma: no cover
            raise BackendError("释放按键失败：%s" % "; ".join(errors))

    def _mark_down(self, char: str) -> None:
        with self._lock:
            self._pressed.add(char)

    def _mark_up(self, char: str) -> None:
        with self._lock:
            self._pressed.discard(char)

    def describe(self) -> str:
        return self.display_name


# --------------------------------------------------------------------------- #
# pydirectinput（默认）
# --------------------------------------------------------------------------- #

class PydirectinputBackend(KeyBackend):
    """SendInput + 扫描码，兼容 DirectInput 游戏。"""

    name = "pydirectinput"
    display_name = "pydirectinput（SendInput + 扫描码，推荐）"

    def __init__(self) -> None:
        super().__init__()
        self._pdi = None

    @classmethod
    def available(cls) -> Tuple[bool, str]:
        try:
            import pydirectinput  # noqa: F401
        except Exception as exc:
            return False, "未安装 pydirectinput（pip install pydirectinput）：%s" % exc
        return True, ""

    def open(self) -> None:
        try:
            import pydirectinput
        except Exception as exc:
            raise BackendError("无法导入 pydirectinput：%s" % exc) from exc
        # FAILSAFE 会把鼠标移到左上角当作中止信号，在我们这种场景下是干扰
        pydirectinput.FAILSAFE = False
        # 关闭内部自带的随机延时，计时全部由本程序控制
        pydirectinput.PAUSE = 0
        self._pdi = pydirectinput

    def key_down(self, char: str) -> None:
        if self._pdi is None:
            raise BackendError("pydirectinput 后端尚未初始化")
        self._pdi.keyDown(char)
        self._mark_down(char)

    def key_up(self, char: str) -> None:
        if self._pdi is None:
            raise BackendError("pydirectinput 后端尚未初始化")
        self._pdi.keyUp(char)
        self._mark_up(char)


# --------------------------------------------------------------------------- #
# Interception（备选，驱动级）
# --------------------------------------------------------------------------- #

class InterceptionBackend(KeyBackend):
    """Interception 驱动后端。

    依赖 ``pip install interception`` + 安装 Interception 驱动（需重启）。
    未安装时不会影响程序运行，仅在设置页显示为不可用。
    """

    name = "interception"
    display_name = "Interception 驱动（内核级，需另装驱动）"

    def __init__(self) -> None:
        super().__init__()
        self._module = None

    @classmethod
    def available(cls) -> Tuple[bool, str]:
        try:
            import interception  # noqa: F401
        except Exception as exc:
            return False, "未安装 interception 包或驱动（pip install interception，并安装驱动后重启）：%s" % exc
        if not hasattr(interception, "key_down"):  # type: ignore[attr-defined]
            return False, "interception 包版本不兼容（缺少 key_down 接口）"
        return True, ""

    def open(self) -> None:
        ok, message = self.available()
        if not ok:
            raise BackendError(message)
        import interception

        # 自动捕获键盘设备：不同版本 API 略有差异，逐个尝试并给出中文提示
        try:
            interception.auto_capture_devices(keyboard=True, mouse=False)
        except TypeError:
            try:
                interception.auto_capture_devices(keyboard=True)
            except Exception as exc:
                raise BackendError("Interception 设备捕获失败（驱动是否已安装并重启？）：%s" % exc) from exc
        except Exception as exc:
            raise BackendError("Interception 设备捕获失败（驱动是否已安装并重启？）：%s" % exc) from exc
        self._module = interception

    def key_down(self, char: str) -> None:
        if self._module is None:
            raise BackendError("Interception 后端尚未初始化")
        self._module.key_down(char)
        self._mark_down(char)

    def key_up(self, char: str) -> None:
        if self._module is None:
            raise BackendError("Interception 后端尚未初始化")
        self._module.key_up(char)
        self._mark_up(char)


# --------------------------------------------------------------------------- #
# Dummy（演练 / 测试）
# --------------------------------------------------------------------------- #

class DummyBackend(KeyBackend):
    """不真正按键，只记录 ``(时间, 按键, 是否按下)``，用于演练与自动化测试。"""

    name = "dummy"
    display_name = "演练模式（不发送按键，仅用于试听节奏）"
    needs_admin = False

    def __init__(self) -> None:
        super().__init__()
        #: 事件列表：[(perf_counter 时间, 按键, True=按下/False=抬起)]
        self.events: List[Tuple[float, str, bool]] = []

    def key_down(self, char: str) -> None:
        self.events.append((time.perf_counter(), char, True))
        self._mark_down(char)

    def key_up(self, char: str) -> None:
        self.events.append((time.perf_counter(), char, False))
        self._mark_up(char)

    def clear(self) -> None:
        self.events.clear()

    def commands(self) -> List[Tuple[str, bool]]:
        """只取 (按键, 按下) 序列，便于断言顺序。"""
        return [(char, is_down) for _, char, is_down in self.events]


# --------------------------------------------------------------------------- #
# 工厂
# --------------------------------------------------------------------------- #

BACKEND_CLASSES: Dict[str, type] = {
    PydirectinputBackend.name: PydirectinputBackend,
    InterceptionBackend.name: InterceptionBackend,
    DummyBackend.name: DummyBackend,
}

DEFAULT_BACKEND = PydirectinputBackend.name


def backend_choices() -> List[Tuple[str, str]]:
    """返回 [(标识, 显示名)]，供设置页下拉框使用。"""
    return [(name, cls.display_name) for name, cls in BACKEND_CLASSES.items()]


def probe_backends() -> Dict[str, Tuple[bool, str]]:
    """探测全部后端可用性：{标识: (是否可用, 说明)}。"""
    result: Dict[str, Tuple[bool, str]] = {}
    for name, cls in BACKEND_CLASSES.items():
        try:
            result[name] = cls.available()
        except Exception as exc:  # pragma: no cover - 防御式
            result[name] = (False, "探测失败：%s" % exc)
    return result


def create_backend(name: str) -> KeyBackend:
    """按标识创建并初始化后端；失败抛 :class:`BackendError`（中文提示）。"""
    cls = BACKEND_CLASSES.get(name)
    if cls is None:
        raise BackendError("未知的按键后端：%r" % name)
    backend = cls()
    backend.open()
    return backend
