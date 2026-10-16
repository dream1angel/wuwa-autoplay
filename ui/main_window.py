# -*- coding: utf-8 -*-
"""主窗口：装配工具栏、节拍网格、虚拟键盘、运输条，并驱动演奏引擎。

职责边界
--------
* 所有编辑动作都经过 :meth:`MainWindow._apply_edit`：先快照 → 执行 → 入撤销栈 → 刷新，
  这样「撤销/重做」天然覆盖每一条编辑路径；
* 演奏相关的一切都在 :class:`core.engine.PlayEngine` 里，本文件只做参数装配与界面反馈；
* 全局热键回调来自钩子线程，统一经 :class:`ui.engine_bridge.HotkeyBridge` 信号切回主线程。
"""

from __future__ import annotations

import os
from typing import Callable, Dict, List, Optional

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QInputDialog,
    QLabel,
    QMainWindow,
    QMessageBox,
    QScrollArea,
    QStatusBar,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

from core.backends import BACKEND_CLASSES, BackendError, KeyBackend, create_backend
from core.config import DOWNGRADE_POLICIES, AppConfig
from core.engine import EngineError, EngineState, PlayEngine, PlayPlan, build_plan, check_mapping
from core.hotkeys import HOTKEY_LABELS, HotkeyManager
from core.model import Issue, Score, has_errors
from core.notes import MODE_CHROMATIC, MODE_DIATONIC, MODE_NAMES, Key, KeyMap, default_preset
from core.timeline import Timeline
from core.undo import UndoStack, clone_notes
from scoreio import json_io, render_io, text_io
from scoreio.json_io import ScoreIOError

from .countdown_overlay import CountdownOverlay
from .engine_bridge import EngineBridge, HotkeyBridge
from .grid_widget import GridScrollArea, GridWidget
from .piano_bar import PianoBar
from .resources import app_icon
from .settings_dialog import SettingsDialog
from .theme import Theme
from .transport_bar import TransportBar

__all__ = ["MainWindow"]

APP_NAME = "鸣潮自动演奏器"
HELP_TEXT = """<b>快速上手</b>
<ol>
<li>用键盘敲 <code>q w e r t y u</code>（高音）、<code>a s d f g h j</code>（中音）、
    <code>z x c v b n m</code>（低音）写谱，每敲一个音光标自动前进一格。</li>
<li>点击下方虚拟键盘也能输入；<code>空格</code>=休止符，<code>Backspace/Delete</code>=删除当前格。</li>
<li>同一格里继续敲另一个音就形成<b>和弦</b>（格子里纵向叠显）。</li>
<li><b>写和弦更顺手</b>：按住 <code>空格</code> 不放，把多个音敲进同一格，松开空格光标才前进
    （单击空格仍然是休止符）；鼠标也可以<b>右键点琴键</b>把音加入/移出当前格。</li>
<li><code>[</code> / <code>]</code> 缩短 / 延长时值（延音线）。</li>
<li><code>← → ↑ ↓</code> 移动光标，<code>Shift+方向键</code> 选一段乐句，
    <code>Ctrl+C/X/V</code> 复制剪切粘贴，<code>Ctrl+Z / Ctrl+Y</code> 撤销重做。</li>
<li>设好 BPM 与细分后点「▶ 开始演奏(F8)」，倒计时 3 秒内切回游戏即可。</li>
</ol>
<b>半音阶模式（36 键）</b>
<ul>
<li>默认关闭；按 <b>F7</b> 或勾选运输条上的「半音阶模式」开启，此时可用三个八度共 36 个半音。</li>
<li>开启后虚拟键盘变成 3×12（黑键更窄更深）；格子里黑键音显示为紫色 <code>#1</code> 这类音高记号。</li>
<li>输入升半音：<b>按住 Shift</b> 再按音键（例如 <code>Shift+S</code> = 中音#2，<code>Shift+F</code> = 中音#4）；
    也可以在设置里把修饰键换成 Ctrl/Alt，或改成「不设修饰键」用 <code>#</code> 前缀。</li>
<li>移调：<code>Ctrl+Shift+←/→</code> 半音、<code>Ctrl+Shift+↑/↓</code> 八度。</li>
<li>键位可在设置 →「半音阶」里改（支持组合键），内置「半音阶 36 键（全单键）」与
    「FF14 风格（钢琴式交错 + Ctrl/Shift 分档）」两套预设。</li>
<li>切回自然阶时若谱里有黑键，会问你：就近归入下方自然音 / 保留但演奏时跳过 / 取消切换。</li>
</ul>
<b>快捷键</b>：F7 切换半音阶 ｜ F8 开始/停止 ｜ F9 暂停/继续 ｜ F10 紧急停止（立即松开所有键）｜ 演奏中 Esc 退出
<br><br>
<b>游戏里弹不对怎么办</b>
<ul>
<li>漏音 → 设置里把「按下-抬起间隔」调到 40~60ms。</li>
<li>连击粘连 → 调大「最小按键间隔」，引擎会自动把太密的同键连击合并成长按。</li>
<li>组合键没反应 → 多数游戏只认单键，请换用「半音阶 36 键」单键预设；
    或先用设置里的「组合键兼容性自检」在记事本里试一下。</li>
<li>提示未提权 → 用管理员身份运行本程序（游戏通常是管理员权限，输入才能送进去）。</li>
<li>游戏改过键 → 设置 →「按键映射」（自然阶）或「半音阶」（36 键）里点格子再按新键。</li>
</ul>"""


