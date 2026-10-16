# -*- coding: utf-8 -*-
"""设置持久化（便携模式）。

* 配置文件固定放在**程序目录**下的 ``settings.json``（打包成 exe 后就在 exe 旁边），
  这样整套软件只占用一个文件夹，拷走即可用，不写注册表、不写 AppData；
* 读取时对每个字段做范围校验，非法值自动回退到默认并给出中文提示；
* 按键映射只保存与默认布局的差异（``keymapOverrides``），便于以后调整默认布局。
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from .backends import BACKEND_CLASSES, DEFAULT_BACKEND
from .engine import PlayOptions
from .hotkeys import DEFAULT_HOTKEYS, find_conflicts, normalize_hotkey
from .model import ALLOWED_SUBDIVISIONS, ALLOWED_TIME_SIGNATURES, BPM_MAX, BPM_MIN, SPEED_MAX, SPEED_MIN
from .notes import (
    DEGREE_TO_SEMITONE,
    MODE_CHROMATIC,
    MODE_DIATONIC,
    MODES,
    PRESET_CHROMATIC,
    PRESET_DIATONIC,
    KeyMap,
    default_preset,
    preset_names_for,
)

__all__ = [
    "SETTINGS_FILENAME",
    "AppConfig",
    "default_settings_path",
    "DOWNGRADE_POLICIES",
    "SHARP_MODIFIERS",
]

SETTINGS_FILENAME = "settings.json"
MAX_RECENT_FILES = 8

#: 半音阶 → 自然阶时的黑键处理策略
DOWNGRADE_POLICIES: Dict[str, str] = {
    "ask": "每次询问",
    "nearest": "就近归入下方自然音",
    "keep-disabled": "保留黑键但演奏时跳过",
}

#: 「按住修饰键写升半音」的修饰键选择。
#: 默认 Shift：Windows 与程序自身都没有占用 Shift+字母，
#: 而 Ctrl+字母 会和本程序的保存/撤销/复制等快捷键冲突（QAction 会先截走事件），
#: Alt+字母 会和菜单助记键、Alt+Tab 冲突。
SHARP_MODIFIERS: Dict[str, str] = {
    "shift": "Shift（推荐，无冲突）",
    "ctrl": "Ctrl（会与保存/撤销/复制等快捷键冲突）",
    "alt": "Alt（会与菜单助记键、Alt+Tab 冲突）",
    "none": "不设修饰键（改用 # 前缀输入）",
}


def default_settings_path() -> str:
    """settings.json 的目标路径（便携模式：与程序同目录）。"""
    if getattr(sys, "frozen", False):  # PyInstaller 打包后
        base = os.path.dirname(os.path.abspath(sys.executable))
    else:
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, SETTINGS_FILENAME)


def _migrate_degree_overrides(overrides: Dict[str, str]) -> Dict[str, str]:
    """把 v1 的 ``"音区,音级"`` 覆盖项迁移成 v2 的 ``"音区,半音"``。"""
    migrated: Dict[str, str] = {}
    for slot, stroke in overrides.items():
        try:
            octave, value = KeyMap.parse_slot(slot)
        except ValueError:
            continue
        if 1 <= value <= 7:  # 老格式里第二个数是音级
            value = DEGREE_TO_SEMITONE[value]
        migrated[KeyMap.slot_str(octave, value)] = stroke
    return migrated


@dataclass
class AppConfig:
    """全部可配置项。"""

    # 节拍
    bpm: int = 120
    time_signature: Tuple[int, int] = (4, 4)
    subdivision: int = 4
    speed: float = 1.0

    # 演奏手感
    key_hold_ms: float = 30.0
    chord_stagger_ms: float = 1.5
    min_key_interval_ms: float = 25.0
    countdown_s: float = 3.0

    # 按键与窗口
    backend: str = DEFAULT_BACKEND
    #: 默认覆盖国际服标题、国服标题与 UE 进程名，任一命中即算在游戏里
    target_window_keyword: str = "Wuthering Waves|鸣潮|Client-Win64-Shipping"
    pause_on_focus_loss: bool = True

    # 快捷键与按键映射
    hotkeys: Dict[str, str] = field(default_factory=lambda: dict(DEFAULT_HOTKEYS))
    keymap_overrides: Dict[str, str] = field(default_factory=dict)
    #: 是否启用全局快捷键（关掉后只能用界面按钮控制演奏）
    enable_hotkeys: bool = True

    # 半音阶模式（默认关闭，需用户主动开启）
    chromatic_enabled: bool = False
    chromatic_preset: str = PRESET_CHROMATIC
    chromatic_keymap: Dict[str, str] = field(default_factory=dict)
    #: 半音阶 → 自然阶时对黑键的处理：ask / nearest / keep-disabled
    chromatic_downgrade_policy: str = "ask"
    #: 「按住修饰键写升半音」用哪个修饰键：shift / ctrl / alt / none
    sharp_modifier: str = "shift"

    # 界面
    autoscroll: bool = True
    max_undo: int = 200
    last_dir: str = ""
    recent_files: List[str] = field(default_factory=list)

    #: 配置文件位置（不写进 JSON；留空时用程序目录下的 settings.json）
    settings_path: str = ""

    # ------------------------------------------------------------------ #
    # 序列化
    # ------------------------------------------------------------------ #
    def to_json(self) -> Dict[str, Any]:
        return {
            "version": 1,
            "bpm": int(self.bpm),
            "timeSignature": [int(self.time_signature[0]), int(self.time_signature[1])],
            "subdivision": int(self.subdivision),
            "speed": float(self.speed),
            "keyHoldMs": float(self.key_hold_ms),
            "chordStaggerMs": float(self.chord_stagger_ms),
            "minKeyIntervalMs": float(self.min_key_interval_ms),
            "countdownSeconds": float(self.countdown_s),
            "backend": self.backend,
            "targetWindowKeyword": self.target_window_keyword,
            "pauseOnFocusLoss": bool(self.pause_on_focus_loss),
            "hotkeys": dict(self.hotkeys),
            "keymapOverrides": dict(self.keymap_overrides),
            "enableHotkeys": bool(self.enable_hotkeys),
            "chromatic": {
                "enabled": bool(self.chromatic_enabled),
                "preset": self.chromatic_preset,
                "keymap": dict(self.chromatic_keymap),
                "downgradePolicy": self.chromatic_downgrade_policy,
                "sharpModifier": self.sharp_modifier,
            },
            "autoscroll": bool(self.autoscroll),
            "maxUndo": int(self.max_undo),
            "lastDir": self.last_dir,
            "recentFiles": list(self.recent_files)[:MAX_RECENT_FILES],
        }

    @staticmethod
    def from_json(data: object) -> Tuple["AppConfig", List[str]]:
        """从 JSON 构造配置；返回 (配置, 提示信息列表)。"""
        warnings: List[str] = []
        config = AppConfig()
        if not isinstance(data, dict):
            return config, ["settings.json 结构不对，已全部恢复默认值"]

        def _num(key: str, current: float, caster) -> float:
            if key not in data or data[key] is None:
                return current
            try:
                return caster(data[key])
            except (TypeError, ValueError):
                warnings.append("设置项 %s 的值无法识别，已用默认值 %s" % (key, current))
                return current

        config.bpm = int(_num("bpm", config.bpm, lambda v: int(float(v))))
        config.subdivision = int(_num("subdivision", config.subdivision, lambda v: int(float(v))))
        config.speed = float(_num("speed", config.speed, float))
        config.key_hold_ms = float(_num("keyHoldMs", config.key_hold_ms, float))
        config.chord_stagger_ms = float(_num("chordStaggerMs", config.chord_stagger_ms, float))
        config.min_key_interval_ms = float(_num("minKeyIntervalMs", config.min_key_interval_ms, float))
        config.countdown_s = float(_num("countdownSeconds", config.countdown_s, float))

        ts = data.get("timeSignature")
        if isinstance(ts, (list, tuple)) and len(ts) == 2:
            try:
                config.time_signature = (int(ts[0]), int(ts[1]))
            except (TypeError, ValueError):
                warnings.append("拍号格式不对，已用默认值 4/4")
        elif ts is not None:
            warnings.append("拍号格式不对，已用默认值 4/4")

        if isinstance(data.get("backend"), str):
            config.backend = data["backend"]
        if isinstance(data.get("targetWindowKeyword"), str):
            config.target_window_keyword = data["targetWindowKeyword"]
        if "pauseOnFocusLoss" in data:
            config.pause_on_focus_loss = bool(data["pauseOnFocusLoss"])
        if "autoscroll" in data:
            config.autoscroll = bool(data["autoscroll"])
        config.max_undo = int(_num("maxUndo", config.max_undo, lambda v: int(float(v))))

        if isinstance(data.get("hotkeys"), dict):
            merged = dict(DEFAULT_HOTKEYS)
            for name, combo in data["hotkeys"].items():
                if isinstance(combo, str) and name in DEFAULT_HOTKEYS:
                    merged[name] = normalize_hotkey(combo) or DEFAULT_HOTKEYS[name]
            config.hotkeys = merged
        if isinstance(data.get("keymapOverrides"), dict):
            raw_overrides = {
                str(k): str(v) for k, v in data["keymapOverrides"].items() if isinstance(v, str)
            }
            # v1 的覆盖项按「音区,音级」保存，v2 改为「音区,半音」；
            # 没有 chromatic 段说明是老配置，需要把 1~7 的音级换成对应半音
            if "chromatic" not in data:
                raw_overrides = _migrate_degree_overrides(raw_overrides)
            config.keymap_overrides = raw_overrides
        if "enableHotkeys" in data:
            config.enable_hotkeys = bool(data["enableHotkeys"])

        chromatic = data.get("chromatic")
        if isinstance(chromatic, dict):
            if "enabled" in chromatic:
                config.chromatic_enabled = bool(chromatic["enabled"])
            if isinstance(chromatic.get("preset"), str):
                config.chromatic_preset = chromatic["preset"]
            if isinstance(chromatic.get("keymap"), dict):
                config.chromatic_keymap = {
                    str(k): str(v) for k, v in chromatic["keymap"].items() if isinstance(v, str)
                }
            if isinstance(chromatic.get("downgradePolicy"), str):
                config.chromatic_downgrade_policy = chromatic["downgradePolicy"]
            if isinstance(chromatic.get("sharpModifier"), str):
                config.sharp_modifier = chromatic["sharpModifier"]
        if isinstance(data.get("lastDir"), str):
            config.last_dir = data["lastDir"]
        if isinstance(data.get("recentFiles"), list):
            config.recent_files = [str(p) for p in data["recentFiles"] if isinstance(p, str)][:MAX_RECENT_FILES]

        warnings.extend(config.sanitize())
        return config, warnings

    # ------------------------------------------------------------------ #
    # 校验
    # ------------------------------------------------------------------ #
    def sanitize(self) -> List[str]:
        """把越界值修正到合法范围，返回修正说明。"""
        notes: List[str] = []
        if not (BPM_MIN <= self.bpm <= BPM_MAX):
            notes.append("BPM %s 超出 %d~%d，已修正" % (self.bpm, BPM_MIN, BPM_MAX))
            self.bpm = min(BPM_MAX, max(BPM_MIN, int(self.bpm)))
        if self.subdivision not in ALLOWED_SUBDIVISIONS:
            notes.append("细分单位 %s 不受支持，已改为每拍 4 格" % self.subdivision)
            self.subdivision = 4
        if tuple(self.time_signature) not in ALLOWED_TIME_SIGNATURES:
            notes.append("拍号 %d/%d 不是预设值，已改为 4/4" % tuple(self.time_signature))
            self.time_signature = (4, 4)
        if not (SPEED_MIN <= self.speed <= SPEED_MAX):
            notes.append("变速 %.2fx 超出 %sx~%sx，已修正" % (self.speed, SPEED_MIN, SPEED_MAX))
            self.speed = min(SPEED_MAX, max(SPEED_MIN, float(self.speed)))
        if not (1 <= self.key_hold_ms <= 500):
            notes.append("按下-抬起间隔 %.0fms 不合理，已改为 30ms" % self.key_hold_ms)
            self.key_hold_ms = 30.0
        if not (0 <= self.chord_stagger_ms <= 20):
            notes.append("和弦错开间隔 %.1fms 不合理，已改为 1.5ms" % self.chord_stagger_ms)
            self.chord_stagger_ms = 1.5
        if not (0 <= self.min_key_interval_ms <= 500):
            notes.append("最小按键间隔 %.0fms 不合理，已改为 25ms" % self.min_key_interval_ms)
            self.min_key_interval_ms = 25.0
        if not (0 <= self.countdown_s <= 10):
            notes.append("倒计时 %.1f 秒不合理，已改为 3 秒" % self.countdown_s)
            self.countdown_s = 3.0
        if self.backend not in BACKEND_CLASSES:
            notes.append("按键后端 %r 不存在，已改用 %s" % (self.backend, DEFAULT_BACKEND))
            self.backend = DEFAULT_BACKEND
        if not (10 <= self.max_undo <= 2000):
            self.max_undo = 200
        merged = dict(DEFAULT_HOTKEYS)
        for name, combo in (self.hotkeys or {}).items():
            if name in DEFAULT_HOTKEYS:
                merged[name] = normalize_hotkey(str(combo)) or DEFAULT_HOTKEYS[name]
        self.hotkeys = merged

        # 半音阶设置
        if self.chromatic_preset not in preset_names_for(MODE_CHROMATIC):
            notes.append("半音阶预设 %r 未知，已改用 %s" % (self.chromatic_preset, default_preset(MODE_CHROMATIC)))
            self.chromatic_preset = default_preset(MODE_CHROMATIC)
        if self.chromatic_downgrade_policy not in DOWNGRADE_POLICIES:
            self.chromatic_downgrade_policy = "ask"
        if self.sharp_modifier not in SHARP_MODIFIERS:
            notes.append("升半音修饰键 %r 无效，已改用 Shift" % self.sharp_modifier)
            self.sharp_modifier = "shift"
        if not isinstance(self.chromatic_keymap, dict):
            self.chromatic_keymap = {}
        notes.extend(find_conflicts(self.hotkeys))
        return notes

    def hotkey_conflicts(self) -> List[str]:
        return find_conflicts(self.hotkeys)

    # ------------------------------------------------------------------ #
    # 派生对象
    # ------------------------------------------------------------------ #
    @property
    def mode(self) -> str:
        """当前音阶模式（半音阶开关默认关闭）。"""
        return MODE_CHROMATIC if self.chromatic_enabled else MODE_DIATONIC

    def keymap(self, mode: Optional[str] = None) -> KeyMap:
        """按当前模式（或指定模式）生成按键映射表，并叠加用户覆盖项。"""
        target = mode or self.mode
        if target == MODE_CHROMATIC:
            return KeyMap(MODE_CHROMATIC, self.chromatic_preset, self.chromatic_keymap)
        return KeyMap(MODE_DIATONIC, PRESET_DIATONIC, self.keymap_overrides)

    def store_keymap(self, keymap: KeyMap) -> None:
        """把设置页编辑过的映射表写回配置（只存与预设不同的项）。"""
        if keymap.mode == MODE_CHROMATIC:
            self.chromatic_preset = keymap.preset
            self.chromatic_keymap = keymap.overrides()
        else:
            self.keymap_overrides = keymap.overrides()

    def play_options(self) -> PlayOptions:
        """转换为演奏参数。"""
        return PlayOptions(
            speed=float(self.speed),
            key_hold_ms=float(self.key_hold_ms),
            chord_stagger_ms=float(self.chord_stagger_ms),
            min_key_interval_ms=float(self.min_key_interval_ms),
            countdown_s=float(self.countdown_s),
            focus_keyword=self.target_window_keyword,
            pause_on_focus_loss=bool(self.pause_on_focus_loss),
        )

    def remember_file(self, path: str) -> None:
        """记入最近打开列表。"""
        if not path:
            return
        target = os.path.abspath(path)
        self.last_dir = os.path.dirname(target)
        self.recent_files = [target] + [p for p in self.recent_files if os.path.abspath(p) != target]
        self.recent_files = self.recent_files[:MAX_RECENT_FILES]

    # ------------------------------------------------------------------ #
    # 读写
    # ------------------------------------------------------------------ #
    def load(self, path: Optional[str] = None) -> List[str]:
        """读取配置（不存在则保持默认），返回提示信息。"""
        target = path or self.settings_path or default_settings_path()
        if not os.path.exists(target):
            return []
        try:
            with open(target, "r", encoding="utf-8-sig") as handle:
                payload = json.load(handle)
        except json.JSONDecodeError as exc:
            return ["settings.json 不是合法 JSON（第 %d 行），已使用默认设置" % exc.lineno]
        except OSError as exc:
            return ["读取 settings.json 失败：%s，已使用默认设置" % exc]
        loaded, warnings = AppConfig.from_json(payload)
        settings_path = self.settings_path
        self.__dict__.update(loaded.__dict__)
        self.settings_path = settings_path
        return warnings

    def save(self, path: Optional[str] = None) -> str:
        """写入配置，返回实际路径。"""
        target = path or self.settings_path or default_settings_path()
        directory = os.path.dirname(target) or "."
        try:
            os.makedirs(directory, exist_ok=True)
            with open(target, "w", encoding="utf-8", newline="\n") as handle:
                json.dump(self.to_json(), handle, ensure_ascii=False, indent=2)
                handle.write("\n")
        except OSError as exc:
            raise OSError("保存设置失败：%s（%s）" % (target, exc)) from exc
        return target

    def clone(self) -> "AppConfig":
        data, _ = AppConfig.from_json(self.to_json())
        return data
