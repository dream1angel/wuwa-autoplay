# -*- coding: utf-8 -*-
"""阶段 4 测试：GUI 冒烟测试（离屏运行，不会弹窗、不会真的按键）。

用 ``QT_QPA_PLATFORM=offscreen`` 在没有显示器的环境下也能跑；
覆盖：窗口构建、键盘输入、编辑/撤销、选区操作、越界标红、演练演奏、
设置对话框、倒计时遮罩。
"""

from __future__ import annotations

import os
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from core.config import AppConfig  # noqa: E402
from core.engine import EngineState  # noqa: E402
from core.notes import Key  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

TMP_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_tmp")

_APP = None
#: 被守卫拦下的模态框：[(方法名, 文本)]
POPUPS: list = []


def _install_modal_guard() -> None:
    """把所有模态框换成「记录 + 立即返回」，避免离屏测试被对话框永久阻塞。

    （离屏平台没有真人点「确定」，真的弹出来就会把整个测试进程卡死。）
    需要断言弹窗内容的用例可以在自己内部临时再包一层，用完恢复即可。
    """
    from PySide6.QtWidgets import QMessageBox

    if getattr(QMessageBox, "_dsh_guard_installed", False):
        return
    for name in ("warning", "information", "critical", "question"):
        original = getattr(QMessageBox, name)

        def make(method_name, _original):
            def patched(*args, **kwargs):
                text = args[2] if len(args) > 2 else (args[1] if len(args) > 1 else "")
                POPUPS.append((method_name, str(text)))
                return (
                    QMessageBox.StandardButton.Yes
                    if method_name == "question"
                    else QMessageBox.StandardButton.Ok
                )

            return patched

        setattr(QMessageBox, name, staticmethod(make(name, original)))
    QMessageBox._dsh_guard_installed = True


def _app():
    global _APP
    from PySide6.QtWidgets import QApplication

    if _APP is None:
        _APP = QApplication.instance() or QApplication([])
    return _APP


def _qt_ok() -> bool:
    try:
        import PySide6.QtWidgets  # noqa: F401
    except Exception as exc:
        print("    （未安装 PySide6，跳过 GUI 测试：%s）" % exc)
        return False
    return True


def _config(**kwargs) -> AppConfig:
    os.makedirs(TMP_DIR, exist_ok=True)
    options = dict(
        backend="dummy",
        countdown_s=0.0,
        target_window_keyword="",
        pause_on_focus_loss=False,
        # 测试时不挂钩系统键盘：避免真的注册 F8/F9/F10
        enable_hotkeys=False,
        settings_path=os.path.join(TMP_DIR, "settings.json"),
    )
    options.update(kwargs)
    config = AppConfig(**options)
    config.sanitize()
    return config


def _window(**kwargs):
    from ui.main_window import MainWindow

    _app()
    _install_modal_guard()
    POPUPS.clear()
    window = MainWindow(_config(**kwargs))
    window.resize(1180, 820)
    window.show()
    _app().processEvents()
    return window


def _close(window) -> None:
    """干净地关掉窗口：先标记已保存，避免弹出“要保存吗”的对话框。"""
    window.undo.mark_saved()
    window._meta_dirty = False
    window.close()
    _app().processEvents()


def _pump(predicate, timeout: float = 20.0) -> bool:
    app = _app()
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        app.processEvents()
        time.sleep(0.005)
    return predicate()


def _key_event(kind, key, text: str = "", modifiers=None, auto_repeat: bool = False):
    from PySide6.QtCore import QEvent, Qt
    from PySide6.QtGui import QKeyEvent

    return QKeyEvent(
        kind,
        key,
        modifiers if modifiers is not None else Qt.KeyboardModifier.NoModifier,
        text,
        auto_repeat,
    )


def _press(window, key, text: str = "", modifiers=None, auto_repeat: bool = False) -> None:
    from PySide6.QtCore import QEvent

    window.keyPressEvent(_key_event(QEvent.Type.KeyPress, key, text, modifiers, auto_repeat))
    _app().processEvents()


def _release(window, key, text: str = "", modifiers=None, auto_repeat: bool = False) -> None:
    from PySide6.QtCore import QEvent

    window.keyReleaseEvent(_key_event(QEvent.Type.KeyRelease, key, text, modifiers, auto_repeat))
    _app().processEvents()


def _tap(window, key, text: str = "", modifiers=None) -> None:
    """按一下再松开（空格这类「按住 = 另一种行为」的键必须成对发）。"""
    _press(window, key, text, modifiers)
    _release(window, key, text, modifiers)