class MainWindow(QMainWindow):
    """主窗口。"""

    def __init__(self, config: Optional[AppConfig] = None, parent=None) -> None:
        super().__init__(parent)
        self.config = config or AppConfig()
        self.keymap: KeyMap = self.config.keymap()
        self.score: Score = Score.empty(
            bpm=self.config.bpm,
            time_signature=self.config.time_signature,
            subdivision=self.config.subdivision,
            mode=self.config.mode,
        )
        self.undo = UndoStack(self.config.max_undo)
        self.timeline = Timeline(self.score)

        self.engine: Optional[PlayEngine] = None
        self.backend: Optional[KeyBackend] = None
        self._backend_name = ""
        self._plan: Optional[PlayPlan] = None
        self._meta_dirty = False
        self._was_maximized = False
        #: 半音阶输入：按过 '#' 之后，下一个音要升半音（仅在「不设修饰键」时使用）
        self._sharp_pending = False
        #: 启动演奏的重入保护（热键自动重复会重入 _start_play）
        self._starting = False
        #: 和弦输入（按住空格）：期间敲的音都进同一个格子
        self._chord_mode = False
        self._chord_added = False
        #: 每次和弦手势一个序号，用来把这一段编辑合并成一步撤销
        self._chord_serial = 0

        self._bridge = EngineBridge()
        self._hotkey_bridge = HotkeyBridge()
        self._hotkeys = HotkeyManager(on_message=self._on_hotkey_message)
        self._overlay = CountdownOverlay()

        self._build_ui()
        self._connect_signals()
        self._sync_from_config()
        self._register_hotkeys()
        self._refresh_all()

    # ------------------------------------------------------------------ #
    # 构建界面
    # ------------------------------------------------------------------ #
    def _build_ui(self) -> None:
        self.setWindowTitle(APP_NAME)
        self.resize(1180, 820)
        icon = app_icon()
        if icon is not None and not icon.isNull():
            self.setWindowIcon(icon)

        self._build_actions()
        self._build_toolbar()
        self._build_menus()

        central = QWidget()
        layout = QVBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.transport = TransportBar()
        layout.addWidget(self.transport)

        self.grid = GridWidget(self.score, self.keymap)
        self.grid.set_edit_handler(self._apply_edit)
        # 统一用网格内部的 timeline，避免出现两个包装同一份 Score 的对象（容易误判状态）
        self.timeline = self.grid.timeline
        self.scroll = GridScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setWidget(self.grid)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        layout.addWidget(self.scroll, 1)

        self.piano = PianoBar(self.keymap)
        layout.addWidget(self.piano)

        self.setCentralWidget(central)

        self.status = QStatusBar()
        self.validation_label = QLabel("")
        self.status.addPermanentWidget(self.validation_label)
        self.setStatusBar(self.status)

    def _build_actions(self) -> None:
        def make(text: str, shortcut: str = "", slot: Optional[Callable] = None, tip: str = "") -> QAction:
            action = QAction(text, self)
            if shortcut:
                action.setShortcut(QKeySequence(shortcut))
            if slot is not None:
                action.triggered.connect(slot)
            if tip:
                action.setStatusTip(tip)
            return action

        self.action_new = make("新建", "Ctrl+N", self._new_score, "清空并新建一首曲谱")
        self.action_open = make("打开…", "Ctrl+O", self._open_score, "打开 .score.json 或文本谱")
        self.action_save = make("保存", "Ctrl+S", self._save_score, "保存为 .score.json")
        self.action_save_as = make("另存为…", "Ctrl+Shift+S", self._save_score_as)
        self.action_import_text = make("导入文本谱…", "", self._import_text, "从纯文本谱导入")
        self.action_export_text = make("导出文本谱…", "", self._export_text, "导出社区分享用的文本谱")
        self.action_export_png = make("导出图片 PNG…", "", self._export_png, "导出曲谱图片")
        self.action_export_pdf = make("导出 PDF…", "", self._export_pdf, "导出单页 PDF")
        self.action_quit = make("退出", "Ctrl+Q", self.close)

        self.action_undo = make("撤销", "Ctrl+Z", self._undo)
        self.action_redo = make("重做", "Ctrl+Y", self._redo)
        self.action_cut = make("剪切", "Ctrl+X", lambda: self.grid.cut_selection())
        self.action_copy = make("复制", "Ctrl+C", lambda: self.grid.copy_selection())
        self.action_paste = make("粘贴", "Ctrl+V", lambda: self.grid.paste_clipboard("merge"))
        self.action_paste_over = make("粘贴（覆盖）", "Ctrl+Shift+V", lambda: self.grid.paste_clipboard("overwrite"))
        self.action_duplicate = make("原地复制一段", "Ctrl+D", lambda: self.grid.duplicate_selection())
        self.action_delete = make("删除所选", "Del", lambda: self.grid.clear_cell())
        self.action_select_all = make("全选", "Ctrl+A", lambda: self.grid.select_all())
        self.action_rest = make("休止符（空格）", "", lambda: self.grid.clear_cell(advance=True))
        self.action_longer = make("延长时值", "]", lambda: self.grid.bump_duration(1))
        self.action_shorter = make("缩短时值", "[", lambda: self.grid.bump_duration(-1))

        self.action_octave_up = make("选区升八度", "Ctrl+Up", lambda: self.grid.transpose(octave_steps=1))
        self.action_octave_down = make("选区降八度", "Ctrl+Down", lambda: self.grid.transpose(octave_steps=-1))
        self.action_degree_up = make("选区升一度", "Ctrl+Right", lambda: self.grid.transpose(degree_steps=1))
        self.action_degree_down = make("选区降一度", "Ctrl+Left", lambda: self.grid.transpose(degree_steps=-1))
        self.action_all_octave_up = make("整曲升八度", "", lambda: self.grid.transpose(octave_steps=1, whole=True))
        self.action_all_octave_down = make("整曲降八度", "", lambda: self.grid.transpose(octave_steps=-1, whole=True))
        self.action_transpose = make("移调…", "", self._transpose_dialog)
        self.action_check_range = make("检查音域", "Ctrl+R", self._check_range)
        self.action_settings = make("设置…", "Ctrl+,", self._open_settings)

        # 半音阶相关
        self.action_mode = make("半音阶模式", "", self._toggle_mode, "在自然阶 21 键与半音阶 36 键之间切换")
        self.action_mode.setCheckable(True)
        self.action_semitone_up = make("选区升半音", "Ctrl+Shift+Right", lambda: self.grid.transpose_semitones(1))
        self.action_semitone_down = make("选区降半音", "Ctrl+Shift+Left", lambda: self.grid.transpose_semitones(-1))
        self.action_semitone_octave_up = make(
            "选区升八度（半音）", "Ctrl+Shift+Up", lambda: self.grid.transpose_semitones(12)
        )
        self.action_semitone_octave_down = make(
            "选区降八度（半音）", "Ctrl+Shift+Down", lambda: self.grid.transpose_semitones(-12)
        )
        self.action_downgrade_sharps = make("黑键处理（就近归入自然音）…", "", self._downgrade_sharps)

        self.action_help = make("使用说明", "F1", self._show_help)
        self.action_about = make("关于", "", self._show_about)

    def _build_toolbar(self) -> None:
        bar = QToolBar("主工具栏")
        bar.setMovable(False)
        self.addToolBar(bar)
        bar.addAction(self.action_new)
        bar.addAction(self.action_open)
        bar.addAction(self.action_save)
        bar.addAction(self.action_save_as)
        bar.addSeparator()
        bar.addAction(self.action_import_text)
        bar.addAction(self.action_export_text)
        bar.addAction(self.action_export_png)
        bar.addAction(self.action_export_pdf)
        bar.addSeparator()
        bar.addAction(self.action_undo)
        bar.addAction(self.action_redo)

    def _build_menus(self) -> None:
        menu_file = self.menuBar().addMenu("文件")
        for action in (
            self.action_new,
            self.action_open,
            self.action_save,
            self.action_save_as,
        ):
            menu_file.addAction(action)
        menu_file.addSeparator()
        for action in (
            self.action_import_text,
            self.action_export_text,
            self.action_export_png,
            self.action_export_pdf,
        ):
            menu_file.addAction(action)
        menu_file.addSeparator()
        menu_file.addAction(self.action_quit)

        menu_edit = self.menuBar().addMenu("编辑")
        for action in (
            self.action_undo,
            self.action_redo,
            self.action_cut,
            self.action_copy,
            self.action_paste,
            self.action_paste_over,
            self.action_duplicate,
            self.action_delete,
            self.action_select_all,
        ):
            menu_edit.addAction(action)

        menu_tools = self.menuBar().addMenu("工具")
        for action in (
            self.action_rest,
            self.action_longer,
            self.action_shorter,
            self.action_octave_up,
            self.action_octave_down,
            self.action_degree_up,
            self.action_degree_down,
            self.action_all_octave_up,
            self.action_all_octave_down,
            self.action_semitone_up,
            self.action_semitone_down,
            self.action_semitone_octave_up,
            self.action_semitone_octave_down,
            self.action_transpose,
            self.action_downgrade_sharps,
            self.action_check_range,
        ):
            menu_tools.addAction(action)
        menu_tools.addSeparator()
        menu_tools.addAction(self.action_mode)
        menu_tools.addAction(self.action_settings)

        menu_help = self.menuBar().addMenu("帮助")
        menu_help.addAction(self.action_help)
        menu_help.addAction(self.action_about)

    def _connect_signals(self) -> None:
        self.transport.startClicked.connect(self._toggle_play)
        self.transport.pauseClicked.connect(self._toggle_pause)
        self.transport.settingsClicked.connect(self._open_settings)
        self.transport.bpmChanged.connect(self._on_bpm_changed)
        self.transport.timeSignatureChanged.connect(self._on_signature_changed)
        self.transport.subdivisionChanged.connect(self._on_subdivision_changed)
        self.transport.speedChanged.connect(self._on_speed_changed)
        self.transport.modeToggled.connect(self._on_mode_toggled)
        self.transport.presetChanged.connect(self._on_preset_changed)

        self.grid.cursorChanged.connect(self._on_cursor_changed)
        self.grid.editNotified.connect(lambda text: self.status.showMessage(text, 4000))
        self.piano.keyClicked.connect(self._insert_key)
        self.piano.keyToggled.connect(self._toggle_key_at_cursor)

        self._bridge.stateChanged.connect(self._on_engine_state)
        self._bridge.countdownTick.connect(self._on_countdown)
        self._bridge.progressTick.connect(self._on_progress)
        self._bridge.messagePosted.connect(self._on_engine_message)
        self._bridge.finished.connect(self._on_engine_finished)
        self._hotkey_bridge.triggered.connect(self._on_hotkey)

    def _sync_from_config(self) -> None:
        self.transport.set_meta(self.score.meta, self.config.speed)
        self.transport.set_mode_state(self.config.mode, self.config.chromatic_preset)
        self.piano.set_keymap(self.keymap)
        self.grid.set_keymap(self.keymap)

    # ------------------------------------------------------------------ #
    # 音阶模式
    # ------------------------------------------------------------------ #
    @property
    def mode(self) -> str:
        return self.score.meta.mode

    def _rebuild_keymap(self) -> None:
        """按当前模式重建映射表并同步到各个部件。"""
        self.keymap = self.config.keymap(self.mode)
        self.piano.set_keymap(self.keymap)
        self.grid.set_keymap(self.keymap)

    def _on_mode_toggled(self, checked: bool) -> None:
        """运输条开关 / 菜单 / F7 都走这里。"""
        target = MODE_CHROMATIC if checked else MODE_DIATONIC
        self._switch_mode(target)

    def _toggle_mode(self) -> None:
        target = MODE_DIATONIC if self.mode == MODE_CHROMATIC else MODE_CHROMATIC
        # 菜单项是可选中的，点它时状态已经翻转，这里以「当前模式」为准重新计算
        self._switch_mode(target)

    def _switch_mode(self, target: str) -> bool:
        """切换音阶模式；返回是否真的切换了。"""
        if target == self.mode:
            return False
        if self.engine is not None and self.engine.is_busy:
            QMessageBox.information(self, "正在演奏", "请先停止演奏再切换音阶模式。")
            self.transport.set_mode_state(self.mode, self.config.chromatic_preset)
            return False

        if target == MODE_DIATONIC and self.score.has_sharps():
            # 半音阶 → 自然阶：谱子里有黑键，按用户策略处理
            if not self._handle_downgrade():
                self.transport.set_mode_state(self.mode, self.config.chromatic_preset)
                return False

        self.score.meta.mode = target
        self.config.chromatic_enabled = target == MODE_CHROMATIC
        self._meta_dirty = True
        # 撤销栈保留：快照里只有音高数据（与模式无关），
        # 因此「黑键就近归入自然音」这类批量修改仍然可以 Ctrl+Z 撤回。
        self._rebuild_keymap()
        self.transport.set_mode_state(target, self.config.chromatic_preset)
        self.action_mode.setChecked(target == MODE_CHROMATIC)
        self.grid.set_score(self.score)   # 重置光标/选区，并按新音域重绘
        self._refresh_all()
        self.status.showMessage(
            "已切换到%s：%d 键可用%s"
            % (MODE_NAMES[target], len(self.keymap.keys()), "" if target == MODE_CHROMATIC else "（黑键已按策略处理）"),
            6000,
        )
        return True

    def _handle_downgrade(self) -> bool:
        """半音阶 → 自然阶时处理黑键，返回是否可以继续切换。"""
        policy = self.config.chromatic_downgrade_policy
        sharps = self.score.sharp_keys()
        listing = "、".join(key.label() for key in sharps[:10])
        if len(sharps) > 10:
            listing += " …"

        if policy == "ask":
            box = QMessageBox(self)
            box.setWindowTitle("谱子里有半音")
            box.setTextFormat(Qt.TextFormat.RichText)
            box.setText(
                "当前曲谱里有 <b>%d 个黑键音</b>（%s）。<br>"
                "自然阶模式只能演奏 21 个自然音，请选择处理方式：" % (len(sharps), listing)
            )
            to_nearest = box.addButton("就近归入下方自然音", QMessageBox.ButtonRole.AcceptRole)
            keep = box.addButton("保留黑键但演奏时跳过", QMessageBox.ButtonRole.DestructiveRole)
            cancel = box.addButton("取消切换", QMessageBox.ButtonRole.RejectRole)
            box.setDefaultButton(cancel)
            box.exec()
            clicked = box.clickedButton()
            if clicked is cancel:
                return False
            if clicked is to_nearest:
                self._downgrade_sharps(confirm=False)
                return True
            if clicked is keep:
                self.config.chromatic_downgrade_policy = "keep-disabled"
                self.status.showMessage("已保留黑键：切回自然阶后它们会标灰并在演奏时跳过", 8000)
                return True
            return False

        if policy == "nearest":
            self._downgrade_sharps(confirm=False)
            return True

        # keep-disabled
        self.status.showMessage(
            "%d 个黑键音将被保留，演奏时自动跳过（可在设置 → 半音阶里改策略）" % len(sharps), 8000
        )
        return True

    def _downgrade_sharps(self, confirm: bool = True) -> None:
        """把黑键音就近归入下方自然音（可撤销）。"""
        if not self.score.has_sharps():
            if confirm:
                QMessageBox.information(self, "黑键处理", "当前曲谱里没有黑键音，不需要处理。")
            return
        count = len(self.score.sharp_keys())

        def action() -> None:
            self.timeline.downgrade_sharps()

        self._apply_edit("黑键就近归入自然音（%d 个音）" % count, action)
        self.status.showMessage("已把 %d 个黑键音归入下方自然音（Ctrl+Z 可撤销）" % count, 8000)

    def _on_preset_changed(self, preset: str) -> None:
        if not preset:
            return
        if self.mode == MODE_CHROMATIC and preset != self.config.chromatic_preset:
            self.config.chromatic_preset = preset
            self._rebuild_keymap()
            self._refresh_all()
            self.status.showMessage("已切换到预设：%s" % preset, 5000)

    def _update_mode_indicator(self) -> None:
        """状态栏 / 运输条上的模式与映射提示。"""
        missing = check_mapping(self.score, self.keymap)
        if missing:
            names = "、".join(key.label() for key in missing[:6])
            self.transport.set_mapping_problem("⛔ %d 个音无按键（%s%s）" % (
                len(missing), names, "…" if len(missing) > 6 else ""))
        else:
            self.transport.set_mapping_problem("")
        self.mode_label_text = "%s %d 键" % (MODE_NAMES[self.mode], len(self.keymap.keys()))

    # ------------------------------------------------------------------ #
    # 编辑基础设施
    # ------------------------------------------------------------------ #
    def _apply_edit(self, label: str, action: Callable[[], None]) -> None:
        """统一的编辑入口：快照 → 执行 → 入撤销栈 → 刷新。

        和弦输入（按住空格）期间的多笔编辑共用一个 ``merge_key``，
        所以整段手势只占一步撤销。
        """
        before = clone_notes(self.score.notes)
        cursor_before = self.grid.cursor
        action()
        self.score.sort()
        after = clone_notes(self.score.notes)
        merge_key = ("chord-%d" % self._chord_serial) if self._chord_mode else ""
        self.undo.push(label, before, after, cursor_before, self.grid.cursor, merge_key=merge_key)
        self.grid.refresh()
        self._refresh_document_state()

    def _undo(self) -> None:
        slot = self.undo.undo(self.score)
        if slot is None:
            self.status.showMessage("没有可撤销的操作", 3000)
            return
        self.grid.set_cursor(slot)
        self.grid.refresh()
        self._refresh_document_state()

    def _redo(self) -> None:
        slot = self.undo.redo(self.score)
        if slot is None:
            self.status.showMessage("没有可重做的操作", 3000)
            return
        self.grid.set_cursor(slot)
        self.grid.refresh()
        self._refresh_document_state()

    def _on_cursor_changed(self, slot: int) -> None:
        bar = self.timeline.bar_of_slot(slot)
        self.status.showMessage("光标：第 %d 格（第 %d 小节）" % (slot, bar + 1), 2000)

    def _refresh_document_state(self) -> None:
        """刷新标题、撤销按钮状态、校验提示、网格高度。"""
        dirty = self.undo.is_dirty or self._meta_dirty
        self.setWindowTitle("%s — %s%s" % (APP_NAME, self.score.path or self.score.meta.title or "未命名", " *" if dirty else ""))
        self.action_undo.setEnabled(self.undo.can_undo)
        self.action_redo.setEnabled(self.undo.can_redo)
        self.action_mode.setChecked(self.mode == MODE_CHROMATIC)
        self._update_validation_hint()

    def _refresh_all(self) -> None:
        self.grid.refresh()
        self._refresh_document_state()
        self.action_copy.setEnabled(True)

    def _update_validation_hint(self, issues: Optional[List[Issue]] = None) -> None:
        """状态栏右侧的校验结果（含音阶模式与映射缺失提示）。"""
        issues = issues if issues is not None else self.score.validate(self.keymap)
        errors = [item for item in issues if item.level == "error"]
        warnings = [item for item in issues if item.level == "warning"]
        self._update_mode_indicator()
        if errors:
            self.validation_label.setText("⛔ %d 个问题需要修复" % len(errors))
            self.validation_label.setStyleSheet("color: %s;" % Theme.ERROR)
            self.validation_label.setToolTip("\n".join(str(item) for item in errors[:12]))
        elif warnings:
            self.validation_label.setText("⚠ %d 条提示" % len(warnings))
            self.validation_label.setStyleSheet("color: %s;" % Theme.WARN)
            self.validation_label.setToolTip("\n".join(str(item) for item in warnings[:12]))
        else:
            self.validation_label.setText("✅ 可以演奏")
            self.validation_label.setStyleSheet("color: %s;" % Theme.OK)
            self.validation_label.setToolTip("")

    # ------------------------------------------------------------------ #
    # 节拍参数
    # ------------------------------------------------------------------ #
    def _on_bpm_changed(self, value: int) -> None:
        if self.score.meta.bpm != value:
            self.score.meta.bpm = int(value)
            self.config.bpm = int(value)
            self._meta_dirty = True
            self.grid.refresh()
            self._refresh_document_state()

    def _on_signature_changed(self, signature) -> None:
        signature = (int(signature[0]), int(signature[1]))
        if tuple(self.score.meta.time_signature) != signature:
            self.score.meta.time_signature = signature
            self.config.time_signature = signature
            self._meta_dirty = True
            self.grid.refresh()
            self._refresh_document_state()

    def _on_subdivision_changed(self, value: int) -> None:
        value = int(value)
        if self.score.meta.subdivision != value:
            self.score.meta.subdivision = value
            self.config.subdivision = value
            self._meta_dirty = True
            self.grid.refresh()
            self._refresh_document_state()

    def _on_speed_changed(self, value: float) -> None:
        self.config.speed = float(value)

    # ------------------------------------------------------------------ #
    # 文件
    # ------------------------------------------------------------------ #
    def _confirm_discard(self) -> bool:
        """有未保存修改时询问；返回 True 表示可以继续。"""
        if not (self.undo.is_dirty or self._meta_dirty):
            return True
        answer = QMessageBox.question(
            self,
            "还有未保存的修改",
            "当前曲谱有未保存的修改，要保存吗？",
            QMessageBox.StandardButton.Save
            | QMessageBox.StandardButton.Discard
            | QMessageBox.StandardButton.Cancel,
        )
        if answer == QMessageBox.StandardButton.Save:
            return self._save_score()
        return answer == QMessageBox.StandardButton.Discard

    def _new_score(self) -> None:
        if not self._confirm_discard():
            return
        self.score = Score.empty(
            title="未命名曲谱",
            bpm=self.config.bpm,
            time_signature=self.config.time_signature,
            subdivision=self.config.subdivision,
            mode=self.config.mode,
        )
        self.undo = UndoStack(self.config.max_undo)
        self._meta_dirty = False
        self._rebuild_keymap()
        self.grid.set_score(self.score)
        self.timeline = self.grid.timeline
        self.transport.set_meta(self.score.meta, self.config.speed)
        self.transport.set_mode_state(self.config.mode, self.config.chromatic_preset)
        self.action_mode.setChecked(self.config.mode == MODE_CHROMATIC)
        self._refresh_all()

    def _load_into_ui(self, score: Score) -> None:
        self.score = score
        self.undo = UndoStack(self.config.max_undo)
        self._meta_dirty = False
        # 曲谱自带的模式决定用哪张映射表（半音阶曲谱自动打开半音阶模式）
        self.config.chromatic_enabled = score.meta.mode == MODE_CHROMATIC
        if score.meta.is_chromatic and score.meta.chromatic_preset:
            self.config.chromatic_preset = score.meta.chromatic_preset
        self._rebuild_keymap()
        self.grid.set_score(score)
        self.timeline = self.grid.timeline
        self.transport.set_meta(score.meta, self.config.speed)
        self.transport.set_mode_state(self.config.mode, self.config.chromatic_preset)
        self.action_mode.setChecked(self.config.mode == MODE_CHROMATIC)
        self._refresh_all()
        if score.warnings:
            self.status.showMessage("；".join(score.warnings), 8000)
        if score.meta.is_chromatic:
            self.status.showMessage(
                "已载入半音阶曲谱：%s（预设 %s）" % (MODE_NAMES[MODE_CHROMATIC], self.config.chromatic_preset), 6000
            )

    def _open_score(self) -> None:
        if not self._confirm_discard():
            return
        path, _ = QFileDialog.getOpenFileName(
            self, "打开曲谱", self.config.last_dir or "", "曲谱 (*.score.json *.json *.txt);;所有文件 (*)"
        )
        if not path:
            return
        try:
            if path.lower().endswith(".txt"):
                score = text_io.load_text(path, self.keymap)
            else:
                score = json_io.load_score(path)
        except ScoreIOError as exc:
            QMessageBox.critical(self, "打开失败", str(exc))
            return
        self._load_into_ui(score)
        self.config.remember_file(path)
        self.status.showMessage("已打开 %s" % path, 5000)
        self._sync_transport_from_score()

    def _sync_transport_from_score(self) -> None:
        """把曲谱的 BPM/拍号/细分同步进设置与界面。"""
        self.config.bpm = int(self.score.meta.bpm)
        self.config.time_signature = tuple(self.score.meta.time_signature)  # type: ignore[assignment]
        self.config.subdivision = int(self.score.meta.subdivision)
        self.transport.set_meta(self.score.meta, self.config.speed)

    def _save_score(self) -> bool:
        if not self.score.path:
            return self._save_score_as()
        try:
            json_io.save_score(self.score, self.score.path)
        except ScoreIOError as exc:
            QMessageBox.critical(self, "保存失败", str(exc))
            return False
        self.undo.mark_saved()
        self._meta_dirty = False
        self.config.remember_file(self.score.path)
        self.status.showMessage("已保存 %s" % self.score.path, 5000)
        self._refresh_document_state()
        return True

    def _save_score_as(self) -> bool:
        default = os.path.join(self.config.last_dir or "", "%s.score.json" % (self.score.meta.title or "未命名曲谱"))
        path, _ = QFileDialog.getSaveFileName(self, "另存为", default, "曲谱 (*.score.json);;所有文件 (*)")
        if not path:
            return False
        try:
            json_io.save_score(self.score, path)
        except ScoreIOError as exc:
            QMessageBox.critical(self, "保存失败", str(exc))
            return False
        self.undo.mark_saved()
        self._meta_dirty = False
        self.config.remember_file(self.score.path or path)
        self.status.showMessage("已保存 %s" % (self.score.path or path), 5000)
        self._refresh_document_state()
        return True

    def _import_text(self) -> None:
        if not self._confirm_discard():
            return
        path, _ = QFileDialog.getOpenFileName(
            self, "导入文本谱", self.config.last_dir or "", "文本谱 (*.txt);;所有文件 (*)"
        )
        if not path:
            return
        try:
            score = text_io.load_text(path, self.keymap)
        except ScoreIOError as exc:
            QMessageBox.critical(self, "导入失败", str(exc))
            return
        self._load_into_ui(score)
        self.config.remember_file(path)
        self.status.showMessage("已导入 %s（记得另存为 .score.json）" % path, 6000)
        self._sync_transport_from_score()

    def _export_text(self) -> None:
        default = os.path.join(self.config.last_dir or "", "%s.txt" % (self.score.meta.title or "未命名曲谱"))
        path, _ = QFileDialog.getSaveFileName(self, "导出文本谱", default, "文本谱 (*.txt)")
        if not path:
            return
        try:
            target = text_io.save_text(self.score, path, self.keymap)
        except ScoreIOError as exc:
            QMessageBox.critical(self, "导出失败", str(exc))
            return
        self.status.showMessage("已导出 %s" % target, 5000)

    def _export_png(self) -> None:
        default = os.path.join(self.config.last_dir or "", "%s.png" % (self.score.meta.title or "未命名曲谱"))
        path, _ = QFileDialog.getSaveFileName(self, "导出图片", default, "PNG 图片 (*.png)")
        if not path:
            return
        try:
            target = render_io.export_png(self.score, path, self.keymap)
        except ScoreIOError as exc:
            QMessageBox.critical(self, "导出失败", str(exc))
            return
        self.status.showMessage("已导出 %s" % target, 5000)

    def _export_pdf(self) -> None:
        default = os.path.join(self.config.last_dir or "", "%s.pdf" % (self.score.meta.title or "未命名曲谱"))
        path, _ = QFileDialog.getSaveFileName(self, "导出 PDF", default, "PDF (*.pdf)")
        if not path:
            return
        try:
            target = render_io.export_pdf(self.score, path, self.keymap)
        except ScoreIOError as exc:
            QMessageBox.critical(self, "导出失败", str(exc))
            return
        self.status.showMessage("已导出 %s" % target, 5000)

    # ------------------------------------------------------------------ #
    # 工具
    # ------------------------------------------------------------------ #
    def _transpose_dialog(self) -> None:
        if self.mode == MODE_CHROMATIC:
            steps, ok = QInputDialog.getInt(
                self, "移调", "把所有音整体移动几个半音？（12 = 一个八度）", 0, -36, 36
            )
            if not ok or steps == 0:
                return
            self.grid.transpose_semitones(steps, whole=True)
            return
        steps, ok = QInputDialog.getInt(self, "移调", "把所有音整体移动几个音级？（负数=降低）", 0, -14, 14)
        if not ok or steps == 0:
            return
        self.grid.transpose(degree_steps=steps, whole=True)

    def _check_range(self) -> None:
        key_count = len(self.keymap.keys())
        bad = self.timeline.out_of_range_keys()
        missing = check_mapping(self.score, self.keymap)
        issues = self.score.validate(self.keymap)
        errors = [item for item in issues if item.level == "error"]
        if not bad and not missing and not errors:
            QMessageBox.information(
                self,
                "音域检查",
                "当前是%s模式：全部 %d 个音都在支持的 %d 键范围内 ✅"
                % (MODE_NAMES[self.mode], len(self.score.notes), key_count),
            )
            return
        lines = [str(item) for item in errors[:15]]
        if bad:
            lines.insert(0, "越界音：%s" % "、".join(key.label() for key in bad))
        if missing:
            lines.insert(0, "没有按键的音：%s" % "、".join(key.label() for key in missing[:12]))
        lines.append("")
        if self.mode == MODE_DIATONIC:
            lines.append("可以用「工具 → 整曲升/降八度、移调」快速修复；黑键音可用「黑键处理」归入自然音。")
        else:
            lines.append("可以用「工具 → 选区升/降半音、移调…」快速修复，或在设置 → 半音阶里补上映射。")
        QMessageBox.warning(self, "音域检查", "\n".join(lines))

    def _open_settings(self) -> None:
        dialog = SettingsDialog(self.config, self)
        if dialog.exec() != SettingsDialog.DialogCode.Accepted:
            return
        previous_mode = self.mode
        self.config = dialog.result_config().clone()
        self.undo.limit = self.config.max_undo
        self.transport.set_speed(self.config.speed)
        try:
            self.config.save()
        except OSError as exc:
            QMessageBox.warning(self, "设置保存失败", str(exc))

        target = self.config.mode
        if target != previous_mode and not self.score.has_sharps():
            # 在设置里改了模式：走统一的切换流程（有黑键时让用户确认）
            self.score.meta.mode = target
            self._meta_dirty = True
        elif target == MODE_DIATONIC and self.score.has_sharps():
            self.score.meta.mode = target if self._handle_downgrade() else MODE_CHROMATIC
            self.config.chromatic_enabled = self.score.meta.mode == MODE_CHROMATIC

        self._rebuild_keymap()
        self.transport.set_mode_state(self.mode, self.config.chromatic_preset)
        self.action_mode.setChecked(self.mode == MODE_CHROMATIC)
        self.grid.set_score(self.score)
        self._register_hotkeys()
        self._refresh_all()
        self.status.showMessage("设置已保存（当前：%s）" % self.keymap.describe(), 5000)

    def _show_help(self) -> None:
        box = QMessageBox(self)
        box.setWindowTitle("使用说明")
        box.setTextFormat(Qt.TextFormat.RichText)
        box.setText(HELP_TEXT)
        box.exec()

    def _show_about(self) -> None:
        QMessageBox.information(
            self,
            "关于",
            "%s\n\n把界面上编排的曲谱按严格节拍转换成模拟按键，在《鸣潮》乐器玩法里自动演奏。\n"
            "核心引擎与界面完全解耦，可用 tests/ 与 cli.py 单独验证。\n\n"
            "BPM %d ｜ 拍号 %s ｜ 细分 1/%d拍 ｜ %s"
            % (
                APP_NAME,
                self.score.meta.bpm,
                self.score.meta.time_signature_text,
                self.score.meta.subdivision,
                self.keymap.describe(),
            ),
        )

    # ------------------------------------------------------------------ #
    # 热键
    # ------------------------------------------------------------------ #
    def _hotkey_callbacks(self) -> Dict[str, Callable[[], None]]:
        return {
            name: self._hotkey_bridge.make_callback(name)
            for name in ("mode", "start", "pause", "emergency", "exit")
        }

    def _register_hotkeys(self) -> None:
        self._hotkeys.set_bindings(self.config.hotkeys)
        if not self.config.enable_hotkeys:
            self._hotkeys.unregister_all()
            self.status.showMessage("全局快捷键已关闭（可在设置 → 界面里打开）", 5000)
            return
        failures = self._hotkeys.register(self._hotkey_callbacks())
        for message in failures:
            self.status.showMessage(message, 8000)

    def _on_hotkey_message(self, level: str, text: str) -> None:
        self.status.showMessage(text, 6000)

    def _on_hotkey(self, name: str) -> None:
        """热键回调（已切回主线程）。"""
        if name == "mode":
            self._toggle_mode()
        elif name == "start":
            self._toggle_play()
        elif name == "pause":
            self._toggle_pause()
        elif name == "emergency":
            self._emergency_stop()
        elif name == "exit":
            if self.engine is not None and self.engine.is_busy:
                self._stop_play("已退出演奏模式")

    # ------------------------------------------------------------------ #
    # 演奏
    # ------------------------------------------------------------------ #
    def _ensure_backend(self) -> Optional[KeyBackend]:
        name = self.config.backend
        cls = BACKEND_CLASSES.get(name)
        if cls is None:
            name = "dummy"
            cls = BACKEND_CLASSES["dummy"]
        available, message = cls.available()
        if not available:
            answer = QMessageBox.question(
                self,
                "按键后端不可用",
                "%s\n\n是否改用「演练模式」？（不真正按键，只按节拍走一遍，用来检查速度与节奏）" % message,
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return None
            name = "dummy"
        if self.backend is not None and self._backend_name == name:
            return self.backend
        try:
            if self.backend is not None:
                self.backend.close()
            self.backend = create_backend(name)
            self._backend_name = name
        except BackendError as exc:
            QMessageBox.critical(self, "按键后端初始化失败", str(exc))
            return None
        return self.backend

    def _start_play(self) -> None:
        """开始演奏（带重入保护）。

        为什么需要保护：本方法末尾会调用 ``QApplication.processEvents()`` 让「最小化」
        立刻生效；这一次事件循环会把**排队中的第二个热键触发**投递进来（``keyboard``
        对按住 F8 的自动重复会反复回调），从而嵌套调用本方法——那时引擎还没启动，
        两次都会走到 ``engine.start()``，第二次就会撞上「演奏已在进行中」并弹出模态框。
        """
        if self._starting:
            self.status.showMessage("正在启动演奏，已忽略这次重复触发", 3000)
            return
        if self.engine is not None and self.engine.is_busy:
            return
        self._starting = True
        try:
            self._start_play_impl()
        finally:
            self._starting = False

    def _start_play_impl(self) -> None:
        issues: List[Issue] = self.score.validate(self.keymap)
        errors = [item for item in issues if item.level == "error"]
        if errors:
            QMessageBox.warning(
                self,
                "曲谱还有问题",
                "修复后才能演奏：\n\n%s" % "\n".join(str(item) for item in errors[:12]),
            )
            return

        # 模式一致性：所有音都必须在当前映射表里有按键
        missing = check_mapping(self.score, self.keymap)
        if missing:
            names = "、".join(key.label() for key in missing[:10])
            QMessageBox.warning(
                self,
                "有音没有按键",
                "当前是%s（%s），下面这些音没有绑定按键：\n%s%s\n\n"
                "解决办法：① 换一个预设；② 在设置 → %s 里补上映射；③ 用工具菜单把它们移调/降八度。"
                % (
                    MODE_NAMES[self.mode],
                    self.keymap.preset,
                    names,
                    "…" if len(missing) > 10 else "",
                    "半音阶" if self.mode == MODE_CHROMATIC else "按键映射",
                ),
            )
            return

        options = self.config.play_options()
        try:
            plan = build_plan(self.score, self.keymap, options)
        except EngineError as exc:
            QMessageBox.warning(self, "无法演奏", str(exc))
            return
        if not plan.events:
            QMessageBox.warning(self, "无法演奏", "曲谱里没有可演奏的音符")
            return
        for warning in plan.warnings:
            self.status.showMessage(warning, 8000)

        backend = self._ensure_backend()
        if backend is None:
            return
        if self.engine is None or self.engine.backend is not backend:
            if self.engine is not None:
                self.engine.close()
            self.engine = PlayEngine(backend, self._bridge.callbacks())
        else:
            self.engine.callbacks = self._bridge.callbacks()

        self._plan = plan
        self._was_maximized = self.isMaximized()
        self.transport.reset_progress(plan.duration_ms)
        self.status.showMessage(plan.describe(), 8000)
        if self.config.pause_on_focus_loss and not self.config.target_window_keyword.strip():
            self.status.showMessage(
                "提示：没有设置目标窗口关键字，失焦自动暂停不会生效（设置 → 按键与窗口）", 8000
            )
        self._hotkeys.register_session(self._hotkey_callbacks())

        # 让出前台：最小化窗口，倒计时期间用户切回游戏
        self.showMinimized()
        QApplication.processEvents()
        try:
            self.engine.start(plan, options, countdown=self.config.countdown_s)
        except EngineError as exc:
            # 先还原窗口再弹窗：窗口被最小化时弹出的模态框会藏在游戏后面，
            # 等演奏结束窗口还原才冒出来（用户会以为「结束时才弹」）
            self._restore_window()
            if self.engine.is_busy:
                # 重复触发（例如热键自动重复）导致的「已经在演奏」：安静忽略，别打断演奏
                self.status.showMessage("演奏已经在进行中，忽略了这次开始请求", 5000)
            else:
                QMessageBox.warning(self, "无法开始演奏", str(exc))
        except BackendError as exc:
            self._restore_window()
            QMessageBox.warning(self, "无法开始演奏", str(exc))

    def _stop_play(self, reason: str = "用户停止") -> None:
        if self.engine is not None:
            self.engine.stop(reason)
        self._overlay.hide_overlay()

    def _emergency_stop(self) -> None:
        if self.engine is not None:
            self.engine.emergency_stop()
        self._overlay.hide_overlay()
        self.status.showMessage("紧急停止：已释放所有按键", 5000)

    def _toggle_play(self) -> None:
        if self._starting:
            # 启动流程正在进行（多半是热键自动重复又触发了一次）：直接忽略
            self.status.showMessage("正在启动演奏，已忽略这次重复触发", 3000)
            return
        if self.engine is not None and self.engine.is_busy:
            self._stop_play()
        else:
            self._start_play()

    def _toggle_pause(self) -> None:
        if self.engine is None:
            return
        state = self.engine.state
        if state == EngineState.PAUSED:
            self.engine.resume()
        elif state in (EngineState.PLAYING, EngineState.COUNTDOWN):
            self.engine.pause()
        else:
            self.status.showMessage("当前没有在演奏", 3000)

    def _restore_window(self) -> None:
        if self._was_maximized:
            self.showMaximized()
        else:
            self.showNormal()
        self.raise_()
        self.activateWindow()

    # ---- 引擎回调（已在主线程） ---------------------------------------- #
    def _on_engine_state(self, value: str) -> None:
        try:
            state = EngineState(value)
        except ValueError:  # pragma: no cover
            return
        self.transport.set_state(state)
        if state == EngineState.PLAYING:
            self._overlay.hide_overlay()
        elif state == EngineState.STOPPED:
            self.grid.clear_playhead()
            self.piano.set_highlight(set())
            self._hotkeys.unregister_session()

    def _on_countdown(self, value: float) -> None:
        self._overlay.show_countdown(value)

    def _on_progress(self, elapsed_ms: float, total_ms: float, bar: int) -> None:
        self.transport.set_progress(elapsed_ms, total_ms, bar, self._plan.bar_count if self._plan else 1)
        if self._plan is None or self._plan.slot_ms <= 0:
            return
        slot = int(elapsed_ms / self._plan.slot_ms)
        self.grid.set_playhead(slot, bar)
        self.piano.set_highlight(self.grid.keys_at_playhead())

    def _on_engine_message(self, level: str, text: str) -> None:
        self.transport.set_status_text(text, level)
        self.status.showMessage(text, 6000)

    def _on_engine_finished(self, reason: str) -> None:
        self._overlay.hide_overlay()
        self.grid.clear_playhead()
        self.piano.set_highlight(set())
        self._hotkeys.unregister_session()
        self._restore_window()
        self.status.showMessage("演奏结束：%s" % reason, 8000)

    # ------------------------------------------------------------------ #
    # 键盘输入
    # ------------------------------------------------------------------ #
    #: Shift 组合下 text() 会变成符号，这里把它们映射回基础按键
    _SHIFTED_SYMBOLS = {
        "@": "2", "#": "3", "$": "4", "%": "5", "^": "6", "&": "7", "*": "8",
        "(": "9", ")": "0", "!": "1", ":": ";", "+": "=", "_": "-",
        "{": "[", "}": "]", "|": "\\", '"': "'", "<": ",", ">": ".", "?": "/",
    }

    @classmethod
    def _event_key_name(cls, event) -> str:
        """把按键事件转成「基础按键名」。

        必须走 ``event.key()`` 而不是 ``event.text()``：按住 Shift 时
        ``Shift+2`` 的 text() 是 ``@``，但我们想要的是主键 ``2``。
        """
        key = event.key()
        if Qt.Key.Key_A <= key <= Qt.Key.Key_Z:
            return chr(key).lower()
        if Qt.Key.Key_0 <= key <= Qt.Key.Key_9:
            return chr(key)
        text = event.text() or ""
        if not text or text.isspace():
            return ""
        lowered = text.lower()
        return cls._SHIFTED_SYMBOLS.get(text, lowered)

    def _sharp_modifier_active(self, mods) -> bool:
        """按设置判断当前是否按住「升半音修饰键」。"""
        choice = self.config.sharp_modifier
        if choice == "shift":
            return bool(mods & Qt.KeyboardModifier.ShiftModifier)
        if choice == "ctrl":
            return bool(mods & Qt.KeyboardModifier.ControlModifier)
        if choice == "alt":
            return bool(mods & Qt.KeyboardModifier.AltModifier)
        return False

    def keyPressEvent(self, event) -> None:  # noqa: N802
        key = event.key()
        mods = event.modifiers()
        shift = bool(mods & Qt.KeyboardModifier.ShiftModifier)

        if key == Qt.Key.Key_Escape:
            if self.engine is not None and self.engine.is_busy:
                self._stop_play("已退出演奏模式")
            else:
                self.grid.clear_selection()
            return
        if key == Qt.Key.Key_Space:
            # 按住不放 = 和弦输入（自动重复要忽略），单击 = 休止符（见 keyReleaseEvent）
            if not event.isAutoRepeat():
                self._begin_chord_input()
            return
        if key == Qt.Key.Key_Backspace:
            self.grid.delete_backward()
            return
        if key == Qt.Key.Key_Delete:
            self.grid.clear_cell()
            return
        if key == Qt.Key.Key_Left:
            self.grid.move_cursor(-1, extend=shift)
            return
        if key == Qt.Key.Key_Right:
            self.grid.move_cursor(1, extend=shift)
            return
        if key == Qt.Key.Key_Up:
            self.grid.move_cursor(0, -1, extend=shift)
            return
        if key == Qt.Key.Key_Down:
            self.grid.move_cursor(0, 1, extend=shift)
            return
        if key in (Qt.Key.Key_BracketRight, Qt.Key.Key_Equal):
            self.grid.bump_duration(1)
            return
        if key in (Qt.Key.Key_BracketLeft, Qt.Key.Key_Minus):
            self.grid.bump_duration(-1)
            return

        text = event.text() or ""
        if not text:
            super().keyPressEvent(event)
            return

        # `#` 前缀模式：只在「不设修饰键」时生效，避免和 Shift+3 这种输入打架
        if text == "#" and self.config.sharp_modifier == "none":
            self._begin_sharp_input()
            return

        name = self._event_key_name(event)
        if not name:
            super().keyPressEvent(event)
            return

        sharp = self._sharp_modifier_active(mods)
        if self._sharp_pending:
            sharp = True
            self._sharp_pending = False

        mapped = self.keymap.locate(name)
        if mapped is not None:
            if self._chord_mode:
                # 和弦模式：加/减同一个格子，不前进光标
                if self._input_key(mapped, sharp=sharp, toggle=True):
                    self._chord_added = True
                return
            self._input_key(mapped, sharp=sharp, advance=True)
            return

        if name in ("0", "."):
            self.grid.clear_cell(advance=True)
            return
        if name.isdigit() and 1 <= int(name) <= 7 and self.mode == MODE_DIATONIC:
            # 自然阶模式下数字键 = 中音音级；半音阶模式下数字是黑键键位，由映射表处理
            self._input_key(Key.from_degree(0, int(name)), sharp=sharp, advance=True)
            return
        super().keyPressEvent(event)

    def keyReleaseEvent(self, event) -> None:  # noqa: N802
        """松开空格 = 结束和弦输入（或当作休止符）。"""
        if event.key() == Qt.Key.Key_Space and not event.isAutoRepeat():
            self._end_chord_input()
            return
        super().keyReleaseEvent(event)

    # ------------------------------------------------------------------ #
    # 和弦输入（按住空格）
    # ------------------------------------------------------------------ #
    def _begin_chord_input(self) -> None:
        self._chord_serial += 1
        self._chord_mode = True
        self._chord_added = False
        self.grid.set_chord_mode(True)
        self.status.showMessage(
            "和弦输入中：按住空格不放，敲多个音（或点虚拟键盘）都进同一格；松开空格结束", 8000
        )

    def _end_chord_input(self) -> None:
        was_chord = self._chord_mode
        added = self._chord_added
        self._chord_mode = False
        self._chord_added = False
        self.grid.set_chord_mode(False)
        if not was_chord:
            return
        if added:
            self.grid.set_cursor(self.grid.cursor + 1)      # 和弦写完，前进一格
            self.status.showMessage("和弦已写入，光标前进一格", 3000)
        else:
            # 按住空格却没敲音 = 休止符：清空该格并前进（保留原有习惯）
            self.grid.clear_cell(advance=True)

    # ------------------------------------------------------------------ #
    # 音符输入
    # ------------------------------------------------------------------ #
    def _begin_sharp_input(self) -> None:
        """按下 `#`：下一个音要升半音（不设修饰键时的输入方式）。"""
        if self.mode != MODE_CHROMATIC:
            self.status.showMessage("升半音需要先开启半音阶模式（运输条开关或按 F7）", 5000)
            return
        self._sharp_pending = True
        self.status.showMessage("升半音：请接着按一个音键（例如 S → 中音#2）", 5000)

    def _input_key(self, key: Key, *, sharp: bool = False, toggle: bool = False, advance: bool = True) -> bool:
        """统一的音符写入入口（电脑键盘、虚拟键盘、和弦模式共用）。

        ``sharp``  = 写这个音的升半音
        ``toggle`` = 已存在则移除（和弦模式用；会给出可见提示）
        """
        if sharp:
            if self.mode != MODE_CHROMATIC:
                self.status.showMessage("升半音需要先开启半音阶模式（运输条开关或按 F7）", 5000)
                return False
            target = key.shifted_semitones(1)
            if not target.is_valid(MODE_CHROMATIC):
                self.status.showMessage("%s 再升半音会超出音域，已取消" % key.label(), 5000)
                return False
            if key.degree in (3, 7):
                self.status.showMessage(
                    "插入 %s：%s 没有升半音，按等音处理" % (target.label(), key.label()), 4000
                )
            key = target

        if toggle:
            existed = key in self.grid.timeline.cell_keys(self.grid.cursor)
            self.grid.toggle_key(key)
            now = self.grid.timeline.cell_keys(self.grid.cursor)
            if existed:
                self.status.showMessage("已从本格移除 %s" % key.label(), 2000)
            elif key in now:
                self.status.showMessage("已加入本格 %s（继续敲 = 和弦）" % key.label(), 2000)
            else:  # pragma: no cover - 兜底
                self.status.showMessage("无法在本格写入 %s" % key.label(), 3000)
            return True
        self.grid.insert_keys([key], advance=advance)
        return True

    def _insert_key(self, key: Key) -> None:
        """虚拟键盘左键点击 / 外部调用：和弦模式下不前进光标。"""
        if self._chord_mode:
            if self._input_key(key, toggle=True):
                self._chord_added = True
            return
        sharp = self._sharp_pending
        self._sharp_pending = False
        self._input_key(key, sharp=sharp, advance=True)

    def _toggle_key_at_cursor(self, key: Key) -> None:
        """虚拟键盘右键：加入/移出当前格，不前进光标（点着写和弦用）。"""
        if self._input_key(key, toggle=True) and self._chord_mode:
            self._chord_added = True

    # ------------------------------------------------------------------ #
    # 关闭
    # ------------------------------------------------------------------ #
    def closeEvent(self, event) -> None:  # noqa: N802
        if not self._confirm_discard():
            event.ignore()
            return
        try:
            self.config.save()
        except OSError:
            pass
        if self.engine is not None:
            self.engine.close()
        elif self.backend is not None:
            try:
                self.backend.close()
            except Exception:
                pass
        self._hotkeys.unregister_all()
        self._overlay.close()
        event.accept()
