# -*- coding: utf-8 -*-
"""音高模型、按键映射与两种音阶模式（自然阶 21 键 / 半音阶 36 键）。

设计要点
--------
1. **规范音高**：内部统一用 ``Key(octave, semitone)``：
   - ``octave ∈ {-1, 0, 1}``（低 / 中 / 高音区）
   - ``semitone ∈ 0..11``（0 = do，1 = #do，…，11 = si）
   - ``pitch = octave * 12 + semitone``（排序、移调、比较都用它）
   - ``midi = REFERENCE_MIDI + pitch``（默认中音 do = C4 = 60）
   自然阶模式只是「只用 7 个自然音 semitone」的特例，``degree`` 变成派生属性。
2. **曲谱只存音高**，按键完全由 :class:`KeyMap` 提供；按键可以是单键（``q``）
   也可以是组合键（``shift+z``），由 :class:`KeyStroke` 解析与规范化。
3. **两套映射表**：自然阶一张、半音阶一张，各带三套内置预设，互不影响。

模式对照::

    semitone   0    1    2    3    4    5    6    7    8    9   10   11
    简谱       1   #1    2   #2    3    4   #4    5   #5    6   #6    7
    音名       C   C#    D   D#    E    F   F#    G   G#    A   A#    B
    白/黑      白   黑    白   黑    白   白   黑    白   黑    白   黑    白
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Dict, Iterable, Iterator, List, Optional, Sequence, Tuple

__all__ = [
    "MODE_DIATONIC",
    "MODE_CHROMATIC",
    "MODES",
    "MODE_NAMES",
    "Key",
    "KeyStroke",
    "KeyMap",
    "DEFAULT_KEYMAP",
    "OCTAVE_MIN",
    "OCTAVE_MAX",
    "DEGREE_MIN",
    "DEGREE_MAX",
    "SEMITONE_MIN",
    "SEMITONE_MAX",
    "OCTAVE_NAMES",
    "SEMITONE_NAMES",
    "SEMITONE_JIANPU",
    "DEGREE_TO_SEMITONE",
    "SEMITONE_TO_DEGREE",
    "NATURAL_SEMITONES",
    "SHARP_SEMITONES",
    "REFERENCE_MIDI",
    "PRESET_DIATONIC",
    "PRESET_CHROMATIC",
    "PRESET_FF14",
    "PRESET_CUSTOM",
    "PRESET_NAMES",
    "all_keys",
    "chromatic_keys",
    "diatonic_keys",
    "is_in_range",
    "key_sort_index",
    "format_key_label",
    "preset_names_for",
    "default_preset",
    "preset_mapping",
]

# --------------------------------------------------------------------------- #
# 常量
# --------------------------------------------------------------------------- #

MODE_DIATONIC = "diatonic"
MODE_CHROMATIC = "chromatic"
MODES: Tuple[str, ...] = (MODE_DIATONIC, MODE_CHROMATIC)
MODE_NAMES: Dict[str, str] = {MODE_DIATONIC: "自然阶", MODE_CHROMATIC: "半音阶"}

OCTAVE_MIN, OCTAVE_MAX = -1, 1
DEGREE_MIN, DEGREE_MAX = 1, 7
SEMITONE_MIN, SEMITONE_MAX = 0, 11

#: 音区中文名（界面提示用）
OCTAVE_NAMES: Dict[int, str] = {-1: "低音", 0: "中音", 1: "高音"}

#: 音级 → 半音（自然音）
DEGREE_TO_SEMITONE: Dict[int, int] = {1: 0, 2: 2, 3: 4, 4: 5, 5: 7, 6: 9, 7: 11}
SEMITONE_TO_DEGREE: Dict[int, int] = {value: degree for degree, value in DEGREE_TO_SEMITONE.items()}
NATURAL_SEMITONES: Tuple[int, ...] = tuple(sorted(DEGREE_TO_SEMITONE.values()))
SHARP_SEMITONES: Tuple[int, ...] = tuple(s for s in range(12) if s not in NATURAL_SEMITONES)

SEMITONE_NAMES: Tuple[str, ...] = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")
SEMITONE_JIANPU: Tuple[str, ...] = ("1", "#1", "2", "#2", "3", "4", "#4", "5", "#5", "6", "#6", "7")

#: 中音 do 对应的 MIDI 号（C4 = 60）
REFERENCE_MIDI = 60

#: 预设名
PRESET_DIATONIC = "diatonic-21"
PRESET_CHROMATIC = "chromatic-36"
PRESET_FF14 = "ff14-style"
PRESET_CUSTOM = "custom"

PRESET_NAMES: Dict[str, str] = {
    PRESET_DIATONIC: "鸣潮默认 21 键",
    PRESET_CHROMATIC: "全单键（推荐，兼容性最好）",
    PRESET_FF14: "FF14 风格（钢琴式交错 + 修饰键分档）",
    PRESET_CUSTOM: "自定义",
}

_KEYS_PER_OCTAVE = 12

# --------------------------------------------------------------------------- #
# Key：规范音高
# --------------------------------------------------------------------------- #

@dataclass(frozen=True, order=True)
class Key:
    """一个音：``octave`` + ``semitone``。

    ``order=True`` 让 Key 可以排序（先比 octave 再比 semitone），便于生成稳定
    的 JSON 输出；界面顺序另用 :func:`key_sort_index`（高音在上）。
    """

    octave: int
    semitone: int

    # ---- 构造 ---------------------------------------------------------- #
    @staticmethod
    def from_degree(octave: int, degree: int) -> "Key":
        """由「音区 + 音级 1~7」构造（自然音）。越界音级会折算到相邻八度。"""
        degree = int(degree)
        octave = int(octave)
        while degree > DEGREE_MAX:
            degree -= DEGREE_MAX - DEGREE_MIN + 1
            octave += 1
        while degree < DEGREE_MIN:
            degree += DEGREE_MAX - DEGREE_MIN + 1
            octave -= 1
        return Key(octave, DEGREE_TO_SEMITONE[degree])

    @staticmethod
    def from_semitone(octave: int, semitone: int) -> "Key":
        """由「音区 + 半音 0~11」构造；半音越界会折算到相邻八度。"""
        return Key.from_pitch(int(octave) * _KEYS_PER_OCTAVE + int(semitone))

    @staticmethod
    def from_pitch(pitch: int) -> "Key":
        """由绝对半音序号（`octave*12 + semitone`）构造。"""
        octave, semitone = divmod(int(pitch), _KEYS_PER_OCTAVE)
        return Key(octave, semitone)

    @staticmethod
    def from_midi(midi: int, reference: int = REFERENCE_MIDI) -> "Key":
        """由 MIDI 号构造（默认中音 do = C4 = 60）。"""
        return Key.from_pitch(int(midi) - int(reference))

    # ---- 派生属性 ------------------------------------------------------ #
    @property
    def pitch(self) -> int:
        """绝对半音序号，跨八度可比大小。"""
        return self.octave * _KEYS_PER_OCTAVE + self.semitone

    @property
    def midi(self) -> int:
        return REFERENCE_MIDI + self.pitch

    def to_midi(self, reference: int = REFERENCE_MIDI) -> int:
        return int(reference) + self.pitch

    @property
    def is_sharp(self) -> bool:
        """是否黑键（非自然音）。"""
        return self.semitone not in NATURAL_SEMITONES

    @property
    def degree(self) -> Optional[int]:
        """自然音返回 1~7；黑键返回 ``None``。"""
        return SEMITONE_TO_DEGREE.get(self.semitone)

    def is_valid(self, mode: str = MODE_CHROMATIC) -> bool:
        """是否落在当前模式的合法音域内。"""
        if not (OCTAVE_MIN <= self.octave <= OCTAVE_MAX):
            return False
        if not (SEMITONE_MIN <= self.semitone <= SEMITONE_MAX):
            return False
        if mode == MODE_DIATONIC and self.semitone not in NATURAL_SEMITONES:
            return False
        return True

    # ---- 展示 ---------------------------------------------------------- #
    def label(self) -> str:
        """如 ``中音#1``；越界时显示原始数值，绝不抛异常（校验流程要靠它报错）。"""
        name = OCTAVE_NAMES.get(self.octave, "音区%d" % self.octave)
        return "%s%s" % (name, self.short_label())

    def short_label(self) -> str:
        """如 ``#1``（简谱，含升号）；越界时显示 ``[半音12]`` 形式。"""
        if SEMITONE_MIN <= self.semitone <= SEMITONE_MAX:
            return SEMITONE_JIANPU[self.semitone]
        return "[半音%d]" % self.semitone

    def octave_dot(self) -> int:
        """简谱八度点位置：``1`` = 数字上方点（高音），``-1`` = 下方点（低音），``0`` = 中音无点。

        编辑器与 PNG/PDF 导出统一用这个约定：单数字 = 中音，上点 = 高音，下点 = 低音，
        升半音仍在数字前加 ``#``（如 ``#1``）。
        """
        if self.octave > 0:
            return 1
        if self.octave < 0:
            return -1
        return 0

    def jianpu_ascii(self) -> str:
        """纯文本场合的回退写法：``1``（中音）/ ``^1``（高音）/ ``_1``（低音）/ ``^#4``。"""
        prefix = "^" if self.octave > 0 else ("_" if self.octave < 0 else "")
        return prefix + self.short_label()

    def jianpu_text(self) -> str:
        """等价于 :meth:`short_label`，语义更明确（简谱文字部分，不含八度点）。"""
        return self.short_label()

    def note_name(self) -> str:
        """如 ``C4`` / ``F#5``（科学音高记号，中音 do = C4）。"""
        if SEMITONE_MIN <= self.semitone <= SEMITONE_MAX:
            return "%s%d" % (SEMITONE_NAMES[self.semitone], self.octave + 4)
        return "MIDI%d" % self.midi

    # ---- 变换 ---------------------------------------------------------- #
    def shifted(self, degree_steps: int = 0, octave_steps: int = 0) -> "Key":
        """自然阶语义的移调（按音级循环）；黑键按半音平移。"""
        octave = self.octave + int(octave_steps)
        if self.is_sharp:
            return Key.from_pitch(octave * _KEYS_PER_OCTAVE + self.semitone + int(degree_steps))
        degree = int(self.degree or 1) + int(degree_steps)
        while degree > DEGREE_MAX:
            degree -= DEGREE_MAX - DEGREE_MIN + 1
            octave += 1
        while degree < DEGREE_MIN:
            degree += DEGREE_MAX - DEGREE_MIN + 1
            octave -= 1
        return Key.from_degree(octave, degree)

    def shifted_semitones(self, steps: int) -> "Key":
        """按半音移调（±1 = 半音，±12 = 八度）。"""
        return Key.from_pitch(self.pitch + int(steps))

    def shifted_octaves(self, steps: int) -> "Key":
        return Key.from_pitch(self.pitch + int(steps) * _KEYS_PER_OCTAVE)

    # ---- 序列化 -------------------------------------------------------- #
    def to_json(self, mode: str = MODE_CHROMATIC, downgrade: bool = True) -> Dict[str, object]:
        """序列化。

        自然阶模式写 ``degree``；半音阶模式写 ``semitone``，
        黑键可选附带一个近似 ``degree``（取下方自然音），方便旧版程序降级读取。
        """
        if mode == MODE_DIATONIC and not self.is_sharp:
            return {"octave": int(self.octave), "degree": int(self.degree or 1)}
        payload: Dict[str, object] = {"octave": int(self.octave), "semitone": int(self.semitone)}
        if downgrade and self.is_sharp:
            nearest = self.semitone - 1
            while nearest > 0 and nearest not in NATURAL_SEMITONES:
                nearest -= 1
            payload["degree"] = int(SEMITONE_TO_DEGREE.get(nearest, 1))
        return payload

    @staticmethod
    def from_json(data: object) -> "Key":
        """解析音符项，优先级：``midi`` > ``semitone`` > ``degree``。"""
        if not isinstance(data, dict):
            raise ValueError("音符项必须是对象，实际为 %s" % type(data).__name__)
        if data.get("midi") is not None:
            try:
                return Key.from_midi(int(data["midi"]))
            except (TypeError, ValueError) as exc:
                raise ValueError("midi 必须是整数：%r" % (data.get("midi"),)) from exc
        try:
            octave = int(data["octave"])
        except KeyError as exc:
            raise ValueError("音符缺少字段 %s" % exc) from exc
        except (TypeError, ValueError) as exc:
            raise ValueError("octave 必须是整数：%r" % (data.get("octave"),)) from exc
        if data.get("semitone") is not None:
            try:
                return Key(octave, int(data["semitone"]))
            except (TypeError, ValueError) as exc:
                raise ValueError("semitone 必须是整数：%r" % (data.get("semitone"),)) from exc
        if data.get("degree") is not None:
            try:
                return Key.from_degree(octave, int(data["degree"]))
            except (TypeError, ValueError) as exc:
                raise ValueError("degree 必须是整数：%r" % (data.get("degree"),)) from exc
        raise ValueError("音符必须给出 midi、semitone 或 degree 之一")