def _send_to_window(window, kind, key, text: str = "", modifiers=None):
    """按**真实链路**投递按键：投给顶层窗口 → Qt 自己派发给焦点控件并逐级冒泡。

    直接调用 ``window.keyPressEvent()`` 会绕过 QScrollArea 之类的中间控件，
    曾经因此漏掉过「方向键被滚动区吃掉、选中框根本不动」的 bug。
    """
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QKeyEvent
    from PySide6.QtWidgets import QApplication

    event = QKeyEvent(
        kind, key, modifiers if modifiers is not None else Qt.KeyboardModifier.NoModifier, text
    )
    QApplication.sendEvent(window.windowHandle(), event)
    _app().processEvents()
    return event


# --------------------------------------------------------------------------- #
# 构建与输入
# --------------------------------------------------------------------------- #

def test_main_window_builds() -> None:
    if not _qt_ok():
        return
    window = _window()
    assert window.grid is not None and window.transport is not None and window.piano is not None
    assert window.score.meta.bpm == 120
    assert window.grid.cursor == 0
    assert window.validation_label.text(), "状态栏应显示校验结果"
    _close(window)


def test_keyboard_input_creates_notes() -> None:
    if not _qt_ok():
        return
    from PySide6.QtCore import Qt

    window = _window()
    _press(window, Qt.Key.Key_Q, "q")
    _press(window, Qt.Key.Key_S, "s")
    _tap(window, Qt.Key.Key_Space, " ")      # 空格：按一下 = 休止符
    _press(window, Qt.Key.Key_Z, "z")

    keys = [note.keys for note in window.score.notes]
    assert keys == [[Key.from_degree(1, 1)], [Key.from_degree(0, 2)], [Key.from_degree(-1, 1)]], keys
    assert window.grid.cursor == 4, window.grid.cursor
    assert window.undo.can_undo

    # 同一格继续敲音 → 和弦
    window.grid.set_cursor(0)
    _press(window, Qt.Key.Key_H, "h")
    assert set(window.grid.timeline.cell_keys(0)) == {Key.from_degree(1, 1), Key.from_degree(0, 6)}

    # 数字键 = 中音
    window.grid.set_cursor(8)
    _press(window, Qt.Key.Key_5, "5")
    assert window.grid.timeline.cell_keys(8) == [Key.from_degree(0, 5)]
    _close(window)


def test_shift_modifier_writes_sharp() -> None:
    """按住 Shift + 音键 = 写升半音（默认手势，与快捷键不冲突）。"""
    from PySide6.QtCore import Qt

    from core.notes import MODE_CHROMATIC

    window = _window()
    window._switch_mode(MODE_CHROMATIC)
    assert window.config.sharp_modifier == "shift"

    window.grid.set_cursor(0)
    _press(window, Qt.Key.Key_S, "S", Qt.KeyboardModifier.ShiftModifier)   # 中音2 → 中音#2
    assert window.grid.timeline.cell_keys(0) == [Key(0, 3)], window.grid.timeline.cell_keys(0)

    # Shift + 数字键：text() 会是符号，也必须还原成主键（Shift+4 → 中音3）
    window.grid.set_cursor(1)
    _press(window, Qt.Key.Key_4, "$", Qt.KeyboardModifier.ShiftModifier)
    assert window.grid.timeline.cell_keys(1) == [Key(0, 4)], window.grid.timeline.cell_keys(1)

    # E / B 没有升半音 → 按等音处理（E# = F）
    window.grid.set_cursor(2)
    _press(window, Qt.Key.Key_D, "D", Qt.KeyboardModifier.ShiftModifier)   # 中音3(E) → 中音4(F)
    assert window.grid.timeline.cell_keys(2) == [Key(0, 5)], window.grid.timeline.cell_keys(2)

    # 自然阶模式下按 Shift 只提示，不写入
    window.config.chromatic_downgrade_policy = "nearest"   # 避免弹「黑键怎么处理」的对话框
    window._switch_mode("diatonic")
    window.grid.set_cursor(4)
    _press(window, Qt.Key.Key_S, "S", Qt.KeyboardModifier.ShiftModifier)
    assert window.grid.timeline.cell_keys(4) == []
    _close(window)


def test_sharp_modifier_configurable_to_ctrl() -> None:
    """修饰键可配置：改成 Ctrl 时 Ctrl+音键写升半音（同时设置页会给出冲突警告）。"""
    from PySide6.QtCore import Qt

    from core.notes import MODE_CHROMATIC

    window = _window(sharp_modifier="ctrl")
    window._switch_mode(MODE_CHROMATIC)
    window.grid.set_cursor(0)
    _press(window, Qt.Key.Key_S, "s", Qt.KeyboardModifier.ControlModifier)
    assert window.grid.timeline.cell_keys(0) == [Key(0, 3)], window.grid.timeline.cell_keys(0)
    # 不按修饰键还是普通音
    window.grid.set_cursor(1)
    _press(window, Qt.Key.Key_S, "s")
    assert window.grid.timeline.cell_keys(1) == [Key(0, 2)]
    _close(window)


