# -*- coding: utf-8 -*-
"""设置对话框：演奏 / 按键与窗口 / 快捷键 / 按键映射 / 半音阶 / 界面。

快捷键与按键映射都用「点一下再按键」的捕获输入框，避免手输格式出错；
保存前统一做冲突检测（快捷键重复、同一按键被两个音占用）。
半音阶标签页支持 36 键映射、组合键捕获与「组合键兼容性自检」。
"""

from __future__ import annotations

import time
from typing import Dict, List, Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from core.backends import BackendError, create_backend
from core.backends import backend_choices, probe_backends
from core.config import DOWNGRADE_POLICIES, SHARP_MODIFIERS, AppConfig
from core.focus import foreground_info
from core.hotkeys import DEFAULT_HOTKEYS, HOTKEY_LABELS, find_conflicts, normalize_hotkey
from core.notes import (
    MODE_CHROMATIC,
    MODE_DIATONIC,
    OCTAVE_NAMES,
    PRESET_NAMES,
    SEMITONE_JIANPU,
    Key,
    KeyMap,
    KeyStroke,
    default_preset,
    preset_names_for,
)

from .theme import Theme

__all__ = ["SettingsDialog", "KeyCaptureEdit", "KeymapEditor"]

_SPECIAL_KEYS: Dict[int, str] = {
    Qt.Key.Key_Escape: "esc",
    Qt.Key.Key_Space: "space",
    Qt.Key.Key_Tab: "tab",
    Qt.Key.Key_Return: "enter",
    Qt.Key.Key_Enter: "enter",
    Qt.Key.Key_Backspace: "backspace",
    Qt.Key.Key_Delete: "delete",
    Qt.Key.Key_Insert: "insert",
    Qt.Key.Key_Home: "home",
    Qt.Key.Key_End: "end",
    Qt.Key.Key_PageUp: "page up",
    Qt.Key.Key_PageDown: "page down",
    Qt.Key.Key_Up: "up",
    Qt.Key.Key_Down: "down",
    Qt.Key.Key_Left: "left",
    Qt.Key.Key_Right: "right",
    Qt.Key.Key_Plus: "+",
    Qt.Key.Key_Minus: "-",
    Qt.Key.Key_BracketLeft: "[",
    Qt.Key.Key_BracketRight: "]",
    Qt.Key.Key_Slash: "/",
    Qt.Key.Key_Semicolon: ";",
    Qt.Key.Key_Comma: ",",
    Qt.Key.Key_Period: ".",
}
for _index in range(1, 13):
    _SPECIAL_KEYS[getattr(Qt.Key, "Key_F%d" % _index)] = "f%d" % _index


class KeyCaptureEdit(QLineEdit):
    """只读输入框：获得焦点后按下一个键（可带修饰键）即被捕获。"""

    captured = Signal(str)

    def __init__(self, text: str = "", parent=None) -> None:
        super().__init__(text, parent)
        self.setReadOnly(True)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setPlaceholderText("点这里再按键")
        self.setToolTip("点击后直接按下想用的键；组合键请按住 Ctrl / Alt / Shift 再按主键")
        self.setMinimumWidth(72)

    def keyPressEvent(self, event) -> None:  # noqa: N802
        name = self._key_name(event)
        if name is None:
            event.ignore()
            return
        modifiers = []
        mods = event.modifiers()
        if mods & Qt.KeyboardModifier.ControlModifier:
            modifiers.append("ctrl")
        if mods & Qt.KeyboardModifier.AltModifier:
            modifiers.append("alt")
        if mods & Qt.KeyboardModifier.ShiftModifier:
            modifiers.append("shift")
        combo = normalize_hotkey("+".join(modifiers + [name]))
        try:
            KeyStroke.parse(combo)
        except ValueError:
            return
        self.setText(combo)
        self.captured.emit(combo)
        event.accept()

    @staticmethod
    def _key_name(event) -> Optional[str]:
        key = event.key()
        if key in _SPECIAL_KEYS:
            return _SPECIAL_KEYS[key]
        if Qt.Key.Key_A <= key <= Qt.Key.Key_Z:
            return chr(key).lower()
        if Qt.Key.Key_0 <= key <= Qt.Key.Key_9:
            return chr(key)
        return None