# --------------------------------------------------------------------------- #
# KeyStroke：单键或组合键
# --------------------------------------------------------------------------- #

_MODIFIERS: Tuple[str, ...] = ("ctrl", "alt", "shift")
_MODIFIER_ALIASES = {
    "control": "ctrl",
    "ctl": "ctrl",
    "ctrl": "ctrl",
    "alt": "alt",
    "menu": "alt",
    "shift": "shift",
    "⇧": "shift",
    "⌃": "ctrl",
    "win": "windows",
    "windows": "windows",
    "meta": "windows",
}
_PUNCTUATION = set(",./;'[]-=\\`")
_MAIN_KEY_RE = re.compile(r"^(?:[a-z0-9]|f(?:[1-9]|1[0-2])|[,./;'\[\]=\\-`]|space|tab|enter|backspace)$")


@dataclass(frozen=True)
class KeyStroke:
    """一个物理按键动作：主键 + 可选修饰键。"""

    main: str
    ctrl: bool = False
    alt: bool = False
    shift: bool = False

    # ---- 构造 ---------------------------------------------------------- #
    @staticmethod
    def single(main: str) -> "KeyStroke":
        return KeyStroke.parse(main)

    @staticmethod
    def parse(text: object) -> "KeyStroke":
        """解析 ``"Ctrl+Shift+Z"`` / ``"z"`` / ``"shift+1"`` 这类写法。"""
        raw = str(text or "").strip().lower().replace("＋", "+").replace(" ", "")
        if not raw:
            raise ValueError("按键不能为空")
        parts = [part for part in raw.split("+") if part]
        if not parts:
            raise ValueError("按键不能为空")
        mods = {"ctrl": False, "alt": False, "shift": False}
        main = ""
        for part in parts:
            alias = _MODIFIER_ALIASES.get(part)
            if alias in _MODIFIERS:
                mods[alias] = True
                continue
            if alias is not None:
                raise ValueError("不支持修饰键 %r（支持 ctrl / alt / shift）" % part)
            if main:
                raise ValueError("组合键只能有一个主键：%r" % text)
            main = part
        if not main:
            raise ValueError("组合键 %r 缺少主键" % text)
        if not _MAIN_KEY_RE.match(main):
            raise ValueError("主键 %r 不是可用的按键（字母/数字/F1~F12/部分符号）" % main)
        return KeyStroke(main, mods["ctrl"], mods["alt"], mods["shift"])

    # ---- 展示 ---------------------------------------------------------- #
    @property
    def modifiers(self) -> Tuple[str, ...]:
        """按固定顺序返回修饰键（ctrl → alt → shift）。"""
        return tuple(name for name in _MODIFIERS if getattr(self, name))

    @property
    def is_combo(self) -> bool:
        return bool(self.modifiers)

    def text(self) -> str:
        """规范化写法，如 ``ctrl+shift+z``。"""
        return "+".join(list(self.modifiers) + [self.main])

    def display(self) -> str:
        """界面键帽写法，如 ``⇧Z``、``C+2``、``Z``。"""
        prefix = ""
        if self.ctrl:
            prefix += "C+"
        if self.alt:
            prefix += "A+"
        if self.shift:
            prefix += "⇧"
        return "%s%s" % (prefix, self.main.upper())

    def __str__(self) -> str:  # pragma: no cover - 展示用
        return self.text()