def test_space_hold_writes_chord() -> None:
    """按住空格不放：敲多个音全部进同一格，松开空格光标才前进。"""
    from PySide6.QtCore import Qt

    from core.notes import MODE_CHROMATIC

    window = _window()
    window._switch_mode(MODE_CHROMATIC)
    window.grid.set_cursor(0)

    _press(window, Qt.Key.Key_Space, " ")
    assert window._chord_mode is True
    assert window.grid._chord_mode is True, "网格应显示和弦输入状态"

    _press(window, Qt.Key.Key_A, "a")        # 中音1 → semitone 0
    _press(window, Qt.Key.Key_F, "f")        # 中音4 → semitone 5
    # 和弦模式下按住 Shift 也能加黑键
    _press(window, Qt.Key.Key_G, "G", Qt.KeyboardModifier.ShiftModifier)   # 中音5 → 升半音 = semitone 8

    assert window.grid.cursor == 0, "和弦输入期间光标不应前进"
    assert set(window.grid.timeline.cell_keys(0)) == {Key(0, 0), Key(0, 5), Key(0, 8)}

    _release(window, Qt.Key.Key_Space, " ")
    assert window._chord_mode is False
    assert window.grid._chord_mode is False
    assert window.grid.cursor == 1, "松开空格后光标前进一格"
    assert set(window.grid.timeline.cell_keys(0)) == {Key(0, 0), Key(0, 5), Key(0, 8)}

    # 撤销应当把整格和弦作为一步撤回
    window._undo()
    assert window.grid.timeline.cell_keys(0) == []
    _close(window)


def test_space_tap_is_rest_and_autorepeat_ignored() -> None:
    """单击空格 = 休止符；按住时的自动重复事件必须被忽略。"""
    from PySide6.QtCore import Qt

    window = _window()
    window.grid.set_cursor(0)
    _press(window, Qt.Key.Key_Q, "q")        # 高音1
    window.grid.set_cursor(0)

    # 自动重复的空格按下不应进入/重复进入和弦模式，也不应立刻清格
    _press(window, Qt.Key.Key_Space, " ", auto_repeat=True)
    assert window.grid.timeline.cell_keys(0) == [Key.from_degree(1, 1)], "自动重复不应触发休止符"

    _tap(window, Qt.Key.Key_Space, " ")      # 真正的单击
    assert window.grid.timeline.cell_keys(0) == []
    assert window.grid.cursor == 1
    _close(window)


def test_chord_mode_toggle_and_right_click_piano() -> None:
    """和弦模式里重复按同一个音 = 移除；虚拟键盘右键也能加入/移出当前格。"""
    from PySide6.QtCore import Qt

    from core.notes import MODE_CHROMATIC

    window = _window()
    window._switch_mode(MODE_CHROMATIC)
    window.grid.set_cursor(0)
    _press(window, Qt.Key.Key_Space, " ")
    _press(window, Qt.Key.Key_A, "a")
    _press(window, Qt.Key.Key_A, "a")        # 再按一次 → 移除
    assert window.grid.timeline.cell_keys(0) == []
    _press(window, Qt.Key.Key_F, "f")        # 中音4 → semitone 5
    _release(window, Qt.Key.Key_Space, " ")
    assert window.grid.timeline.cell_keys(0) == [Key(0, 5)]

    # 右键点琴键：加入 / 移出，且不前进光标
    window.grid.set_cursor(4)
    window.piano.keyToggled.emit(Key.from_degree(0, 1))
    assert window.grid.timeline.cell_keys(4) == [Key(0, 0)]
    assert window.grid.cursor == 4
    window.piano.keyToggled.emit(Key.from_degree(0, 1))
    assert window.grid.timeline.cell_keys(4) == []

    # 左键点击仍然前进一格
    window.piano.keyClicked.emit(Key.from_degree(0, 2))
    assert window.grid.cursor == 5
    assert window.grid.timeline.cell_keys(4) == [Key(0, 2)]
    _close(window)