class KeymapEditor(QWidget):
    """按键映射编辑器（自然阶 21 音位 / 半音阶 36 音位）。"""

    changed = Signal()

    def __init__(self, keymap: KeyMap, parent=None) -> None:
        super().__init__(parent)
        self._keymap = keymap.copy()
        self._mode = keymap.mode
        self._editors: Dict[Key, KeyCaptureEdit] = {}
        self._build()

    # ------------------------------------------------------------------ #
    @property
    def mode(self) -> str:
        return self._mode

    def _columns(self) -> List[Key]:
        if self._mode == MODE_CHROMATIC:
            return [Key(1, semitone) for semitone in range(12)]
        return [Key.from_degree(1, degree) for degree in range(1, 8)]

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)

        if self._mode == MODE_CHROMATIC:
            hint = QLabel(
                "半音阶 36 键：白键（自然音）与黑键（升半音）各 12 个/八度。"
                "点格子后按下新键即可改绑；支持组合键（按住 Ctrl/Alt/Shift 再按主键）。"
            )
        else:
            hint = QLabel(
                "默认就是游戏内布局（高音 q~u、中音 a~j、低音 z~m）。"
                "如果游戏里改过键、或想用在别的游戏上，点对应格子后按下新键即可。"
            )
        hint.setWordWrap(True)
        hint.setStyleSheet("color: %s;" % Theme.MUTED)
        root.addWidget(hint)

        grid = QGridLayout()
        grid.setHorizontalSpacing(6)
        grid.setVerticalSpacing(6)
        columns = self._columns()
        for column, sample in enumerate(columns):
            header = QLabel(sample.short_label())
            header.setAlignment(Qt.AlignmentFlag.AlignCenter)
            color = Theme.NOTE_SHARP if sample.is_sharp else Theme.MUTED
            header.setStyleSheet("color: %s;" % color)
            grid.addWidget(header, 0, column + 1)

        for row, octave in enumerate((1, 0, -1), start=1):
            label = QLabel(OCTAVE_NAMES[octave])
            label.setStyleSheet("color: %s;" % Theme.MUTED)
            grid.addWidget(label, row, 0)
            for column, sample in enumerate(columns):
                key = Key(octave, sample.semitone)
                editor = KeyCaptureEdit(self._keymap.char_for(key))
                editor.captured.connect(lambda combo, k=key: self._on_captured(k, combo))
                self._editors[key] = editor
                grid.addWidget(editor, row, column + 1)

        root.addLayout(grid)

        buttons = QHBoxLayout()
        reset = QPushButton("恢复默认布局")
        reset.clicked.connect(self.reset_default)
        buttons.addWidget(reset)
        buttons.addStretch(1)
        self.status = QLabel("")
        self.status.setStyleSheet("color: %s;" % Theme.MUTED)
        buttons.addWidget(self.status)
        root.addLayout(buttons)
        self._sync_widgets()

    # ------------------------------------------------------------------ #
    def _on_captured(self, key: Key, combo: str) -> None:
        if not combo:
            return
        slot = KeyMap.slot_str(key.octave, key.semitone)
        previous = self._keymap.overrides()
        try:
            self._keymap.apply_overrides({slot: combo}, strict=True)
        except ValueError as exc:
            QMessageBox.warning(self, "这个按键不能用", str(exc))
            self._sync_widgets()
            return
        problems = self._keymap.validate()
        if problems:
            # 冲突时回滚，避免两个音共用一个键
            preset, mode = self._keymap.preset, self._keymap.mode
            self._keymap = KeyMap(mode, preset, previous)
            QMessageBox.warning(self, "按键冲突", "\n".join(problems))
            self._sync_widgets()
            return
        self._sync_widgets()
        self.changed.emit()

    def _sync_widgets(self) -> None:
        for key, editor in self._editors.items():
            editor.setText(self._keymap.char_for(key))
        overrides = self._keymap.overrides()
        for key, editor in self._editors.items():
            changed = KeyMap.slot_str(key.octave, key.semitone) in overrides
            editor.setStyleSheet("color: %s;" % (Theme.ACCENT if changed else Theme.TEXT))
        self.status.setText("已自定义 %d 个音位" % len(overrides) if overrides else "当前为预设布局")

    def reset_default(self) -> None:
        self._keymap.reset()
        self._sync_widgets()
        self.changed.emit()

    def set_preset(self, preset: str) -> None:
        """换预设：重置为预设默认键位。"""
        self._keymap = KeyMap(self._mode, preset)
        self._sync_widgets()
        self.changed.emit()

    def keymap(self) -> KeyMap:
        return self._keymap.copy()

    def overrides(self) -> Dict[str, str]:
        return self._keymap.overrides()

    def error_text(self) -> str:
        return "\n".join(self._keymap.validate())

    def combo_strokes(self) -> List[KeyStroke]:
        """返回当前映射里用到的全部组合键（去重，按文本排序）。"""
        seen: Dict[str, KeyStroke] = {}
        for key in self._keymap.keys():
            stroke = self._keymap.stroke_for(key)
            if stroke is not None and stroke.is_combo:
                seen[stroke.text()] = stroke
        return [seen[text] for text in sorted(seen)]