# --------------------------------------------------------------------------- #
# 预设映射表
# --------------------------------------------------------------------------- #

#: 自然阶默认键位（游戏内 21 键）
_DIATONIC_ROWS: Dict[int, str] = {1: "qwertyu", 0: "asdfghj", -1: "zxcvbnm"}

#: 半音阶预设 A：白键沿用自然阶（手感不变），黑键全用单键
#: 高音黑用数字行 2 3 5 6 7（与 FF14 原生一致），中音黑用数字行剩余，低音黑用空闲字母
_CHROMATIC36_ROWS: Dict[int, Tuple[str, str]] = {
    1: ("qwertyu", "23567"),
    0: ("asdfghj", "14890"),
    -1: ("zxcvbnm", "iopkl"),
}

#: 半音阶预设 B（FF14 风格）：一排 12 键按钢琴方式交错（白黑相间），
#: 用 无修饰 / Ctrl+ / Shift+ 三档覆盖三个八度
_FF14_BASE_KEYS = "zsxdcvgbhnjm"
_FF14_MOD_OF_OCTAVE: Dict[int, str] = {-1: "ctrl", 0: "", 1: "shift"}


def _build_diatonic_mapping() -> Dict[Tuple[int, int], str]:
    mapping: Dict[Tuple[int, int], str] = {}
    for octave, row in _DIATONIC_ROWS.items():
        for degree, char in enumerate(row, start=DEGREE_MIN):
            mapping[(octave, DEGREE_TO_SEMITONE[degree])] = char
    return mapping