def test_arrow_keys_move_selection_box_through_real_event_chain() -> None:
    """回归（用户实测）：真实使用时方向键必须能移动选中框。

    QAbstractScrollArea 会在 keyPressEvent 里把方向键吃掉用于滚动画布，
    事件到不了主窗口 —— 鼠标点过网格之后方向键就失效了。
    这里走真实事件链路（投给顶层窗口 → 焦点控件 → 冒泡），确保按键真的能到。
    """
    from PySide6.QtCore import QEvent, Qt

    window = _window()
    window.grid.setFocus()
    _app().processEvents()
    assert QApplication.focusWidget() is window.grid

    _send_to_window(window, QEvent.Type.KeyPress, Qt.Key.Key_Right)
    assert window.grid.cursor == 1, "方向键没有到达主窗口（被滚动区吃掉了？）"
    _send_to_window(window, QEvent.Type.KeyPress, Qt.Key.Key_Left)
    assert window.grid.cursor == 0
    _send_to_window(window, QEvent.Type.KeyPress, Qt.Key.Key_Left)
    assert window.grid.cursor == 0, "左边界不能越过第 0 格"

    row_step = window.grid._bars_per_row() * window.grid.slots_per_bar
    _send_to_window(window, QEvent.Type.KeyPress, Qt.Key.Key_Down)
    assert window.grid.cursor == row_step, "↓ 应移动一整行"
    _send_to_window(window, QEvent.Type.KeyPress, Qt.Key.Key_Up)
    assert window.grid.cursor == 0

    # 有选区时：方向键平移整个选中框，框内音符不动
    window.grid.set_cursor(0)
    window.grid.insert_keys([Key.from_degree(0, 1)], advance=False)
    window.grid.select_range(0, 3)
    _send_to_window(window, QEvent.Type.KeyPress, Qt.Key.Key_Right)
    assert window.grid.selection_range() == (1, 4)
    assert window.grid.timeline.cell_keys(0) == [Key.from_degree(0, 1)], "移动框不应改动音符"
    _send_to_window(window, QEvent.Type.KeyPress, Qt.Key.Key_Down)
    assert window.grid.selection_range() == (1 + row_step, 4 + row_step)
    _send_to_window(window, QEvent.Type.KeyPress, Qt.Key.Key_Up)
    assert window.grid.selection_range() == (1, 4)

    # Esc 取消选区后，方向键恢复为移动光标
    _send_to_window(window, QEvent.Type.KeyPress, Qt.Key.Key_Escape)
    assert window.grid.selection_range() is None
    before = window.grid.cursor
    _send_to_window(window, QEvent.Type.KeyPress, Qt.Key.Key_Right)
    assert window.grid.cursor == before + 1
    _close(window)


def test_selection_translation_clamps_at_bounds() -> None:
    """选中框平移到左边界就停住，不会跑到负格。"""
    window = _window()
    window.grid.select_range(2, 5)
    assert window.grid.translate_selection(-9) is True       # 贴住第 0 格
    assert window.grid.selection_range() == (0, 3)
    assert window.grid.translate_selection(-1) is False      # 已经在边界，不再移动
    assert window.grid.selection_range() == (0, 3)
    assert window.grid.translate_selection(2) is True
    assert window.grid.selection_range() == (2, 5)
    assert window.grid.translate_selection(0) is False       # 没有位移
    window.grid.clear_selection()
    assert window.grid.translate_selection(1) is False       # 没有选区
    _close(window)


def test_space_chord_gesture_through_real_event_chain() -> None:
    """回归：空格的按下/松开也必须能穿过滚动区到达主窗口（和弦手势依赖它）。"""
    from PySide6.QtCore import QEvent, Qt

    from core.notes import MODE_CHROMATIC

    window = _window()
    window._switch_mode(MODE_CHROMATIC)
    window.grid.setFocus()
    window.grid.set_cursor(0)
    _app().processEvents()

    _send_to_window(window, QEvent.Type.KeyPress, Qt.Key.Key_Space, " ")
    assert window._chord_mode is True
    _send_to_window(window, QEvent.Type.KeyPress, Qt.Key.Key_A, "a")
    _send_to_window(window, QEvent.Type.KeyPress, Qt.Key.Key_F, "f")
    _send_to_window(window, QEvent.Type.KeyRelease, Qt.Key.Key_Space, " ")
    assert window._chord_mode is False
    assert set(window.grid.timeline.cell_keys(0)) == {Key(0, 0), Key(0, 5)}
    assert window.grid.cursor == 1
    _close(window)


def test_jianpu_notation_drawing_and_grid_render() -> None:
    """格内符号统一为简谱（数字 + 上下点）：绘制能落笔，两种模式网格都能渲染。"""
    from PySide6.QtCore import QRect
    from PySide6.QtGui import QColor, QFont, QImage, QPainter

    from core.notes import MODE_CHROMATIC, MODE_DIATONIC
    from ui.notation import draw_jianpu

    _app()
    image = QImage(80, 80, QImage.Format.Format_ARGB32)
    image.fill(QColor("black"))
    painter = QPainter(image)
    try:
        font = QFont("Consolas", 16)
        draw_jianpu(painter, QRect(0, 0, 80, 80), Key.from_degree(1, 1), font=font, color=QColor("white"))
    finally:
        painter.end()
    # 逐像素统计（八度点只有几像素宽，抽样扫描会漏掉）
    ink = sum(
        1 for x in range(80) for y in range(80) if image.pixelColor(x, y) != QColor("black")
    )
    assert ink > 20, "简谱符号（数字 + 八度点）应该真的画上去了，实际墨点 %d" % ink

    for mode in (MODE_DIATONIC, MODE_CHROMATIC):
        window = _window()
        if window.mode != mode:
            window._switch_mode(mode)
        window.grid.set_cursor(0)
        # 高音 / 中音 / 低音 三种八度点 + 一个黑键
        for key in (
            Key.from_degree(1, 1),
            Key.from_degree(0, 5),
            Key.from_degree(-1, 3),
            Key(0, 1),
        ):
            window.grid.insert_keys([key], advance=False)
        pixmap = window.grid.grab()
        assert not pixmap.isNull() and pixmap.width() > 0
        _close(window)


