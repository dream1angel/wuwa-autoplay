# -*- coding: utf-8 -*-
"""阶段 4 测试：设置持久化与快捷键校验。"""

from __future__ import annotations

import json
import os

from core.config import AppConfig, default_settings_path
from core.hotkeys import (
    DEFAULT_HOTKEYS,
    HotkeyManager,
    find_conflicts,
    normalize_hotkey,
)
from core.notes import MODE_CHROMATIC, MODE_DIATONIC, Key

TMP_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_tmp")


def _tmp(name: str) -> str:
    os.makedirs(TMP_DIR, exist_ok=True)
    return os.path.join(TMP_DIR, name)


def test_defaults_are_clean() -> None:
    config = AppConfig()
    assert config.sanitize() == []
    assert config.backend == "pydirectinput"
    assert config.hotkeys == DEFAULT_HOTKEYS
    assert config.bpm == 120 and config.subdivision == 4


def test_hotkey_normalize() -> None:
    assert normalize_hotkey("F8") == "f8"
    assert normalize_hotkey(" Ctrl + Shift + P ") == "ctrl+shift+p"
    assert normalize_hotkey("shift+ctrl+p") == "ctrl+shift+p"
    assert normalize_hotkey("Escape") == "esc"
    assert normalize_hotkey("") == ""


def test_hotkey_conflicts() -> None:
    assert find_conflicts({"start": "f8", "pause": "f9", "emergency": "f10", "exit": "esc"}) == []
    problems = find_conflicts({"start": "F8", "pause": "f8", "emergency": "f10", "exit": "esc"})
    assert problems and "同时占用" in problems[0]
    empty = find_conflicts({"start": "", "pause": "f9", "emergency": "f10", "exit": "esc"})
    assert empty and "没有设置快捷键" in empty[0]


def test_sanitize_fixes_bad_values() -> None:
    config = AppConfig(
        bpm=999,
        subdivision=5,
        time_signature=(5, 4),
        speed=9.0,
        key_hold_ms=0,
        chord_stagger_ms=99,
        min_key_interval_ms=-5,
        countdown_s=42,
        backend="不存在的后端",
        hotkeys={"start": "f8", "pause": "f8", "emergency": "f10", "exit": "esc"},
    )
    notes = config.sanitize()
    assert config.bpm == 300 and config.subdivision == 4
    assert config.time_signature == (4, 4)
    assert config.speed == 2.0
    assert config.key_hold_ms == 30.0
    assert config.chord_stagger_ms == 1.5
    assert config.min_key_interval_ms == 25.0
    assert config.countdown_s == 3.0
    assert config.backend == "pydirectinput"
    assert any("同时占用" in note for note in notes)
    assert len(notes) >= 6


def test_from_json_roundtrip_and_bad_types() -> None:
    config = AppConfig(bpm=144, subdivision=8, speed=1.25, target_window_keyword="鸣潮|Wuthering Waves")
    config.hotkeys["start"] = "f6"
    config.keymap_overrides = {"1,1": "o"}
    config.remember_file(os.path.join(TMP_DIR, "曲谱.score.json"))

    loaded, warnings = AppConfig.from_json(config.to_json())
    assert warnings == []
    assert loaded.bpm == 144 and loaded.subdivision == 8
    assert abs(loaded.speed - 1.25) < 1e-9
    assert loaded.hotkeys["start"] == "f6"
    assert loaded.keymap_overrides == {"1,1": "o"}
    assert loaded.recent_files == config.recent_files

    broken, warnings = AppConfig.from_json({"bpm": "很快", "timeSignature": "4/4", "keyHoldMs": None})
    assert broken.bpm == 120 and broken.time_signature == (4, 4)
    assert len(warnings) >= 2

    fallback, warnings = AppConfig.from_json(["不是对象"])
    assert warnings and fallback.bpm == 120