def _build_chromatic36_mapping() -> Dict[Tuple[int, int], str]:
    mapping: Dict[Tuple[int, int], str] = {}
    for octave, (naturals, sharps) in _CHROMATIC36_ROWS.items():
        for degree, char in enumerate(naturals, start=DEGREE_MIN):
            mapping[(octave, DEGREE_TO_SEMITONE[degree])] = char
        for semitone, char in zip(SHARP_SEMITONES, sharps):
            mapping[(octave, semitone)] = char
    return mapping


def _build_ff14_mapping() -> Dict[Tuple[int, int], str]:
    mapping: Dict[Tuple[int, int], str] = {}
    for octave, modifier in _FF14_MOD_OF_OCTAVE.items():
        for semitone, char in enumerate(_FF14_BASE_KEYS):
            stroke = "%s+%s" % (modifier, char) if modifier else char
            mapping[(octave, semitone)] = stroke
    return mapping


_PRESET_BUILDERS = {
    PRESET_DIATONIC: _build_diatonic_mapping,
    PRESET_CHROMATIC: _build_chromatic36_mapping,
    PRESET_FF14: _build_ff14_mapping,
}
#: 「自定义」以内置的半音阶预设为底，再由用户覆盖项覆盖
_PRESET_BASE_OF = {PRESET_CUSTOM: PRESET_CHROMATIC}