def test_duration_and_delete_keys() -> None:
    if not _qt_ok():
        return
    from PySide6.QtCore import Qt

    window = _window()
    window.grid.set_cursor(0)
    _press(window, Qt.Key.Key_Q, "q")
    window.grid.set_cursor(0)
    _press(window, Qt.Key.Key_BracketRight, "]")
    _press(window, Qt.Key.Key_BracketRight, "]")
    assert window.grid.timeline.duration_at(0) == 3

    _press(window, Qt.Key.Key_BracketLeft, "[")
    assert window.grid.timeline.duration_at(0) == 2

    _press(window, Qt.Key.Key_Delete)
    assert window.score.notes == []
    _close(window)


def test_cursor_movement_and_selection() -> None:
    if not _qt_ok():
        return
    from PySide6.QtCore import Qt

    window = _window()
    window.grid.set_cursor(0)
    for degree in (1, 2, 3, 4):
        _press(window, Qt.Key.Key_Q, "q")
    window.grid.set_cursor(0)
    _press(window, Qt.Key.Key_Right)
    assert window.grid.cursor == 1
    _press(window, Qt.Key.Key_Down)
    assert window.grid.cursor == 1 + window.grid._bars_per_row() * window.grid.slots_per_bar

    window.grid.set_cursor(0)
    _press(window, Qt.Key.Key_Right, modifiers=Qt.KeyboardModifier.ShiftModifier)
    _press(window, Qt.Key.Key_Right, modifiers=Qt.KeyboardModifier.ShiftModifier)
    assert window.grid.selection_range() == (0, 2)
    _close(window)


def test_copy_paste_undo_redo() -> None:
    if not _qt_ok():
        return
    window = _window()
    window.grid.set_cursor(0)
    window.grid.insert_keys([Key.from_degree(0, 1)])
    window.grid.insert_keys([Key.from_degree(0, 2)])
    window.grid.insert_keys([Key.from_degree(0, 3)])
    assert len(window.score.notes) == 3

    window.grid.set_cursor(0)
    window.grid.select_range(0, 2)
    window.grid.copy_selection()
    window.grid.set_cursor(8)
    window.grid.paste_clipboard("merge")
    assert len(window.score.notes) == 6
    assert window.grid.timeline.cell_keys(8) == [Key.from_degree(0, 1)]

    window._undo()
    assert len(window.score.notes) == 3
    window._redo()
    assert len(window.score.notes) == 6

    # 剪切
    window.grid.set_cursor(8)
    window.grid.select_range(8, 10)
    window.grid.cut_selection()
    assert len(window.score.notes) == 3

    # 删除整段
    window.grid.select_range(0, 2)
    window.grid.clear_cell()
    assert window.score.notes == []
    _close(window)


def test_out_of_range_is_flagged() -> None:
    if not _qt_ok():
        return
    window = _window()
    window.grid.set_cursor(0)
    window.grid.insert_keys([Key.from_degree(1, 1)])
    window.grid.transpose(octave_steps=1, whole=True)   # 高音1 → 越界的音区 2
    assert window.timeline.out_of_range_keys()
    assert window.grid._error_slots, "越界音所在的格必须被标红"
    issues = window.score.validate(window.keymap)
    assert any(item.level == "error" for item in issues)

    window.grid.transpose(octave_steps=-1, whole=True)
    assert not window.timeline.out_of_range_keys()
    assert not window.grid._error_slots
    _close(window)


# --------------------------------------------------------------------------- #
# 演奏与设置
# --------------------------------------------------------------------------- #

def test_playback_with_dummy_backend() -> None:
    if not _qt_ok():
        return
    window = _window()
    window.grid.set_cursor(0)
    for degree in (1, 2, 3, 4):
        window.grid.insert_keys([Key.from_degree(0, degree)])

    window._start_play()
    assert window.engine is not None
    assert _pump(lambda: not window.engine.is_busy, timeout=25.0), "演奏未结束"
    assert window.engine.state == EngineState.STOPPED
    events = getattr(window.backend, "events", [])
    assert len(events) >= 8, "演练后端应记录到按键事件"
    assert window.backend.pressed == []
    # 最后一次进度信号可能还在队列里，泵一下事件循环再断言
    assert _pump(
        lambda: window.transport.progress.value() == window.transport.progress.maximum(), timeout=5.0
    ), "演奏结束时进度条应走满"
    _close(window)


