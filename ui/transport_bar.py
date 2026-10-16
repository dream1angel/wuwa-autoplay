# -*- coding: utf-8 -*-
"""运输条：BPM / 拍号 / 细分 / 变速 / 播放控制 / 进度与状态。"""

from __future__ import annotations

from typing import Optional, Tuple

from PySide6.QtCore import QSignalBlocker, Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from core.engine import EngineState, format_duration
from core.model import ALLOWED_SUBDIVISIONS, ALLOWED_TIME_SIGNATURES, BPM_MAX, BPM_MIN, SPEED_MAX, SPEED_MIN, ScoreMeta
from core.notes import (
    MODE_CHROMATIC,
    MODE_DIATONIC,
    PRESET_NAMES,
    default_preset,
    preset_names_for,
)

from .theme import Theme

__all__ = ["TransportBar"]

_TIME_SIGNATURES: Tuple[Tuple[int, int], ...] = ALLOWED_TIME_SIGNATURES
_SUBDIVISION_LABELS = {
    4: "1/4 拍（每拍 4 格）",
    8: "1/8 拍（每拍 8 格）",
    16: "1/16 拍（每拍 16 格）",
    32: "1/32 拍（每拍 32 格）",
}


class TransportBar(QWidget):
    """顶部控制条。"""

    bpmChanged = Signal(int)
    timeSignatureChanged = Signal(object)   # (分子, 分母)
    subdivisionChanged = Signal(int)
    speedChanged = Signal(float)
    startClicked = Signal()
    pauseClicked = Signal()
    settingsClicked = Signal()
    modeToggled = Signal(bool)              # 半音阶模式开关
    presetChanged = Signal(str)             # 半音阶预设名

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("transportBar")
        self._build()
        self.set_state(EngineState.IDLE)

    # ------------------------------------------------------------------ #
    # 构建
    # ------------------------------------------------------------------ #
    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(10, 6, 10, 6)
        root.setSpacing(6)

        # ---- 第 0 行：音阶模式 ----
        mode_row = QHBoxLayout()
        mode_row.setSpacing(8)
        mode_row.addWidget(QLabel("音阶模式"))
        self.chromatic_check = QCheckBox("半音阶模式（36 键 · F7 切换）")
        self.chromatic_check.setToolTip(
            "默认关闭。开启后可编辑三个八度共 36 个半音，使用可自定义的键位映射（支持组合键）"
        )
        self.chromatic_check.toggled.connect(self.modeToggled.emit)
        mode_row.addWidget(self.chromatic_check)

        mode_row.addWidget(QLabel("预设"))
        self.preset_box = QComboBox()
        self.preset_box.setMinimumWidth(230)
        self.preset_box.currentIndexChanged.connect(self._emit_preset)
        mode_row.addWidget(self.preset_box)

        self.mode_label = QLabel("自然阶 21 键")
        self.mode_label.setStyleSheet("color: %s;" % Theme.MUTED)
        mode_row.addWidget(self.mode_label)
        mode_row.addStretch(1)
        self.mapping_label = QLabel("")
        self.mapping_label.setStyleSheet("color: %s; font-weight: bold;" % Theme.ERROR)
        mode_row.addWidget(self.mapping_label)
        root.addLayout(mode_row)
        self.set_mode_state(MODE_DIATONIC, default_preset(MODE_DIATONIC))

        row = QHBoxLayout()
        row.setSpacing(8)

        row.addWidget(QLabel("BPM"))
        self.bpm_box = QSpinBox()
        self.bpm_box.setRange(BPM_MIN, BPM_MAX)
        self.bpm_box.setValue(120)
        self.bpm_box.setFixedWidth(70)
        self.bpm_box.setToolTip("每分钟拍数（40 ~ 300）")
        self.bpm_box.valueChanged.connect(self.bpmChanged.emit)
        row.addWidget(self.bpm_box)

        row.addWidget(QLabel("拍号"))
        self.signature_box = QComboBox()
        for num, den in _TIME_SIGNATURES:
            self.signature_box.addItem("%d/%d" % (num, den), (num, den))
        self.signature_box.setFixedWidth(70)
        self.signature_box.setToolTip("只影响小节线位置与默认网格")
        self.signature_box.currentIndexChanged.connect(self._emit_signature)
        row.addWidget(self.signature_box)

        row.addWidget(QLabel("细分"))
        self.subdivision_box = QComboBox()
        for value in ALLOWED_SUBDIVISIONS:
            self.subdivision_box.addItem(_SUBDIVISION_LABELS[value], value)
        self.subdivision_box.setCurrentIndex(0)
        self.subdivision_box.setFixedWidth(170)
        self.subdivision_box.setToolTip("每一拍切成几格，格是最小输入单位")
        self.subdivision_box.currentIndexChanged.connect(self._emit_subdivision)
        row.addWidget(self.subdivision_box)

        row.addWidget(QLabel("变速"))
        self.speed_box = QDoubleSpinBox()
        self.speed_box.setRange(SPEED_MIN, SPEED_MAX)
        self.speed_box.setSingleStep(0.05)
        self.speed_box.setDecimals(2)
        self.speed_box.setSuffix(" x")
        self.speed_box.setValue(1.0)
        self.speed_box.setFixedWidth(80)
        self.speed_box.setToolTip("0.5x ~ 2.0x，演奏速度倍率")
        self.speed_box.valueChanged.connect(self.speedChanged.emit)
        row.addWidget(self.speed_box)

        row.addSpacing(12)
        self.start_button = QPushButton("▶ 开始演奏 (F8)")
        self.start_button.setObjectName("primary")
        self.start_button.setMinimumWidth(140)
        self.start_button.clicked.connect(self.startClicked.emit)
        row.addWidget(self.start_button)

        self.pause_button = QPushButton("⏸ 暂停 (F9)")
        self.pause_button.setMinimumWidth(110)
        self.pause_button.setEnabled(False)
        self.pause_button.clicked.connect(self.pauseClicked.emit)
        row.addWidget(self.pause_button)

        self.settings_button = QPushButton("⚙ 设置")
        self.settings_button.setMinimumWidth(80)
        self.settings_button.clicked.connect(self.settingsClicked.emit)
        row.addWidget(self.settings_button)

        row.addStretch(1)
        self.status_label = QLabel("就绪")
        self.status_label.setStyleSheet("color: %s;" % Theme.MUTED)
        row.addWidget(self.status_label)
        root.addLayout(row)

        row2 = QHBoxLayout()
        row2.setSpacing(8)
        self.progress = QProgressBar()
        self.progress.setRange(0, 1000)
        self.progress.setValue(0)
        self.progress.setTextVisible(False)
        self.progress.setFixedHeight(14)
        row2.addWidget(self.progress, 1)

        self.time_label = QLabel("0:00.0 / 0:00.0")
        self.time_label.setFont(Theme.mono_font(9))
        self.time_label.setMinimumWidth(130)
        self.time_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        row2.addWidget(self.time_label)

        self.bar_label = QLabel("第 0 / 0 小节")
        self.bar_label.setMinimumWidth(110)
        self.bar_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.bar_label.setStyleSheet("color: %s;" % Theme.MUTED)
        row2.addWidget(self.bar_label)
        root.addLayout(row2)

    # ------------------------------------------------------------------ #
    # 信号转发
    # ------------------------------------------------------------------ #
    def _emit_signature(self, index: int) -> None:
        data = self.signature_box.itemData(index)
        if data:
            self.timeSignatureChanged.emit(tuple(data))

    def _emit_subdivision(self, index: int) -> None:
        data = self.subdivision_box.itemData(index)
        if data:
            self.subdivisionChanged.emit(int(data))

    def _emit_preset(self, index: int) -> None:
        name = self.preset_box.itemData(index)
        if name:
            self.presetChanged.emit(str(name))

    # ------------------------------------------------------------------ #
    # 音阶模式
    # ------------------------------------------------------------------ #
    def current_mode(self) -> str:
        return MODE_CHROMATIC if self.chromatic_check.isChecked() else MODE_DIATONIC

    def current_preset(self) -> str:
        return str(self.preset_box.currentData() or default_preset(self.current_mode()))

    def set_mode_state(self, mode: str, preset: str) -> None:
        """同步模式开关与预设下拉（不触发信号）。"""
        chromatic = mode == MODE_CHROMATIC
        with QSignalBlocker(self.chromatic_check):
            self.chromatic_check.setChecked(chromatic)
        self._fill_presets(mode, preset)
        count = 36 if chromatic else 21
        self.mode_label.setText(
            "%s %d 键%s"
            % ("半音阶" if chromatic else "自然阶", count, "" if chromatic else "（游戏内默认布局）")
        )
        # 自然阶模式没有可选预设，禁用下拉避免误解
        self.preset_box.setEnabled(chromatic)

    def _fill_presets(self, mode: str, current: str) -> None:
        with QSignalBlocker(self.preset_box):
            self.preset_box.clear()
            for name in preset_names_for(mode):
                self.preset_box.addItem(PRESET_NAMES.get(name, name), name)
            index = self.preset_box.findData(current)
            if index < 0 and self.preset_box.count():
                index = 0
            if index >= 0:
                self.preset_box.setCurrentIndex(index)

    def set_mapping_problem(self, text: str) -> None:
        """在运输条右侧显示「有 N 个音无映射」之类的红字提示。"""
        self.mapping_label.setText(text)

    # ------------------------------------------------------------------ #
    # 状态同步
    # ------------------------------------------------------------------ #
    def set_meta(self, meta: ScoreMeta, speed: float) -> None:
        """把曲谱元信息回填到控件（不触发信号）。"""
        with QSignalBlocker(self.bpm_box), QSignalBlocker(self.signature_box), QSignalBlocker(self.subdivision_box):
            self.bpm_box.setValue(int(meta.bpm))
            index = self.signature_box.findData(tuple(meta.time_signature))
            if index < 0:
                self.signature_box.addItem(meta.time_signature_text, tuple(meta.time_signature))
                index = self.signature_box.count() - 1
            self.signature_box.setCurrentIndex(index)
            sub_index = self.subdivision_box.findData(int(meta.subdivision))
            if sub_index >= 0:
                self.subdivision_box.setCurrentIndex(sub_index)
        with QSignalBlocker(self.speed_box):
            self.speed_box.setValue(float(speed))

    def set_state(self, state: EngineState) -> None:
        """按引擎状态切换按钮可用性与文字。"""
        busy = state in (EngineState.PLAYING, EngineState.PAUSED, EngineState.COUNTDOWN)
        self.start_button.setText("■ 停止 (F8)" if busy else "▶ 开始演奏 (F8)")
        self.start_button.setObjectName("danger" if busy else "primary")
        self.start_button.setStyleSheet("")  # 让 QSS 重新应用 objectName 样式
        self.start_button.style().unpolish(self.start_button)
        self.start_button.style().polish(self.start_button)
        self.pause_button.setEnabled(busy)
        self.pause_button.setText("▶ 继续 (F9)" if state == EngineState.PAUSED else "⏸ 暂停 (F9)")
        self.status_label.setText(state.text)
        color = {
            EngineState.PLAYING: Theme.OK,
            EngineState.PAUSED: Theme.WARN,
            EngineState.COUNTDOWN: Theme.ACCENT,
            EngineState.ERROR: Theme.ERROR,
        }.get(state, Theme.MUTED)
        self.status_label.setStyleSheet("color: %s; font-weight: bold;" % color)
        # 只在回到「就绪」时清空进度；演奏结束时保留满格，让用户看到「已弹完」
        if state == EngineState.IDLE:
            self.progress.setValue(0)
            self.bar_label.setText("第 0 / 0 小节")

    def set_progress(self, elapsed_ms: float, total_ms: float, bar: int, bar_count: int) -> None:
        total = max(1.0, float(total_ms))
        ratio = max(0.0, min(1.0, float(elapsed_ms) / total))
        self.progress.setValue(int(ratio * 1000))
        self.time_label.setText("%s / %s" % (format_duration(elapsed_ms), format_duration(total)))
        self.bar_label.setText("第 %d / %d 小节" % (min(bar + 1, max(1, bar_count)), max(1, bar_count)))

    def reset_progress(self, total_ms: float = 0.0) -> None:
        self.progress.setValue(0)
        self.time_label.setText("0:00.0 / %s" % format_duration(total_ms))
        self.bar_label.setText("第 0 / 0 小节")

    def set_status_text(self, text: str, level: str = "info") -> None:
        color = {"info": Theme.MUTED, "warning": Theme.WARN, "error": Theme.ERROR, "ok": Theme.OK}.get(
            level, Theme.MUTED
        )
        self.status_label.setText(text)
        self.status_label.setStyleSheet("color: %s; font-weight: bold;" % color)

    def set_speed(self, speed: float) -> None:
        with QSignalBlocker(self.speed_box):
            self.speed_box.setValue(float(speed))

    @property
    def speed(self) -> float:
        return float(self.speed_box.value())

    @property
    def bpm(self) -> int:
        return int(self.bpm_box.value())

    @property
    def subdivision(self) -> int:
        return int(self.subdivision_box.currentData() or 4)

    @property
    def time_signature(self) -> Tuple[int, int]:
        data = self.signature_box.currentData() or (4, 4)
        return (int(data[0]), int(data[1]))