#: 自然阶默认映射表 ``(octave, semitone) -> 按键``（21 项，兼容旧代码引用）
DEFAULT_KEYMAP: Dict[Tuple[int, int], str] = _build_diatonic_mapping()


def preset_names_for(mode: str) -> List[str]:
    """返回某个模式可选的预设名（顺序即界面下拉框顺序）。"""
    if mode == MODE_DIATONIC:
        return [PRESET_DIATONIC]
    return [PRESET_CHROMATIC, PRESET_FF14, PRESET_CUSTOM]


def default_preset(mode: str) -> str:
    return PRESET_DIATONIC if mode == MODE_DIATONIC else PRESET_CHROMATIC


def preset_mapping(preset: str, mode: str) -> Dict[Tuple[int, int], str]:
    """返回预设的原始映射表（未叠加用户覆盖项）。"""
    name = preset or default_preset(mode)
    if name not in _PRESET_BUILDERS:
        name = _PRESET_BASE_OF.get(name, default_preset(mode))
    return dict(_PRESET_BUILDERS[name]())


# --------------------------------------------------------------------------- #
# 音域工具
# --------------------------------------------------------------------------- #

def chromatic_keys() -> List[Key]:
    """全部 36 个半音，按界面从上到下（高音→低音）、半音 0→11 排列。"""
    return [Key(octave, semitone) for octave in (1, 0, -1) for semitone in range(SEMITONE_MIN, SEMITONE_MAX + 1)]