def test_pause_and_emergency_stop() -> None:
    if not _qt_ok():
        return
    window = _window()
    window.score.meta.subdivision = 32
    window.score.meta.bpm = 240
    window.grid.set_cursor(0)
    for degree in range(1, 8):
        window.grid.insert_keys([Key.from_degree(0, degree)])

    window.config.key_hold_ms = 400.0
    window._start_play()
    assert _pump(lambda: window.engine is not None and window.engine.state == EngineState.PLAYING, timeout=5.0)
    window._toggle_pause()
    assert _pump(lambda: window.engine.state == EngineState.PAUSED, timeout=5.0)
    window._toggle_pause()
    assert _pump(lambda: window.engine.state in (EngineState.PLAYING, EngineState.STOPPED), timeout=5.0)
    window._emergency_stop()
    assert _pump(lambda: window.engine.state == EngineState.STOPPED, timeout=5.0), "急停后引擎应很快停下"
    assert window.backend.pressed == []
    _close(window)


def test_playback_survives_wrong_focus_keyword() -> None:
    """回归（用户实测 bug）：窗口关键字跟实际窗口对不上时，F8 也要一路弹完。

    修复前：第一个音之后首次焦点轮询误判「失去焦点」→ 自动暂停，
    必须手动按 F9 才继续，之后又不会再暂停。
    """
    window = _window(target_window_keyword="Wuthering Waves", pause_on_focus_loss=True)
    window.grid.set_cursor(0)
    for degree in (1, 2, 3, 4, 5):
        window.grid.insert_keys([Key.from_degree(0, degree)])

    window._toggle_play()  # 等价于按 F8
    assert window.engine is not None
    assert window.config.countdown_s == 0.0
    assert _pump(lambda: not window.engine.is_busy, timeout=25.0), "演奏被焦点误判卡住了"
    assert window.engine.state == EngineState.STOPPED
    events = getattr(window.backend, "events", [])
    assert len(events) == 10, "5 个音应该产生 10 个按键事件，实际 %d" % len(events)
    assert window.engine.finish_reason == "演奏完成"
    _close(window)


def test_chromatic_mode_toggle_and_piano() -> None:
    """切换半音阶模式：映射表 36 键、虚拟键盘 3×12、状态同步。"""
    from core.notes import MODE_CHROMATIC, MODE_DIATONIC

    window = _window()
    assert window.mode == MODE_DIATONIC
    assert len(window.keymap.keys()) == 21

    assert window._switch_mode(MODE_CHROMATIC) is True
    assert window.mode == MODE_CHROMATIC
    assert len(window.keymap.keys()) == 36
    assert window.piano.mode == MODE_CHROMATIC
    assert window.grid.is_chromatic
    assert window.transport.chromatic_check.isChecked()
    assert window.action_mode.isChecked()

    # 半音阶键盘：中音这一行 12 个键都要画得出来，黑键有独立（更窄）的矩形
    for semitone in range(12):
        assert window.piano._key_rect(Key(0, semitone)) is not None, semitone
    black = window.piano._key_rect(Key(0, 1))
    white = window.piano._key_rect(Key(0, 0))
    assert black.width() < white.width()

    assert window._switch_mode(MODE_DIATONIC) is True
    assert len(window.keymap.keys()) == 21
    assert not window.transport.chromatic_check.isChecked()
    _close(window)


def test_chromatic_sharp_input_with_hash_prefix() -> None:
    """`#` 前缀：把升半音修饰键设为「不设」时，用 # 前缀输入升半音。"""
    from PySide6.QtCore import Qt

    from core.notes import MODE_CHROMATIC

    window = _window(sharp_modifier="none")
    window._switch_mode(MODE_CHROMATIC)
    window.grid.set_cursor(0)

    # 自然阶模式下按 # 只会给出提示
    window.score.meta.mode = "diatonic"
    _press(window, Qt.Key.Key_NumberSign, "#")
    assert window._sharp_pending is False
    window.score.meta.mode = MODE_CHROMATIC

    _press(window, Qt.Key.Key_NumberSign, "#")
    assert window._sharp_pending is True
    _press(window, Qt.Key.Key_S, "s")            # 中音2 → 中音#2
    assert window.grid.timeline.cell_keys(0) == [Key(0, 3)], window.grid.timeline.cell_keys(0)

    # 通过虚拟键盘点击同样支持 # 前缀
    window.grid.set_cursor(4)
    _press(window, Qt.Key.Key_NumberSign, "#")
    window._insert_key(Key.from_degree(0, 1))    # 中音1 → 中音#1
    assert window.grid.timeline.cell_keys(4) == [Key(0, 1)]
    _close(window)


