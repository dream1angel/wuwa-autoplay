# -*- coding: utf-8 -*-
"""命令行工具：不开界面也能校验曲谱、试听节奏、导出文本谱。

用法::

    python cli.py check  examples/小星星.score.json
    python cli.py play   examples/小星星.score.json --backend dummy
    python cli.py play   examples/小星星.score.json --backend pydirectinput --countdown 3
    python cli.py export-text examples/小星星.score.json
    python cli.py stats  examples/小星星.score.json --json

``play --backend dummy`` 只按节拍空跑（不碰键盘），适合在没有游戏的机器上验证计时；
``pydirectinput`` 才会真正发按键，请先切到游戏窗口。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from typing import List, Optional

ROOT = os.path.dirname(os.path.abspath(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from core.backends import BACKEND_CLASSES, BackendError, DummyBackend, create_backend  # noqa: E402
from core.clock import JitterTracker  # noqa: E402
from core.engine import EngineCallbacks, EngineState, PlayEngine, PlayOptions, build_plan, check_mapping  # noqa: E402
from core.model import Score, has_errors  # noqa: E402
from core.notes import MODE_DIATONIC, KeyMap, default_preset  # noqa: E402
from scoreio import json_io, text_io  # noqa: E402
from scoreio.json_io import ScoreIOError  # noqa: E402


def load_any(path: str, keymap: KeyMap) -> Score:
    if path.lower().endswith(".txt"):
        return text_io.load_text(path, keymap)
    return json_io.load_score(path)


def load_with_keymap(path: str):
    """读取曲谱，并按曲谱自身的音阶模式准备映射表（半音阶曲谱自动用 36 键表）。"""
    score = load_any(path, KeyMap(MODE_DIATONIC))
    keymap = KeyMap(score.meta.mode, score.meta.chromatic_preset or default_preset(score.meta.mode))
    return score, keymap


def cmd_check(args: argparse.Namespace) -> int:
    score, keymap = load_with_keymap(args.file)
    issues = score.validate(keymap)
    errors = [item for item in issues if item.level == "error"]
    warnings = [item for item in issues if item.level == "warning"]
    print("曲谱：%s" % score.meta.title)
    print(
        "BPM %d ｜ 拍号 %s ｜ 细分 1/%d拍 ｜ %s（%s） ｜ 音符 %d 个 ｜ 时长 %.1f 秒"
        % (
            score.meta.bpm,
            score.meta.time_signature_text,
            score.meta.subdivision,
            keymap.describe(),
            "含黑键" if score.has_sharps() else "无黑键",
            len(score.notes),
            score.total_ms() / 1000.0,
        )
    )
    for item in warnings:
        print("  [提示] %s" % item)
    for item in errors:
        print("  [错误] %s" % item)
    if errors:
        print("结论：有 %d 个错误，无法演奏" % len(errors))
        return 1
    missing = check_mapping(score, keymap)
    if missing:
        print("映射缺失：%s" % "、".join(key.label() for key in missing[:10]))
        return 1
    plan = build_plan(score, keymap, PlayOptions())
    print("可演奏：%s" % plan.describe())
    if plan.combo_count:
        print("其中 %d 个音使用组合键（修饰键事件 %d 个）" % (plan.combo_count, plan.modifier_count))
    return 0


def cmd_stats(args: argparse.Namespace) -> int:
    score, keymap = load_with_keymap(args.file)
    options = PlayOptions(
        speed=args.speed,
        key_hold_ms=args.hold,
        min_key_interval_ms=args.min_interval,
        countdown_s=0.0,
    )
    plan = build_plan(score, keymap, options)
    data = {
        "title": score.meta.title,
        "mode": score.meta.mode,
        "keymap": keymap.describe(),
        "bpm": score.meta.bpm,
        "timeSignature": list(score.meta.time_signature),
        "subdivision": score.meta.subdivision,
        "notes": plan.note_count,
        "chords": plan.chord_count,
        "sharps": len(score.sharp_keys()),
        "combos": plan.combo_count,
        "modifierEvents": plan.modifier_count,
        "events": plan.event_count,
        "bars": plan.bar_count,
        "mergedRepeats": plan.merged_count,
        "durationMs": round(plan.duration_ms, 3),
        "slotMs": round(plan.slot_ms, 6),
        "warnings": plan.warnings,
    }
    if args.json:
        print(json.dumps(data, ensure_ascii=False, indent=2))
    else:
        for key, value in data.items():
            print("%-14s %s" % (key, value))
    return 0


def cmd_export_text(args: argparse.Namespace) -> int:
    score, keymap = load_with_keymap(args.file)
    target = args.output or os.path.splitext(args.file)[0] + ".txt"
    path = text_io.save_text(score, target, keymap)
    print("已导出文本谱：%s" % path)
    return 0


def cmd_play(args: argparse.Namespace) -> int:
    score, keymap = load_with_keymap(args.file)
    issues = score.validate(keymap)
    if has_errors(issues):
        for item in issues:
            if item.level == "error":
                print("[错误] %s" % item)
        return 1

    options = PlayOptions(
        speed=args.speed,
        key_hold_ms=args.hold,
        chord_stagger_ms=args.chord_stagger,
        min_key_interval_ms=args.min_interval,
        countdown_s=args.countdown,
        focus_keyword=args.window or "",
        pause_on_focus_loss=bool(args.window),
    )
    plan = build_plan(score, keymap, options)
    if not plan.events:
        print("没有可演奏的音符")
        return 1
    print(plan.describe())
    backend = create_backend(args.backend)
    tracker = JitterTracker("实测调度")
    started = {"t0": 0.0, "base": None}

    def on_state(state: EngineState) -> None:
        print("状态：%s" % state.text)
        if state == EngineState.PLAYING:
            started["t0"] = time.perf_counter()

    def on_progress(elapsed: float, total: float, bar: int) -> None:
        bar_count = max(1, plan.bar_count)
        print("\r进度 %5.1f%%  小节 %d/%d" % (elapsed / max(1.0, total) * 100.0, bar + 1, bar_count), end="")

    engine = PlayEngine(
        backend,
        EngineCallbacks(on_state=on_state, on_progress=on_progress, on_message=lambda level, text: print("\n[%s] %s" % (level, text))),
    )
    if options.countdown_s > 0:
        print("倒计时 %.0f 秒，请切到游戏窗口…" % options.countdown_s)
    engine.start(plan, options)
    try:
        engine.wait()
    except KeyboardInterrupt:  # pragma: no cover - 手动中断
        engine.emergency_stop()
    print("\n演奏结束：%s" % engine.finish_reason)

    if isinstance(backend, DummyBackend) and backend.events:
        base = backend.events[0][0]
        for (stamp, _char, _down), expected in zip(backend.events, plan.events):
            tracker.add_relative(expected.time_ms, (stamp - base) * 1000.0)
        tracker.samples.pop(0)
        print(tracker.report())
    backend.close()
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="cli.py", description="鸣潮自动演奏器 命令行工具")
    sub = parser.add_subparsers(dest="command", required=True)

    def add_common(target: argparse.ArgumentParser) -> None:
        target.add_argument("file", help="曲谱文件（.score.json 或 .txt）")

    p_check = sub.add_parser("check", help="校验曲谱并打印可演奏性")
    add_common(p_check)
    p_check.set_defaults(func=cmd_check)

    p_stats = sub.add_parser("stats", help="打印展开后的按键事件统计")
    add_common(p_stats)
    p_stats.add_argument("--json", action="store_true", help="以 JSON 输出")
    p_stats.add_argument("--speed", type=float, default=1.0)
    p_stats.add_argument("--hold", type=float, default=30.0, help="按下-抬起间隔 ms")
    p_stats.add_argument("--min-interval", type=float, default=25.0, dest="min_interval")
    p_stats.set_defaults(func=cmd_stats)

    p_export = sub.add_parser("export-text", help="导出文本谱")
    add_common(p_export)
    p_export.add_argument("-o", "--output", default="")
    p_export.set_defaults(func=cmd_export_text)

    p_play = sub.add_parser("play", help="按节拍演奏（可先切到游戏窗口）")
    add_common(p_play)
    p_play.add_argument("--backend", default="dummy", choices=sorted(BACKEND_CLASSES.keys()))
    p_play.add_argument("--speed", type=float, default=1.0)
    p_play.add_argument("--hold", type=float, default=30.0, help="按下-抬起间隔 ms")
    p_play.add_argument("--chord-stagger", type=float, default=1.5, dest="chord_stagger", help="和弦内错开 ms")
    p_play.add_argument("--min-interval", type=float, default=25.0, dest="min_interval", help="同键最小间隔 ms")
    p_play.add_argument("--countdown", type=float, default=0.0, help="开始前倒计时秒数")
    p_play.add_argument("--window", default="", help="目标窗口关键字（用于失焦自动暂停）")
    p_play.set_defaults(func=cmd_play)
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except ScoreIOError as exc:
        print("[曲谱错误] %s" % exc)
        return 2
    except BackendError as exc:
        print("[后端错误] %s" % exc)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