def diatonic_keys() -> List[Key]:
    """全部 21 个自然音，按界面顺序排列。"""
    return [Key.from_degree(octave, degree) for octave in (1, 0, -1) for degree in range(DEGREE_MIN, DEGREE_MAX + 1)]


def all_keys(mode: str = MODE_CHROMATIC) -> List[Key]:
    """按模式返回全部音（半音阶 36 个 / 自然阶 21 个）。"""
    return diatonic_keys() if mode == MODE_DIATONIC else chromatic_keys()


def is_in_range(octave: int, semitone: int, mode: str = MODE_CHROMATIC) -> bool:
    return Key(int(octave), int(semitone)).is_valid(mode)


def key_sort_index(key: Key) -> int:
    """界面排序权重：高音在前，同一音区内半音从小到大。"""
    return (OCTAVE_MAX - key.octave) * _KEYS_PER_OCTAVE + key.semitone


def format_key_label(key: Key, keymap: Optional["KeyMap"] = None) -> str:
    """返回 ``中音#1(1)`` 这样的完整标签，用于提示与错误信息。"""
    stroke = keymap.char_for(key) if keymap is not None else ""
    if not stroke:
        stroke = preset_mapping(default_preset(MODE_CHROMATIC), MODE_CHROMATIC).get(
            (key.octave, key.semitone), "?"
        )
    return "%s(%s)" % (key.label(), stroke)


# --------------------------------------------------------------------------- #
# KeyMap：模式 + 预设 + 用户覆盖
# --------------------------------------------------------------------------- #