def test_sharp_modifier_shift_does_not_hijack_hash_prefix() -> None:
    """修饰键为 Shift 时，Shift+3（打出 #）应按「主键 3 的升半音」处理，而不是进前缀模式。"""
    from PySide6.QtCore import Qt

    from core.notes import MODE_CHROMATIC

    window = _window()
    window._switch_mode(MODE_CHROMATIC)
    assert window.config.sharp_modifier == "shift"
    window.grid.set_cursor(0)
    _press(window, Qt.Key.Key_NumberSign, "#", Qt.KeyboardModifier.ShiftModifier)
    assert window._sharp_pending is False, "Shift 修饰键模式下不应进入 # 前缀"
    # chromatic-36 预设里 "3" = 高音#2，再升半音 = 高音3
    assert window.grid.timeline.cell_keys(0) == [Key(1, 4)], window.grid.timeline.cell_keys(0)
    _close(window)


def test_chromatic_downgrade_policy_nearest() -> None:
    """切回自然阶（策略=就近归入）：黑键降级可撤销。"""
    from core.notes import MODE_CHROMATIC, MODE_DIATONIC

    window = _window()
    window.config.chromatic_downgrade_policy = "nearest"
    window._switch_mode(MODE_CHROMATIC)
    window.grid.set_cursor(0)
    window.grid.insert_keys([Key(0, 1)])         # 中音#1
    window.grid.insert_keys([Key(1, 6)])         # 高音#4
    assert window.score.has_sharps()

    assert window._switch_mode(MODE_DIATONIC) is True
    assert window.mode == MODE_DIATONIC
    assert not window.score.has_sharps()
    assert window.grid.timeline.cell_keys(0) == [Key.from_degree(0, 1)]
    assert window.grid.timeline.cell_keys(1) == [Key.from_degree(1, 4)]

    window._undo()   # 降级操作可以撤销
    assert window.score.has_sharps()
    _close(window)


def test_chromatic_playback_with_dummy() -> None:
    """半音阶曲谱（含黑键）用演练后端完整演奏。"""
    from core.notes import MODE_CHROMATIC

    window = _window()
    window._switch_mode(MODE_CHROMATIC)
    for index, key in enumerate([Key(0, 0), Key(0, 1), Key(1, 6), Key(-1, 11)]):
        window.grid.set_cursor(index)
        window.grid.insert_keys([key], advance=False)

    window._start_play()
    assert window.engine is not None
    assert _pump(lambda: not window.engine.is_busy, timeout=25.0)
    events = getattr(window.backend, "events", [])
    assert len(events) == 8, events
    assert window.backend.pressed == []
    assert window.engine.state == EngineState.STOPPED
    _close(window)


def test_settings_dialog_chromatic_tab() -> None:
    """设置页半音阶标签页：36 个格子、预设切换、保存回配置。"""
    from core.notes import MODE_CHROMATIC
    from ui.settings_dialog import SettingsDialog

    _app()
    config = _config()
    dialog = SettingsDialog(config)
    dialog.chromatic_check.setChecked(True)
    assert dialog.preset_box.count() >= 3            # 半音阶 36 键 / FF14 风格 / 自定义
    assert dialog.chromatic_editor.mode == MODE_CHROMATIC
    assert len(dialog.chromatic_editor._editors) == 36

    dialog.chromatic_editor.set_preset("ff14-style")
    keymap = dialog.chromatic_editor.keymap()
    assert keymap.char_for(Key(0, 0)) == "z"
    assert keymap.char_for(Key(1, 0)) == "shift+z"
    assert keymap.char_for(Key(-1, 0)) == "ctrl+z"
    assert keymap.validate() == []

    dialog.chromatic_check.setChecked(True)
    dialog._on_accept()
    assert dialog.result() == SettingsDialog.DialogCode.Accepted
    result = dialog.result_config()
    assert result.chromatic_enabled is True
    assert result.chromatic_preset == "ff14-style"
    assert result.mode == MODE_CHROMATIC
    dialog.close()