class SettingsDialog(QDialog):
    """设置窗口。"""

    def __init__(self, config: AppConfig, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("设置")
        self.setMinimumWidth(720)
        self._config = config.clone()
        self._build()
        self._load()

    # ------------------------------------------------------------------ #
    def _build(self) -> None:
        root = QVBoxLayout(self)
        self.tabs = QTabWidget()
        root.addWidget(self.tabs)
        self._build_performance_tab()
        self._build_input_tab()
        self._build_hotkey_tab()
        self._build_keymap_tab()
        self._build_chromatic_tab()
        self._build_ui_tab()

        box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        box.button(QDialogButtonBox.StandardButton.Ok).setText("保存")
        box.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        box.accepted.connect(self._on_accept)
        box.rejected.connect(self.reject)
        root.addWidget(box)

    def _build_performance_tab(self) -> None:
        page = QWidget()
        form = QFormLayout(page)

        self.hold_box = QDoubleSpinBox()
        self.hold_box.setRange(1, 500)
        self.hold_box.setSingleStep(5)
        self.hold_box.setSuffix(" ms")
        self.hold_box.setToolTip("按键按下到抬起的间隔。游戏丢键时可加大到 40~60ms")
        form.addRow("按下-抬起间隔", self.hold_box)

        self.stagger_box = QDoubleSpinBox()
        self.stagger_box.setRange(0, 20)
        self.stagger_box.setSingleStep(0.5)
        self.stagger_box.setDecimals(1)
        self.stagger_box.setSuffix(" ms")
        self.stagger_box.setToolTip(
            "和弦里多个键、以及组合键里「修饰键→主键」之间的错开量，1~2ms 最自然"
        )
        form.addRow("和弦/组合键错开", self.stagger_box)

        self.interval_box = QDoubleSpinBox()
        self.interval_box.setRange(0, 500)
        self.interval_box.setSingleStep(5)
        self.interval_box.setSuffix(" ms")
        self.interval_box.setToolTip(
            "同一个按键动作两次按下的最小间隔。游戏对极快的重复按键有节流时，把它调大（如 40ms）会自动合并连击"
        )
        form.addRow("最小按键间隔", self.interval_box)

        self.countdown_box = QDoubleSpinBox()
        self.countdown_box.setRange(0, 10)
        self.countdown_box.setSingleStep(0.5)
        self.countdown_box.setSuffix(" 秒")
        self.countdown_box.setToolTip("开始演奏前的倒计时，留给切换窗口的时间")
        form.addRow("开始倒计时", self.countdown_box)

        tip = QLabel("提示：游戏里若出现漏音，优先提高「按下-抬起间隔」；若出现连击粘连，调大「最小按键间隔」。")
        tip.setWordWrap(True)
        tip.setStyleSheet("color: %s;" % Theme.MUTED)
        form.addRow(tip)
        self.tabs.addTab(page, "演奏")

    def _build_input_tab(self) -> None:
        page = QWidget()
        root = QVBoxLayout(page)

        box = QGroupBox("按键发送方式")
        form = QFormLayout(box)
        self.backend_box = QComboBox()
        for name, display in backend_choices():
            self.backend_box.addItem(display, name)
        form.addRow("后端", self.backend_box)
        self.backend_hint = QLabel("")
        self.backend_hint.setWordWrap(True)
        self.backend_hint.setStyleSheet("color: %s;" % Theme.MUTED)
        form.addRow(self.backend_hint)
        probe_button = QPushButton("重新检测可用性")
        probe_button.clicked.connect(self._refresh_backend_hint)
        form.addRow(probe_button)
        root.addWidget(box)

        box2 = QGroupBox("目标窗口（失焦自动暂停）")
        form2 = QFormLayout(box2)
        self.keyword_edit = QLineEdit()
        self.keyword_edit.setPlaceholderText("例如 Wuthering Waves 或 鸣潮；多个关键字用 | 分隔")
        form2.addRow("窗口关键字", self.keyword_edit)
        grab = QPushButton("读取当前前台窗口")
        grab.setToolTip("切到游戏窗口后点这里，会自动填入可用的关键字")
        grab.clicked.connect(self._grab_foreground)
        form2.addRow(grab)
        self.pause_check = QCheckBox("演奏时检测到游戏窗口失去焦点就自动暂停")
        form2.addRow(self.pause_check)
        hint = QLabel(
            "关键字为空 = 不检测窗口。窗口标题与进程名任一命中即算在游戏里；"
            "只有先看到过游戏窗口，离开时才会自动暂停。"
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color: %s;" % Theme.MUTED)
        form2.addRow(hint)
        root.addWidget(box2)
        root.addStretch(1)
        self.tabs.addTab(page, "按键与窗口")

    def _build_hotkey_tab(self) -> None:
        page = QWidget()
        root = QVBoxLayout(page)
        box = QGroupBox("快捷键")
        form = QFormLayout(box)
        self.hotkey_edits: Dict[str, KeyCaptureEdit] = {}
        for name in ("mode", "start", "pause", "emergency", "exit"):
            editor = KeyCaptureEdit()
            self.hotkey_edits[name] = editor
            form.addRow(HOTKEY_LABELS[name], editor)
        root.addWidget(box)
        hint = QLabel(
            "F7 切换半音阶模式（演奏中不可切换）。F10 紧急停止会在任何时候立即松开全部按键，建议保留。\n"
            "「退出演奏模式」的 Esc 只在演奏进行中生效，平时不会抢占你的 Esc。"
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color: %s;" % Theme.MUTED)
        root.addWidget(hint)
        root.addStretch(1)
        self.tabs.addTab(page, "快捷键")

    def _build_keymap_tab(self) -> None:
        page = QWidget()
        root = QVBoxLayout(page)
        self.keymap_editor = KeymapEditor(self._config.keymap(MODE_DIATONIC))
        root.addWidget(self.keymap_editor)
        self.tabs.addTab(page, "按键映射")

    def _build_chromatic_tab(self) -> None:
        page = QWidget()
        root = QVBoxLayout(page)

        head = QGroupBox("半音阶模式")
        form = QFormLayout(head)
        self.chromatic_check = QCheckBox("启用半音阶模式（三个八度共 36 个半音）")
        self.chromatic_check.setToolTip("默认关闭。也可以在运输条上开关，或按 F7 切换")
        self.chromatic_check.toggled.connect(self._on_chromatic_toggled)
        form.addRow(self.chromatic_check)

        preset_row = QWidget()
        preset_layout = QHBoxLayout(preset_row)
        preset_layout.setContentsMargins(0, 0, 0, 0)
        self.preset_box = QComboBox()
        self.preset_box.setMinimumWidth(280)
        self.preset_box.currentIndexChanged.connect(self._on_preset_changed)
        preset_layout.addWidget(self.preset_box)
        preset_layout.addStretch(1)
        form.addRow("键位预设", preset_row)

        self.policy_box = QComboBox()
        for name, display in DOWNGRADE_POLICIES.items():
            self.policy_box.addItem(display, name)
        self.policy_box.setToolTip("从半音阶切回自然阶时，谱子里的黑键音怎么处理")
        form.addRow("黑键处理策略", self.policy_box)

        # 写谱手势：按住哪个修饰键 = 写升半音
        self.sharp_box = QComboBox()
        for name, display in SHARP_MODIFIERS.items():
            self.sharp_box.addItem(display, name)
        self.sharp_box.setToolTip(
            "写谱时按住这个修饰键再按音键，就写入该音的升半音（例如 Shift+S = 中音#2）。\n"
            "Shift 与系统 / 本程序的快捷键都不冲突，推荐使用。"
        )
        self.sharp_box.currentIndexChanged.connect(self._on_sharp_modifier_changed)
        form.addRow("升半音修饰键", self.sharp_box)
        self.sharp_hint = QLabel("")
        self.sharp_hint.setWordWrap(True)
        self.sharp_hint.setStyleSheet("color: %s;" % Theme.MUTED)
        form.addRow(self.sharp_hint)

        chord_hint = QLabel(
            "写和弦：按住<b>空格</b>不放，敲多个音（或点虚拟键盘）都进同一格，松开空格光标才前进；"
            "单击空格仍是休止符。鼠标也可以<b>右键点琴键</b>把音加入/移出当前格（不前进）。"
        )
        chord_hint.setWordWrap(True)
        chord_hint.setStyleSheet("color: %s;" % Theme.MUTED)
        form.addRow(chord_hint)
        root.addWidget(head)

        map_box = QGroupBox("36 键映射")
        map_layout = QVBoxLayout(map_box)
        self.chromatic_editor = KeymapEditor(self._config.keymap(MODE_CHROMATIC))
        map_layout.addWidget(self.chromatic_editor)

        check_row = QHBoxLayout()
        self.selfcheck_check = QCheckBox("实际发送测试按键")
        self.selfcheck_check.setToolTip("勾选后点「开始自检」会在倒计时 3 秒后真的按键，请先切到记事本观察")
        check_row.addWidget(self.selfcheck_check)
        check_button = QPushButton("组合键兼容性自检")
        check_button.clicked.connect(self._run_selfcheck)
        check_row.addWidget(check_button)
        check_row.addStretch(1)
        map_layout.addLayout(check_row)
        root.addWidget(map_box)

        hint = QLabel(
            "很多游戏只接受单一物理键（游戏读的是「键位绑定」而不是原始键盘事件），"
            "因此预设里默认用全单键的「半音阶 36 键」；FF14 风格预设使用 Ctrl/Shift 组合键，"
            "属于进阶用法，请先用自检确认游戏是否接受。"
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color: %s;" % Theme.MUTED)
        root.addWidget(hint)
        self.tabs.addTab(page, "半音阶")

    def _build_ui_tab(self) -> None:
        page = QWidget()
        form = QFormLayout(page)
        self.hotkey_enable_check = QCheckBox("启用全局快捷键（F7 切换模式、F8 开始/停止、F9 暂停、F10 紧急停止）")
        self.hotkey_enable_check.setToolTip("关掉后只能用界面上的按钮控制演奏，程序不会再挂钩系统键盘")
        form.addRow(self.hotkey_enable_check)
        self.autoscroll_check = QCheckBox("光标移动时自动滚动到可见区域")
        form.addRow(self.autoscroll_check)
        self.undo_box = QSpinBox()
        self.undo_box.setRange(10, 2000)
        self.undo_box.setSingleStep(10)
        self.undo_box.setToolTip("撤销栈最多保留多少步")
        form.addRow("撤销步数上限", self.undo_box)
        self.tabs.addTab(page, "界面")

    # ------------------------------------------------------------------ #
    def _load(self) -> None:
        config = self._config
        self.hold_box.setValue(config.key_hold_ms)
        self.stagger_box.setValue(config.chord_stagger_ms)
        self.interval_box.setValue(config.min_key_interval_ms)
        self.countdown_box.setValue(config.countdown_s)

        index = self.backend_box.findData(config.backend)
        if index >= 0:
            self.backend_box.setCurrentIndex(index)
        self._refresh_backend_hint()
        self.keyword_edit.setText(config.target_window_keyword)
        self.pause_check.setChecked(config.pause_on_focus_loss)

        for name, editor in self.hotkey_edits.items():
            editor.setText(config.hotkeys.get(name, DEFAULT_HOTKEYS[name]))

        self.hotkey_enable_check.setChecked(config.enable_hotkeys)
        self.autoscroll_check.setChecked(config.autoscroll)
        self.undo_box.setValue(config.max_undo)

        self.chromatic_check.setChecked(config.chromatic_enabled)
        self._fill_presets(config.chromatic_preset)
        policy_index = self.policy_box.findData(config.chromatic_downgrade_policy)
        if policy_index >= 0:
            self.policy_box.setCurrentIndex(policy_index)
        sharp_index = self.sharp_box.findData(config.sharp_modifier)
        if sharp_index >= 0:
            self.sharp_box.setCurrentIndex(sharp_index)
        self._on_sharp_modifier_changed(self.sharp_box.currentIndex())
        self._on_chromatic_toggled(config.chromatic_enabled)

    #: 用 Ctrl 当升半音修饰键时会被本程序快捷键抢走的组合
    _CTRL_CONFLICTS = "Ctrl+S 保存、Ctrl+Z/Y 撤销重做、Ctrl+C/V/X 复制粘贴、Ctrl+A 全选、Ctrl+D 复制乐句、Ctrl+R 音域检查、Ctrl+N/O/Q 新建/打开/退出、Ctrl+, 设置"

    def _on_sharp_modifier_changed(self, index: int) -> None:
        choice = self.sharp_box.itemData(index)
        if choice == "shift":
            self.sharp_hint.setText("按住 Shift + 音键 = 写升半音（例如 Shift+S = 中音#2）。与系统及本程序快捷键都不冲突，推荐。")
            self.sharp_hint.setStyleSheet("color: %s;" % Theme.OK)
        elif choice == "ctrl":
            self.sharp_hint.setText(
                "⚠ 用 Ctrl 会与这些快捷键冲突，它们会优先执行而写不出音：%s。建议改回 Shift。" % self._CTRL_CONFLICTS
            )
            self.sharp_hint.setStyleSheet("color: %s;" % Theme.WARN)
        elif choice == "alt":
            self.sharp_hint.setText("⚠ Alt 会触发菜单助记键、并与 Alt+Tab 冲突，不建议使用。")
            self.sharp_hint.setStyleSheet("color: %s;" % Theme.WARN)
        else:
            self.sharp_hint.setText("不设修饰键：改用 `#` 前缀输入（先按 # 再按音键）。")
            self.sharp_hint.setStyleSheet("color: %s;" % Theme.MUTED)

    def _fill_presets(self, current: str) -> None:
        from PySide6.QtCore import QSignalBlocker

        with QSignalBlocker(self.preset_box):
            self.preset_box.clear()
            for name in preset_names_for(MODE_CHROMATIC):
                self.preset_box.addItem(PRESET_NAMES.get(name, name), name)
            index = self.preset_box.findData(current)
            if index >= 0:
                self.preset_box.setCurrentIndex(index)

    def _on_chromatic_toggled(self, checked: bool) -> None:
        self.preset_box.setEnabled(checked)
        self.policy_box.setEnabled(checked)
        self.chromatic_editor.setEnabled(checked)
        self.selfcheck_check.setEnabled(checked)

    def _on_preset_changed(self, index: int) -> None:
        preset = self.preset_box.itemData(index)
        if not preset:
            return
        if self.chromatic_editor.overrides():
            answer = QMessageBox.question(
                self,
                "切换预设",
                "切换预设会把 36 键映射恢复成该预设的默认布局，当前的按键修改会丢失。继续吗？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
        self.chromatic_editor.set_preset(str(preset))

    def _refresh_backend_hint(self) -> None:
        kind = self.backend_box.currentData()
        results = probe_backends()
        available, message = results.get(kind, (False, "未知后端"))
        if available:
            self.backend_hint.setText("✅ 该后端可用")
        else:
            self.backend_hint.setText("⚠ 该后端当前不可用：%s" % message)

    def _grab_foreground(self) -> None:
        hwnd, title, process = foreground_info()
        if not title and not process:
            QMessageBox.information(self, "读取失败", "没有读到前台窗口信息（可能是权限不足）。")
            return
        guess = process or title
        self.keyword_edit.setText(guess)
        QMessageBox.information(
            self,
            "已填入关键字",
            "当前前台窗口：\n标题：%s\n进程：%s\n\n已填入：%s" % (title or "（空）", process or "（空）", guess),
        )

    # ------------------------------------------------------------------ #
    def _run_selfcheck(self) -> None:
        """组合键兼容性自检。"""
        problems = self.chromatic_editor.error_text()
        if problems:
            QMessageBox.warning(self, "映射有问题", problems)
            return
        strokes = self.chromatic_editor.combo_strokes()
        if not strokes:
            QMessageBox.information(
                self, "组合键自检", "当前 36 键映射里没有使用组合键，全部是单键，兼容性最好，无需自检。"
            )
            return
        listing = "、".join(stroke.text() for stroke in strokes)
        if not self.selfcheck_check.isChecked():
            QMessageBox.information(
                self,
                "组合键自检",
                "当前映射用到 %d 个组合键：\n%s\n\n"
                "① 勾选「实际发送测试按键」后点本按钮，程序会倒计时 3 秒再依次发送这些组合键；\n"
                "② 请先把光标放进记事本，看能否打出对应字符（说明系统层面 OK）；\n"
                "③ 再切到游戏，用同样的组合键试弹一次，能出声才说明游戏接受该组合键。"
                % (len(strokes), listing),
            )
            return

        QMessageBox.information(self, "准备发送", "点确定后有 3 秒时间切换到记事本或游戏窗口。")
        backend = None
        try:
            backend = create_backend(self._config.backend)
        except BackendError as exc:
            QMessageBox.critical(self, "后端不可用", str(exc))
            return
        try:
            deadline = time.perf_counter() + 3.0
            while time.perf_counter() < deadline:
                QApplication.processEvents()
                time.sleep(0.02)
            for stroke in strokes:
                for name in stroke.modifiers:
                    backend.key_down(name)
                backend.key_down(stroke.main)
                time.sleep(0.06)
                backend.key_up(stroke.main)
                for name in reversed(stroke.modifiers):
                    backend.key_up(name)
                time.sleep(0.35)
                QApplication.processEvents()
        finally:
            try:
                backend.release_all()
                backend.close()
            except Exception:
                pass
        QMessageBox.information(self, "自检完成", "已依次发送：%s" % listing)

    # ------------------------------------------------------------------ #
    def _on_accept(self) -> None:
        config = self._config
        config.key_hold_ms = float(self.hold_box.value())
        config.chord_stagger_ms = float(self.stagger_box.value())
        config.min_key_interval_ms = float(self.interval_box.value())
        config.countdown_s = float(self.countdown_box.value())
        config.backend = str(self.backend_box.currentData())
        config.target_window_keyword = self.keyword_edit.text().strip()
        config.pause_on_focus_loss = bool(self.pause_check.isChecked())
        config.hotkeys = {name: normalize_hotkey(editor.text()) for name, editor in self.hotkey_edits.items()}
        config.enable_hotkeys = bool(self.hotkey_enable_check.isChecked())
        config.autoscroll = bool(self.autoscroll_check.isChecked())
        config.max_undo = int(self.undo_box.value())

        # 自然阶映射
        natural_problems = self.keymap_editor.error_text()
        if natural_problems:
            QMessageBox.warning(self, "自然阶按键映射有问题", natural_problems)
            self.tabs.setCurrentIndex(3)
            return
        config.store_keymap(self.keymap_editor.keymap())

        # 半音阶设置
        config.chromatic_enabled = bool(self.chromatic_check.isChecked())
        config.chromatic_downgrade_policy = str(self.policy_box.currentData() or "ask")
        config.sharp_modifier = str(self.sharp_box.currentData() or "shift")
        chromatic_problems = self.chromatic_editor.error_text()
        if config.chromatic_enabled and chromatic_problems:
            QMessageBox.warning(self, "半音阶按键映射有问题", chromatic_problems)
            self.tabs.setCurrentIndex(4)
            return
        config.store_keymap(self.chromatic_editor.keymap())

        problems = find_conflicts(config.hotkeys)
        if problems:
            QMessageBox.warning(self, "快捷键冲突", "\n".join(problems))
            self.tabs.setCurrentIndex(2)
            return
        sanitize_notes = config.sanitize()
        if sanitize_notes:
            QMessageBox.information(self, "部分设置已自动修正", "\n".join(sanitize_notes))
        self.accept()

    def result_config(self) -> AppConfig:
        return self._config