class KeyMap:
    """``(音区, 半音) ↔ 按键`` 的双向映射，支持单键与组合键。

    - 每个模式一张表：自然阶（21 项）/ 半音阶（36 项）；
    - 预设提供初始键位（见 :data:`PRESET_NAMES`）；
    - 用户在设置页的改动以 ``overrides`` 形式保存（``"音区,半音" -> 按键``），
      只记录与预设不同的项，便于以后调整预设而不丢用户设置。
    """

    def __init__(
        self,
        mode: str = MODE_DIATONIC,
        preset: Optional[str] = None,
        overrides: Optional[Dict[object, object]] = None,
    ) -> None:
        # 兼容旧写法 KeyMap({"0,0": "a"})：第一个位置参数是覆盖项
        if isinstance(mode, dict):
            overrides = mode
            mode = MODE_DIATONIC
        self.mode: str = mode if mode in MODES else MODE_DIATONIC
        self.preset: str = preset or default_preset(self.mode)
        if self.preset not in preset_names_for(self.mode):
            self.preset = default_preset(self.mode)
        base = preset_mapping(self.preset, self.mode)
        self._strokes: Dict[Tuple[int, int], KeyStroke] = {
            slot: KeyStroke.parse(text) for slot, text in base.items()
        }
        self._errors: List[str] = []
        if overrides:
            self.apply_overrides(overrides, strict=False)

    # ------------------------------------------------------------------ #
    # 槽位与覆盖项
    # ------------------------------------------------------------------ #
    @staticmethod
    def parse_slot(raw: object) -> Tuple[int, int]:
        """把 ``"1,3"`` / ``"1-3"`` / ``(1, 3)`` 解析成 ``(octave, semitone)``。"""
        if isinstance(raw, (tuple, list)) and len(raw) == 2:
            return int(raw[0]), int(raw[1])
        text = str(raw).strip().replace("，", ",")
        for sep in (",", ":", "/", "-", "_"):
            if sep in text:
                left, _, right = text.partition(sep)
                try:
                    return int(left), int(right)
                except ValueError:
                    continue
        raise ValueError("音位写法无法识别：%r（应形如 \"1,3\" 表示高音半音3）" % (raw,))

    @staticmethod
    def slot_str(octave: int, semitone: int) -> str:
        return "%d,%d" % (int(octave), int(semitone))

    def apply_overrides(self, overrides: Dict[object, object], strict: bool = False) -> List[str]:
        """应用一批覆盖项，返回警告信息列表（``strict=True`` 时直接抛 ValueError）。"""
        warnings: List[str] = []
        for raw_slot, raw_key in dict(overrides).items():
            try:
                octave, semitone = self.parse_slot(raw_slot)
                stroke = KeyStroke.parse(raw_key)
            except ValueError as exc:
                if strict:
                    raise
                warnings.append(str(exc))
                continue
            key = Key(octave, semitone)
            if not key.is_valid(self.mode):
                message = "音位 %s 在%s模式下不合法" % (key.label(), MODE_NAMES.get(self.mode, self.mode))
                if strict:
                    raise ValueError(message)
                warnings.append(message)
                continue
            self._strokes[(octave, semitone)] = stroke
        self._errors = warnings
        return warnings

    def reset(self) -> None:
        """恢复为当前预设的默认键位。"""
        self._strokes = {slot: KeyStroke.parse(text) for slot, text in preset_mapping(self.preset, self.mode).items()}
        self._errors = []

    def switch_mode(self, mode: str, preset: Optional[str] = None) -> None:
        """切换模式（清空覆盖项，回到该模式的默认预设）。"""
        self.__init__(mode, preset)

    # ------------------------------------------------------------------ #
    # 查询
    # ------------------------------------------------------------------ #
    def stroke_for(self, key: Key) -> Optional[KeyStroke]:
        """取音对应的按键动作；未映射返回 ``None``。"""
        return self._strokes.get((key.octave, key.semitone))

    def char_for(self, key: Key) -> str:
        """取音对应的按键文本（组合键返回如 ``shift+z``）；未映射返回空串。"""
        stroke = self.stroke_for(key)
        return stroke.text() if stroke is not None else ""

    def key_for(self, octave: int, semitone: int) -> str:
        stroke = self._strokes.get((int(octave), int(semitone)))
        return stroke.text() if stroke is not None else ""

    def locate(self, char: str) -> Optional[Key]:
        """单键 → 音（用于键盘直接输入）；组合键请用 :meth:`locate_stroke`。"""
        text = (char or "").strip().lower()
        if not text or "+" in text:
            return None
        for (octave, semitone), stroke in self._strokes.items():
            if not stroke.is_combo and stroke.main == text:
                return Key(octave, semitone)
        return None

    def locate_stroke(self, stroke: KeyStroke) -> Optional[Key]:
        """按键动作 → 音。"""
        target = stroke.text()
        for (octave, semitone), item in self._strokes.items():
            if item.text() == target:
                return Key(octave, semitone)
        return None

    def keys(self) -> List[Key]:
        """当前模式下的全部音（界面顺序）。"""
        return all_keys(self.mode)

    def items(self) -> List[Tuple[Key, Optional[KeyStroke]]]:
        return [(key, self.stroke_for(key)) for key in self.keys()]

    def chars(self) -> List[str]:
        return [self.char_for(key) for key in self.keys()]

    def row_strokes(self, octave: int) -> List[Optional[KeyStroke]]:
        """返回某一音区的 12 个按键动作（自然阶模式下只有 7 个有值）。"""
        return [self.stroke_for(Key(octave, semitone)) for semitone in range(SEMITONE_MIN, SEMITONE_MAX + 1)]

    def row_chars(self, octave: int) -> str:
        """返回某一音区的主键字符。

        自然阶 7 个（``qwertyu``）、半音阶 12 个（``q2w3er5t6y7u``），
        取按键动作里的**主键**（组合键忽略修饰键），未映射用 ``.`` 占位。
        """
        semitones = NATURAL_SEMITONES if self.mode == MODE_DIATONIC else tuple(range(SEMITONE_MIN, SEMITONE_MAX + 1))
        chars = []
        for semitone in semitones:
            stroke = self.stroke_for(Key(int(octave), semitone))
            chars.append(stroke.main if stroke is not None else ".")
        return "".join(chars)

    def describe(self) -> str:
        return "%s %d 键 · %s" % (
            MODE_NAMES.get(self.mode, self.mode),
            len(self.keys()),
            PRESET_NAMES.get(self.preset, self.preset),
        )

    # ------------------------------------------------------------------ #
    # 序列化
    # ------------------------------------------------------------------ #
    def to_json(self) -> Dict[str, str]:
        """导出全部映射（完整表，便于人工查看）。"""
        return {
            self.slot_str(octave, semitone): stroke.text()
            for (octave, semitone), stroke in sorted(self._strokes.items(), reverse=True)
        }

    def overrides(self) -> Dict[str, str]:
        """只导出与预设不同的项，用于写入 settings.json。"""
        base = preset_mapping(self.preset, self.mode)
        result: Dict[str, str] = {}
        for (octave, semitone), stroke in sorted(self._strokes.items(), reverse=True):
            original = base.get((octave, semitone))
            if original is None or KeyStroke.parse(original).text() != stroke.text():
                result[self.slot_str(octave, semitone)] = stroke.text()
        return result

    @classmethod
    def from_json(cls, data: Optional[Dict[object, object]], mode: str = MODE_DIATONIC, preset: Optional[str] = None) -> "KeyMap":
        if not data:
            return cls(mode, preset)
        return cls(mode, preset, overrides=data)

    def copy(self) -> "KeyMap":
        clone = KeyMap(self.mode, self.preset)
        clone._strokes = dict(self._strokes)
        clone._errors = list(self._errors)
        return clone

    # ------------------------------------------------------------------ #
    # 校验
    # ------------------------------------------------------------------ #
    def validate(self) -> List[str]:
        """检查映射表是否可用：缺键、重复占用、按键非法。"""
        problems: List[str] = []
        for key in self.keys():
            if (key.octave, key.semitone) not in self._strokes:
                problems.append("音位 %s 没有绑定按键" % key.label())
        seen: Dict[str, Key] = {}
        for key in self.keys():
            stroke = self.stroke_for(key)
            if stroke is None:
                continue
            text = stroke.text()
            if text in seen:
                problems.append(
                    "按键 %s 被 %s 与 %s 同时占用" % (text, seen[text].label(), key.label())
                )
            else:
                seen[text] = key
        return problems

    def __iter__(self) -> Iterator[Tuple[Key, Optional[KeyStroke]]]:
        return iter(self.items())

    def __repr__(self) -> str:  # pragma: no cover - 调试用
        return "KeyMap(mode=%r, preset=%r, overrides=%r)" % (self.mode, self.preset, self.overrides())