def test_keymap_and_play_options() -> None:
    config = AppConfig(keymap_overrides={"1,0": "o"}, bpm=90, subdivision=8, speed=1.5)
    keymap = config.keymap()
    assert keymap.char_for(Key.from_degree(1, 1)) == "o"   # 高音1（半音 0）改到 o
    assert keymap.char_for(Key.from_degree(1, 2)) == "w"   # 其它音保持默认
    assert config.mode == MODE_DIATONIC
    config.chromatic_enabled = True
    assert config.mode == MODE_CHROMATIC
    assert config.keymap().stroke_for(Key(0, 1)) is not None   # 半音阶映射表可用

    options = config.play_options()
    assert options.speed == 1.5
    assert options.key_hold_ms == 30.0
    assert options.focus_keyword == config.target_window_keyword
    assert options.pause_on_focus_loss is True


def test_remember_file_orders_and_dedupes() -> None:
    config = AppConfig()
    for name in ("a.score.json", "b.score.json", "a.score.json"):
        config.remember_file(os.path.join(TMP_DIR, name))
    assert len(config.recent_files) == 2
    assert config.recent_files[0].endswith("a.score.json")
    assert config.last_dir == os.path.abspath(TMP_DIR)
    for index in range(12):
        config.remember_file(os.path.join(TMP_DIR, "f%d.score.json" % index))
    assert len(config.recent_files) == 8


def test_save_and_load_file() -> None:
    path = _tmp("settings.json")
    config = AppConfig(bpm=180, countdown_s=1.5, target_window_keyword="鸣潮")
    assert config.save(path) == os.path.abspath(path)
    payload = json.loads(open(path, "r", encoding="utf-8").read())
    assert payload["bpm"] == 180 and payload["countdownSeconds"] == 1.5

    fresh = AppConfig()
    assert fresh.load(path) == []
    assert fresh.bpm == 180 and fresh.countdown_s == 1.5
    assert fresh.target_window_keyword == "鸣潮"


def test_load_broken_file_keeps_defaults() -> None:
    path = _tmp("broken-settings.json")
    with open(path, "w", encoding="utf-8") as handle:
        handle.write("{ 这不是 JSON ")
    config = AppConfig()
    messages = config.load(path)
    assert messages and "不是合法 JSON" in messages[0]
    assert config.bpm == 120

    assert AppConfig().load(_tmp("根本不存在的设置.json")) == []


def test_default_settings_path_is_portable() -> None:
    path = default_settings_path()
    assert os.path.basename(path) == "settings.json"
    assert os.path.isdir(os.path.dirname(path))


def test_hotkey_manager_probe_and_graceful_failure() -> None:
    ok, message = HotkeyManager.probe()
    if not ok:
        print("    （未安装 keyboard 库：%s）" % message)
    manager = HotkeyManager()
    manager.set_bindings(DEFAULT_HOTKEYS)
    assert manager._bindings["mode"] == "f7"
    assert manager._bindings["start"] == "f8"
    # 未注册时注销不应报错
    manager.unregister_all()
    manager.unregister_session()
    # 转发包装器要吞掉回调异常
    wrapped = manager._wrap("start", lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    wrapped()


def test_hotkey_debounce_absorbs_auto_repeat() -> None:
    """回归：keyboard 库对按住的热键会随自动重复反复回调，必须只认第一次。

    实测 bug：F8 稍微按久一点 → 回调连发 → 嵌套开始演奏 → 弹出
    「演奏已在进行中，请先停止」的模态框（还藏在最小化的窗口后面）。
    """
    import time as _time

    manager = HotkeyManager()
    manager.debounce_s = 0.15
    hits: list = []
    runner = manager._wrap("start", lambda: hits.append(1))

    for _ in range(12):          # 模拟按住时的自动重复（每 ~10ms 一次）
        runner()
        _time.sleep(0.01)
    assert len(hits) == 1, "自动重复期间只应触发一次，实际 %d 次" % len(hits)

    _time.sleep(0.2)             # 松手、安静超过去抖窗口
    runner()
    assert len(hits) == 2, "安静之后应当能再次触发，实际 %d 次" % len(hits)

    # 不同热键之间互不影响
    other: list = []
    manager._wrap("pause", lambda: other.append(1))()
    assert len(other) == 1
    assert len(hits) == 2
