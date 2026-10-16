# -*- coding: utf-8 -*-
"""无界面自检（打包成 exe 后也能跑，用来验证整个程序是活的）。

用法::

    python main.py --selftest        # 开发环境
    wuwa-autoplay.exe --selftest     # 打包后

流程：构造主窗口 → 写入音符 → 撤销重做 → 复制粘贴 → 移调与音域检查 →
用「演练后端」跑一遍演奏 → 导出 PNG/PDF → 关闭。任何一步失败都会返回非 0。
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import time
from typing import Callable, List, Optional

from core.notes import MODE_CHROMATIC, MODE_DIATONIC, Key

__all__ = ["run_selftest"]


class _Tee:
    """把自检输出同时写到控制台与日志文件（打包成窗口程序后控制台看不见）。"""

    def __init__(self, *streams) -> None:
        self._streams = [stream for stream in streams if stream is not None]

    def write(self, text: str) -> int:
        for stream in self._streams:
            try:
                stream.write(text)
                stream.flush()
            except Exception:
                pass
        return len(text)

    def flush(self) -> None:
        for stream in self._streams:
            try:
                stream.flush()
            except Exception:
                pass


def _wait_until(predicate: Callable[[], bool], timeout: float = 30.0) -> bool:
    """空转事件循环直到条件成立（自检里没有交互，只能手动泵事件）。"""
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance()
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        if app is not None:
            app.processEvents()
        time.sleep(0.005)
    return predicate()


def run_selftest(verbose: bool = True) -> int:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

    # 打包成窗口程序后没有控制台，把输出同时写一份到 exe 旁边的 selftest.log
    log_handle = None
    original_stdout = sys.stdout
    if getattr(sys, "frozen", False):
        try:
            log_path = os.path.join(os.path.dirname(os.path.abspath(sys.executable)), "selftest.log")
            log_handle = open(log_path, "w", encoding="utf-8")
            sys.stdout = _Tee(original_stdout, log_handle)
        except Exception:
            log_handle = None

    try:
        from PySide6.QtWidgets import QApplication
    except Exception as exc:  # pragma: no cover
        print("[失败] 无法导入 PySide6：%s" % exc)
        _close_log(log_handle, original_stdout)
        return 2

    from ui.main_window import MainWindow
    from ui.theme import Theme

    app = QApplication.instance() or QApplication([])
    app.setStyleSheet(Theme.stylesheet())

    from core.config import AppConfig

    steps: List[str] = []

    def step(name: str, func: Callable[[], None]) -> None:
        func()
        steps.append(name)
        if verbose:
            print("  [通过] %s" % name)

    config = AppConfig(
        backend="dummy",
        countdown_s=0.0,
        target_window_keyword="",
        pause_on_focus_loss=False,
        # 自检不挂钩真实全局快捷键（免得占用用户的 F7/F8/F9/F10）
        enable_hotkeys=False,
    )
    window = MainWindow(config)
    window.resize(1200, 820)
    window.show()
    app.processEvents()

    try:
        step("主窗口构建完成", lambda: None)

        def build_twinkle() -> None:
            melody = [1, 1, 5, 5, 6, 6, 5, 4, 4, 3, 3, 2, 2, 1]
            window.grid.set_cursor(0)
            for index, degree in enumerate(melody):
                window.grid.set_cursor(index * 4)
                window.grid.insert_keys([Key.from_degree(0, degree)], advance=False)
            assert len(window.score.notes) == len(melody), len(window.score.notes)

        step("写入旋律", build_twinkle)

        def test_edit_ops() -> None:
            window.grid.set_cursor(0)
            window.grid.select_range(0, 15)      # 覆盖前 4 个音（4 格一拍）
            window.grid.copy_selection()
            window.grid.set_cursor(64)           # 空白处粘贴
            window.grid.paste_clipboard("merge")
            assert len(window.score.notes) == 18, len(window.score.notes)
            window._undo()
            assert len(window.score.notes) == 14, len(window.score.notes)
            window._redo()
            assert len(window.score.notes) == 18, len(window.score.notes)
            window._undo()
            window.grid.clear_selection()
            window.grid.set_cursor(2)            # 落在音符中间：应先截断延音再插入
            window.grid.insert_keys([Key.from_degree(1, 3)], advance=False)
            assert window.score.note_at(2.0) is not None
            window.grid.bump_duration(2)
            assert window.timeline.duration_at(2) >= 2
            window._undo()
            window._undo()

        step("编辑 / 撤销 / 重做", test_edit_ops)

        def test_transpose_and_range() -> None:
            window.grid.select_all()
            window.grid.transpose(octave_steps=1)
            bad = window.timeline.out_of_range_keys()
            assert all(not key.is_valid() for key in bad), bad
            window.grid.transpose(octave_steps=-1)
            assert window.timeline.out_of_range_keys() == []
            window.grid.clear_selection()
            window.grid.set_cursor(0)

        step("移调与音域校验", test_transpose_and_range)

        def test_validation() -> None:
            issues = window.score.validate(window.keymap)
            errors = [item for item in issues if item.level == "error"]
            assert not errors, [str(item) for item in errors]

        step("曲谱校验通过", test_validation)

        def test_chromatic_switch() -> None:
            assert window._switch_mode(MODE_CHROMATIC) is True
            assert window.mode == MODE_CHROMATIC
            assert len(window.keymap.keys()) == 36
            assert window.piano.mode == MODE_CHROMATIC
            window.grid.set_cursor(16)
            window.grid.insert_keys([Key(0, 1)])    # 中音#1（黑键）
            window.grid.insert_keys([Key(1, 6)])    # 高音#4（黑键）
            window.grid.insert_keys([Key(-1, 11)])  # 低音7
            issues = [item for item in window.score.validate(window.keymap) if item.level == "error"]
            assert not issues, [str(item) for item in issues]
            from core.engine import check_mapping

            assert check_mapping(window.score, window.keymap) == []

        step("切换到半音阶模式并写入黑键", test_chromatic_switch)

        def test_combo_expansion() -> None:
            from core.engine import build_plan

            window.config.chromatic_preset = "ff14-style"
            window._rebuild_keymap()
            plan = build_plan(window.score, window.keymap, window.config.play_options())
            assert plan.combo_count > 0, "FF14 预设应当产生组合键"
            assert any(event.is_modifier for event in plan.events), "缺少修饰键事件"
            downs = [event.char for event in plan.events if event.is_down and event.is_modifier]
            assert set(downs) <= {"ctrl", "alt", "shift"}, downs
            # 组合键的主键按下之前，必须先按下它需要的每一个修饰键
            for index, event in enumerate(plan.events):
                if event.is_down and event.modifiers:
                    prior = {e.char for e in plan.events[:index] if e.is_down and e.is_modifier}
                    for name in event.modifiers:
                        assert name in prior, "主键 %s 按下前应先按下修饰键 %s" % (event.char, name)
            window.config.chromatic_preset = "chromatic-36"
            window._rebuild_keymap()

        step("组合键展开与修饰键引用计数", test_combo_expansion)

        def test_input_gestures() -> None:
            """写谱手势：按住空格写和弦 + 按住 Shift 写升半音，且整段手势一步撤销。"""
            from PySide6.QtCore import QEvent, Qt
            from PySide6.QtGui import QKeyEvent

            def press(key, text, mods=Qt.KeyboardModifier.NoModifier) -> None:
                window.keyPressEvent(QKeyEvent(QEvent.Type.KeyPress, key, mods, text, False))

            def release(key, text="") -> None:
                window.keyReleaseEvent(
                    QKeyEvent(QEvent.Type.KeyRelease, key, Qt.KeyboardModifier.NoModifier, text, False)
                )

            if window.mode != MODE_CHROMATIC:
                window._switch_mode(MODE_CHROMATIC)
            assert window.mode == MODE_CHROMATIC
            # 用一个空白格子（旋律占 0~55 格）
            window.grid.set_cursor(60)
            press(Qt.Key.Key_Space, " ")
            assert window._chord_mode and window.grid._chord_mode
            press(Qt.Key.Key_A, "a")                                        # 中音1
            press(Qt.Key.Key_F, "f")                                        # 中音4
            press(Qt.Key.Key_G, "G", Qt.KeyboardModifier.ShiftModifier)     # 中音5 升半音
            assert window.grid.cursor == 60, "和弦输入期间光标不应前进"
            release(Qt.Key.Key_Space, " ")
            assert window.grid.cursor == 61, "松开空格后光标前进一格"
            keys = set(window.grid.timeline.cell_keys(60))
            assert keys == {Key(0, 0), Key(0, 5), Key(0, 8)}, keys
            window._undo()
            assert window.grid.timeline.cell_keys(60) == [], "整段手势应当一步撤销"

        step("按住空格写和弦 + Shift 写升半音", test_input_gestures)

        def test_back_to_diatonic() -> None:
            window.config.chromatic_downgrade_policy = "nearest"
            count_before = len(window.score.notes)
            assert window._switch_mode(MODE_DIATONIC) is True
            assert not window.score.has_sharps(), "切回自然阶后不应再有黑键"
            assert len(window.score.notes) == count_before
            window._undo()
            assert window.score.has_sharps(), "黑键降级应当可以撤销"
            window._redo()
            assert not window.score.has_sharps()

        step("切回自然阶（黑键就近归入，可撤销）", test_back_to_diatonic)

        def test_playback() -> None:
            window._start_play()
            assert window.engine is not None
            finished = _wait_until(lambda: not window.engine.is_busy, timeout=30.0)
            assert finished, "演奏没有在 30 秒内结束"
            assert window.backend is not None
            events = getattr(window.backend, "events", [])
            assert events, "演练后端没有收到任何按键事件"
            assert window.backend.pressed == [], "演奏结束后不应有按下的键"

        step("演练后端完整演奏一遍", test_playback)

        def test_export() -> None:
            from scoreio import render_io

            # 优先用系统临时目录；受限环境下退回到程序目录旁边的 _selftest_out
            candidates = []
            try:
                candidates.append(tempfile.mkdtemp(prefix="wuwa-selftest-"))
            except OSError:
                pass
            candidates.append(os.path.join(tempfile.gettempdir(), "wuwa-selftest"))
            local_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_selftest_out")
            candidates.append(local_dir)

            last_error: Optional[BaseException] = None
            for folder in candidates:
                try:
                    os.makedirs(folder, exist_ok=True)
                    png = render_io.export_png(window.score, os.path.join(folder, "selftest.png"), window.keymap)
                    pdf = render_io.export_pdf(window.score, os.path.join(folder, "selftest.pdf"), window.keymap)
                    assert os.path.getsize(png) > 1000, "PNG 内容太小"
                    assert os.path.getsize(pdf) > 500, "PDF 内容太小"
                    if verbose:
                        print("        导出于 %s" % folder)
                    return
                except Exception as exc:  # 换下一个目录再试
                    last_error = exc
            raise AssertionError("导出 PNG/PDF 失败：%s" % last_error)

        step("导出 PNG / PDF", test_export)

        def test_json_roundtrip() -> None:
            from scoreio import json_io

            payload = json_io.dumps_score(window.score)
            restored = json_io.loads_score(payload)
            assert len(restored.notes) == len(window.score.notes)

        step("曲谱 JSON 往返", test_json_roundtrip)

    except Exception as exc:  # pragma: no cover - 自检失败路径
        import traceback

        print("[失败] %s" % exc)
        traceback.print_exc()
        return 1
    finally:
        # 先把文档标记为已保存，避免 close() 弹出「要保存吗」的模态框把自检卡住
        try:
            window.undo.mark_saved()
            window._meta_dirty = False
            window.close()
        except Exception:
            pass
        shutil.rmtree(os.path.join(os.path.dirname(os.path.abspath(__file__)), "_selftest_out"), ignore_errors=True)

    print("自检完成：%d 项全部通过 ✅" % len(steps))
    _close_log(log_handle, original_stdout)
    return 0


def _close_log(log_handle, original_stdout) -> None:
    """恢复标准输出并关闭日志文件。"""
    if log_handle is not None:
        try:
            sys.stdout.flush()
        except Exception:
            pass
        sys.stdout = original_stdout
        try:
            log_handle.close()
            print("自检日志已写入 %s" % log_handle.name)
        except Exception:
            pass