def test_start_play_is_not_reentrant() -> None:
    """回归（用户实测 bug）：热键自动重复导致 _start_play 重入 → 弹出
    「演奏已在进行中，请先停止」，而且弹窗藏在最小化窗口后面，演奏结束才冒出来。

    这里用 QTimer.singleShot(0) 复刻真实时序：第二次触发会在
    ``_start_play`` 内部的 ``processEvents()`` 里被投递进来。
    """
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QMessageBox

    popups = []
    original_warning = QMessageBox.warning
    original_information = QMessageBox.information

    def record(*args, **kwargs):
        popups.append(str(args[2] if len(args) > 2 else args))
        return QMessageBox.StandardButton.Ok

    QMessageBox.warning = staticmethod(record)
    QMessageBox.information = staticmethod(record)
    try:
        window = _window()
        window.score.meta.subdivision = 8
        window.grid.set_cursor(0)
        for index in range(6):
            window.grid.insert_keys([Key.from_degree(0, index % 7 + 1)])

        calls = []
        original = window._start_play_impl

        def traced():
            calls.append(1)
            return original()

        window._start_play_impl = traced

        # 第二次触发排进事件队列，会在第一次 _start_play 的 processEvents() 里被投递
        QTimer.singleShot(0, lambda: window._hotkey_bridge.triggered.emit("start"))
        window._on_hotkey("start")

        assert len(calls) == 1, "启动流程被重入了 %d 次" % len(calls)
        assert popups == [], "不应弹出任何对话框：%s" % popups

        # 演奏要能正常跑完，结束时也不该弹窗
        assert _pump(lambda: not window.engine.is_busy, timeout=25.0)
        assert window.engine.finish_reason == "演奏完成"
        assert popups == [], "演奏结束时不应弹窗：%s" % popups
        _close(window)
    finally:
        QMessageBox.warning = original_warning
        QMessageBox.information = original_information


def test_start_play_twice_while_busy_is_silent() -> None:
    """已经在演奏时再触发一次开始：安静忽略，不弹模态框。"""
    from PySide6.QtWidgets import QMessageBox

    popups = []
    original_warning = QMessageBox.warning

    def record(*args, **kwargs):
        popups.append(str(args[2] if len(args) > 2 else args))
        return QMessageBox.StandardButton.Ok

    QMessageBox.warning = staticmethod(record)
    try:
        window = _window()
        window.grid.set_cursor(0)
        for index in range(4):
            window.grid.insert_keys([Key.from_degree(0, index % 7 + 1)])
        window._start_play()
        assert _pump(lambda: window.engine is not None and window.engine.is_busy, timeout=5.0)
        window._start_play()          # 引擎确实在演奏时再调一次
        assert popups == [], popups
        window._stop_play("测试停止")
        assert _pump(lambda: not window.engine.is_busy, timeout=5.0)
        _close(window)
    finally:
        QMessageBox.warning = original_warning


def test_settings_dialog_roundtrip() -> None:
    if not _qt_ok():
        return
    from ui.settings_dialog import SettingsDialog

    _app()
    config = _config()
    dialog = SettingsDialog(config)
    dialog.hold_box.setValue(45.0)
    dialog.countdown_box.setValue(2.0)
    dialog.keyword_edit.setText("鸣潮")
    dialog.autoscroll_check.setChecked(not config.autoscroll)
    dialog.hotkey_edits["start"].setText("f6")
    dialog._on_accept()
    assert dialog.result() == SettingsDialog.DialogCode.Accepted
    result = dialog.result_config()
    assert result.key_hold_ms == 45.0
    assert result.countdown_s == 2.0
    assert result.target_window_keyword == "鸣潮"
    assert result.hotkeys["start"] == "f6"
    dialog.close()


def test_countdown_overlay() -> None:
    if not _qt_ok():
        return
    from ui.countdown_overlay import CountdownOverlay

    _app()
    overlay = CountdownOverlay()
    overlay.show_countdown(3)
    assert overlay.isVisible()
    assert overlay._value == 3
    overlay.show_countdown(1)
    assert overlay._value == 1
    overlay.show_countdown(0)
    assert not overlay.isVisible()
    overlay.close()


def test_transport_reflects_meta() -> None:
    if not _qt_ok():
        return
    window = _window()
    window.transport.bpm_box.setValue(150)
    _app().processEvents()
    assert window.score.meta.bpm == 150
    window.transport.subdivision_box.setCurrentIndex(2)  # 1/16 拍
    _app().processEvents()
    assert window.score.meta.subdivision == 16
    assert window.grid.slots_per_bar == 4 * 16
    window.transport.signature_box.setCurrentIndex(1)   # 3/4
    _app().processEvents()
    assert window.score.meta.time_signature == (3, 4)
    assert window.grid.slots_per_bar == 3 * 16
    _close(window)


def test_export_and_import_through_window() -> None:
    if not _qt_ok():
        return
    from scoreio import json_io, text_io

    os.makedirs(TMP_DIR, exist_ok=True)
    window = _window()
    window.grid.set_cursor(0)
    for degree in (1, 3, 5):
        window.grid.insert_keys([Key.from_degree(0, degree)])

    path = json_io.save_score(window.score, os.path.join(TMP_DIR, "gui.score.json"))
    loaded = json_io.load_score(path)
    window._load_into_ui(loaded)
    assert len(window.score.notes) == 3
    assert window.undo.can_undo is False, "加载后撤销栈应清空"

    text_path = text_io.save_text(window.score, os.path.join(TMP_DIR, "gui.txt"), window.keymap)
    back = text_io.load_text(text_path, window.keymap)
    window._load_into_ui(back)
    assert len(window.score.notes) == 3
    _close(window)
