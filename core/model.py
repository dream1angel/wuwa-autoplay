# -*- coding: utf-8 -*-
"""曲谱数据模型与合法性校验（纯数据，无 GUI 依赖）。

JSON 结构（v1.1，兼容读取 v1.0）::

    {
      "format": "wuthering-score",
      "version": "1.1",
      "meta": {
        "title": "曲名", "author": "",
        "bpm": 120, "timeSignature": [4, 4], "subdivision": 4,
        "mode": "diatonic",            // diatonic（21 键自然阶，默认）| chromatic（36 键半音阶）
        "chromaticPreset": "chromatic-36",
        "referenceMidi": 60
      },
      "notes": [
        {"beat": 0, "duration": 1, "keys": [{"octave": 0, "degree": 1}]},
        {"beat": 1, "duration": 1, "keys": [{"octave": 0, "semitone": 1}]},
        {"beat": 2, "duration": 1, "keys": [{"midi": 66}]}
      ]
    }

约定
----
* ``beat``：以「最小细分单位（格）」为 1 的绝对位置，允许小数；
* ``duration``：持续格数，>= 1；
* ``subdivision``：**每拍格数**（1/4 拍 → 4，1/8 → 8，1/16 → 16，1/32 → 32）；
* 每格毫秒数 = ``60000 / (bpm * subdivision)``，变速播放时再除以 speed；
* 休止符 = 该位置没有任何 ``notes`` 项（不需要显式表示）；
* 音符键三种写法优先级：``midi`` > ``semitone`` > ``degree``，解析后统一成
  :class:`core.notes.Key` 的规范形式 ``Key(octave, semitone)``。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from .notes import (
    DEGREE_MAX,
    DEGREE_MIN,
    MODE_CHROMATIC,
    MODE_DIATONIC,
    MODE_NAMES,
    MODES,
    NATURAL_SEMITONES,
    OCTAVE_MAX,
    OCTAVE_MIN,
    REFERENCE_MIDI,
    SEMITONE_MAX,
    SEMITONE_MIN,
    Key,
    KeyMap,
    format_key_label,
    key_sort_index,
    preset_names_for,
)

__all__ = [
    "SCORE_FORMAT",
    "SCORE_VERSION",
    "SUPPORTED_VERSIONS",
    "ALLOWED_SUBDIVISIONS",
    "ALLOWED_TIME_SIGNATURES",
    "BPM_MIN",
    "BPM_MAX",
    "SPEED_MIN",
    "SPEED_MAX",
    "EPS",
    "Issue",
    "ScoreError",
    "ScoreMeta",
    "ScoreNote",
    "Score",
]

SCORE_FORMAT = "wuthering-score"
SCORE_VERSION = "1.1"
#: 能正常读取的版本号；更高版本会给出警告但尝试解析
SUPPORTED_VERSIONS: Tuple[str, ...] = ("1.0", "1.1")

ALLOWED_SUBDIVISIONS: Tuple[int, ...] = (4, 8, 16, 32)
ALLOWED_TIME_SIGNATURES: Tuple[Tuple[int, int], ...] = (
    (4, 4),
    (3, 4),
    (2, 4),
    (6, 8),
    (3, 8),
    (2, 2),
)

BPM_MIN, BPM_MAX = 40, 300
SPEED_MIN, SPEED_MAX = 0.5, 2.0

#: 浮点比较容差（beat/duration 都是格数，1e-6 足够）
EPS = 1e-6


class ScoreError(Exception):
    """曲谱文件损坏 / 内容非法时抛出，消息面向最终用户（中文）。"""


@dataclass
class Issue:
    """一条校验结果。level: ``"error"`` 阻止演奏，``"warning"`` 仅提示。"""

    level: str
    message: str
    beat: Optional[float] = None

    def __str__(self) -> str:  # pragma: no cover - 展示用
        where = "" if self.beat is None else "（第 %.2f 格）" % self.beat
        return "%s%s" % (self.message, where)


def has_errors(issues: Sequence[Issue]) -> bool:
    return any(item.level == "error" for item in issues)


def _isclose(a: float, b: float) -> bool:
    return abs(float(a) - float(b)) <= EPS


# --------------------------------------------------------------------------- #
# 元信息
# --------------------------------------------------------------------------- #

@dataclass
class ScoreMeta:
    """曲谱元信息（BPM / 拍号 / 细分 / 标题 / 音阶模式）。"""

    title: str = "未命名曲谱"
    author: str = ""
    bpm: int = 120
    time_signature: Tuple[int, int] = (4, 4)
    subdivision: int = 4
    #: 音阶模式：``diatonic``（21 键自然阶，默认）或 ``chromatic``（36 键半音阶）
    mode: str = MODE_DIATONIC
    #: 导出时使用的半音阶预设名（仅供提示，实际演奏以本机映射为准）
    chromatic_preset: str = "chromatic-36"
    #: 中音 do 对应的 MIDI 号
    reference_midi: int = REFERENCE_MIDI

    # ---- 派生属性 ------------------------------------------------------ #
    @property
    def is_chromatic(self) -> bool:
        return self.mode == MODE_CHROMATIC

    @property
    def mode_name(self) -> str:
        return MODE_NAMES.get(self.mode, self.mode)

    @property
    def key_count(self) -> int:
        return 36 if self.is_chromatic else 21

    @property
    def slots_per_beat(self) -> int:
        """每拍格数 = subdivision。"""
        return int(self.subdivision)

    @property
    def slots_per_bar(self) -> int:
        """每小节格数 = 分子 × 每拍格数（拍号分母即一拍）。"""
        return int(self.time_signature[0]) * self.slots_per_beat

    def ms_per_slot(self, speed: float = 1.0) -> float:
        """一格的毫秒数；speed 为变速倍率（2.0 = 两倍速）。"""
        if speed <= 0:
            raise ValueError("变速倍率必须大于 0")
        return 60000.0 / (float(self.bpm) * float(self.subdivision)) / float(speed)

    @property
    def time_signature_text(self) -> str:
        return "%d/%d" % (int(self.time_signature[0]), int(self.time_signature[1]))

    # ---- 序列化 -------------------------------------------------------- #
    def to_json(self) -> Dict[str, object]:
        payload: Dict[str, object] = {
            "title": self.title,
            "author": self.author,
            "bpm": int(self.bpm),
            "timeSignature": [int(self.time_signature[0]), int(self.time_signature[1])],
            "subdivision": int(self.subdivision),
            "mode": self.mode,
            "referenceMidi": int(self.reference_midi),
        }
        if self.is_chromatic:
            payload["chromaticPreset"] = self.chromatic_preset
        return payload

    @staticmethod
    def from_json(data: object) -> "ScoreMeta":
        if data is None:
            data = {}
        if not isinstance(data, dict):
            raise ScoreError("meta 字段必须是对象")
        meta = ScoreMeta()
        if "title" in data and data["title"] is not None:
            meta.title = str(data["title"])
        if "author" in data and data["author"] is not None:
            meta.author = str(data["author"])
        if "bpm" in data and data["bpm"] is not None:
            try:
                meta.bpm = int(float(data["bpm"]))
            except (TypeError, ValueError) as exc:
                raise ScoreError("bpm 必须是数字：%r" % (data["bpm"],)) from exc
        if "timeSignature" in data and data["timeSignature"] is not None:
            ts = data["timeSignature"]
            if not isinstance(ts, (list, tuple)) or len(ts) != 2:
                raise ScoreError("timeSignature 必须是 [分子, 分母] 形式")
            try:
                meta.time_signature = (int(ts[0]), int(ts[1]))
            except (TypeError, ValueError) as exc:
                raise ScoreError("timeSignature 必须是整数对：%r" % (ts,)) from exc
        if "subdivision" in data and data["subdivision"] is not None:
            try:
                meta.subdivision = int(data["subdivision"])
            except (TypeError, ValueError) as exc:
                raise ScoreError("subdivision 必须是整数：%r" % (data["subdivision"],)) from exc
        if "mode" in data and data["mode"] is not None:
            mode = str(data["mode"]).strip().lower()
            if mode not in MODES:
                raise ScoreError("mode 只能是 %s，实际为 %r" % (" 或 ".join(MODES), data["mode"]))
            meta.mode = mode
        if "chromaticPreset" in data and data["chromaticPreset"] is not None:
            meta.chromatic_preset = str(data["chromaticPreset"])
        if "referenceMidi" in data and data["referenceMidi"] is not None:
            try:
                meta.reference_midi = int(data["referenceMidi"])
            except (TypeError, ValueError) as exc:
                raise ScoreError("referenceMidi 必须是整数：%r" % (data["referenceMidi"],)) from exc
        return meta

    def clone(self) -> "ScoreMeta":
        return ScoreMeta(
            title=self.title,
            author=self.author,
            bpm=self.bpm,
            time_signature=tuple(self.time_signature),  # type: ignore[arg-type]
            subdivision=self.subdivision,
            mode=self.mode,
            chromatic_preset=self.chromatic_preset,
            reference_midi=self.reference_midi,
        )


# --------------------------------------------------------------------------- #
# 单个音符
# --------------------------------------------------------------------------- #

@dataclass
class ScoreNote:
    """一个音符：起始格 + 持续格数 + 同时按下的若干键（和弦）。"""

    beat: float
    duration: float
    keys: List[Key] = field(default_factory=list)

    # ---- 派生属性 ------------------------------------------------------ #
    @property
    def end(self) -> float:
        return float(self.beat) + float(self.duration)

    @property
    def is_chord(self) -> bool:
        return len(self.keys) > 1

    def sorted_keys(self) -> List[Key]:
        """按界面从上到下排序，保证和弦按键的激发顺序稳定。"""
        return sorted(self.keys, key=key_sort_index)

    def labels(self, keymap: Optional[KeyMap] = None) -> List[str]:
        return [format_key_label(key, keymap) for key in self.sorted_keys()]

    def char_string(self, keymap: KeyMap) -> str:
        """如 ``a+f``，用于文本谱导出与格子内显示。"""
        return "+".join(keymap.char_for(key) for key in self.sorted_keys())

    # ---- 变换 ---------------------------------------------------------- #
    def clone(self) -> "ScoreNote":
        return ScoreNote(float(self.beat), float(self.duration), list(self.keys))

    def shifted(self, degree_steps: int = 0, octave_steps: int = 0) -> "ScoreNote":
        return ScoreNote(
            float(self.beat),
            float(self.duration),
            [key.shifted(degree_steps, octave_steps) for key in self.keys],
        )

    def shifted_semitones(self, steps: int) -> "ScoreNote":
        return ScoreNote(
            float(self.beat),
            float(self.duration),
            [key.shifted_semitones(steps) for key in self.keys],
        )

    # ---- 序列化 -------------------------------------------------------- #
    def to_json(self, mode: str = MODE_DIATONIC, downgrade: bool = True) -> Dict[str, object]:
        return {
            "beat": round(float(self.beat), 6),
            "duration": round(float(self.duration), 6),
            "keys": [key.to_json(mode, downgrade) for key in self.sorted_keys()],
        }

    @staticmethod
    def from_json(data: object) -> "ScoreNote":
        if not isinstance(data, dict):
            raise ScoreError("notes 的每一项都必须是对象")
        try:
            beat = float(data["beat"])
            duration = float(data["duration"])
        except KeyError as exc:
            raise ScoreError("音符缺少字段 %s" % exc) from exc
        except (TypeError, ValueError) as exc:
            raise ScoreError("beat/duration 必须是数字：%r" % (data,)) from exc
        raw_keys = data.get("keys")
        if not isinstance(raw_keys, list) or not raw_keys:
            raise ScoreError("音符 %r 的 keys 必须是非空数组" % (round(beat, 3),))
        keys: List[Key] = []
        for item in raw_keys:
            try:
                keys.append(Key.from_json(item))
            except ValueError as exc:
                raise ScoreError("音符 %r 解析失败：%s" % (round(beat, 3), exc)) from exc
        return ScoreNote(beat, duration, keys)

    # ---- 比较（撤销栈依赖 dataclass 的 __eq__） ------------------------ #
    def same_content(self, other: "ScoreNote") -> bool:
        return (
            _isclose(self.beat, other.beat)
            and _isclose(self.duration, other.duration)
            and sorted(self.keys) == sorted(other.keys)
        )


# --------------------------------------------------------------------------- #
# 整首曲谱
# --------------------------------------------------------------------------- #

@dataclass
class Score:
    """一首曲谱：元信息 + 音符列表（按 beat 升序维护）。"""

    meta: ScoreMeta = field(default_factory=ScoreMeta)
    notes: List[ScoreNote] = field(default_factory=list)
    #: 关联文件路径（打开/另存为后回填，未保存为 None）
    path: Optional[str] = None
    #: 读取时收集的非致命提示（界面状态栏展示）
    warnings: List[str] = field(default_factory=list)

    # ---- 构造 ---------------------------------------------------------- #
    @staticmethod
    def empty(
        title: str = "未命名曲谱",
        bpm: int = 120,
        time_signature: Tuple[int, int] = (4, 4),
        subdivision: int = 4,
        mode: str = MODE_DIATONIC,
    ) -> "Score":
        return Score(
            ScoreMeta(
                title=title,
                bpm=bpm,
                time_signature=time_signature,
                subdivision=subdivision,
                mode=mode,
            )
        )

    # ---- 基础操作 ------------------------------------------------------ #
    def sort(self) -> None:
        self.notes.sort(key=lambda n: (round(n.beat, 6), key_sort_index(n.keys[0]) if n.keys else 0))

    def clone(self) -> "Score":
        return Score(
            self.meta.clone(),
            [note.clone() for note in self.notes],
            self.path,
            list(self.warnings),
        )

    def total_slots(self) -> float:
        """最后一个音的结束位置（格）。空谱返回 0。"""
        if not self.notes:
            return 0.0
        return max(note.end for note in self.notes)

    def total_ms(self, speed: float = 1.0) -> float:
        return self.total_slots() * self.meta.ms_per_slot(speed)

    # ---- 按位置查找 ---------------------------------------------------- #
    def note_at(self, beat: float) -> Optional[ScoreNote]:
        for note in self.notes:
            if _isclose(note.beat, beat):
                return note
        return None

    def remove_at(self, beat: float) -> bool:
        before = len(self.notes)
        self.notes = [n for n in self.notes if not _isclose(n.beat, beat)]
        return len(self.notes) != before

    def upsert(self, note: ScoreNote) -> None:
        existing = self.note_at(note.beat)
        if existing is not None:
            self.notes[self.notes.index(existing)] = note
        else:
            self.notes.append(note)
        self.sort()

    def notes_in_range(self, start: float, end: float) -> List[ScoreNote]:
        """返回起始位置落在 ``[start, end)`` 的音符。"""
        return [n for n in self.notes if start - EPS <= n.beat < end - EPS]

    # ---- 整体变调 ------------------------------------------------------ #
    def shift_all(self, degree_steps: int = 0, octave_steps: int = 0) -> None:
        for note in self.notes:
            note.keys = [key.shifted(degree_steps, octave_steps) for key in note.keys]

    def shift_all_semitones(self, steps: int) -> None:
        """半音阶模式的移调（±1 = 半音，±12 = 八度）。"""
        for note in self.notes:
            note.keys = [key.shifted_semitones(steps) for key in note.keys]

    def has_sharps(self) -> bool:
        """是否存在黑键（半音）音符。"""
        return any(key.is_sharp for note in self.notes for key in note.keys)

    def sharp_keys(self) -> List[Key]:
        """返回谱中用到的全部黑键音（去重，按界面顺序）。"""
        found: List[Key] = []
        for note in self.notes:
            for key in note.keys:
                if key.is_sharp and key not in found:
                    found.append(key)
        return sorted(found, key=key_sort_index)

    # ---- 校验 ---------------------------------------------------------- #
    def validate(self, keymap: Optional[KeyMap] = None) -> List[Issue]:
        """返回全部问题；有 ``error`` 时禁止演奏。"""
        issues: List[Issue] = []

        meta = self.meta
        if not meta.title.strip():
            issues.append(Issue("warning", "曲谱标题为空，建议填写以便区分文件"))
        if not (BPM_MIN <= meta.bpm <= BPM_MAX):
            issues.append(Issue("error", "BPM %d 超出允许范围 %d~%d" % (meta.bpm, BPM_MIN, BPM_MAX)))
        if meta.subdivision not in ALLOWED_SUBDIVISIONS:
            issues.append(
                Issue(
                    "error",
                    "细分单位 %d 非法，只支持每拍 %s 格"
                    % (meta.subdivision, "/".join(str(v) for v in ALLOWED_SUBDIVISIONS)),
                )
            )
        num, den = meta.time_signature
        if not (1 <= num <= 16) or den not in (2, 4, 8, 16):
            issues.append(
                Issue("error", "拍号 %s 不合理，请使用 4/4、3/4、6/8、2/4 等常见拍号" % meta.time_signature_text)
            )
        elif tuple(meta.time_signature) not in ALLOWED_TIME_SIGNATURES:
            issues.append(Issue("warning", "拍号 %s 非预设值，将按 分子×每拍 的规则排布小节" % meta.time_signature_text))

        if meta.mode not in MODES:
            issues.append(Issue("error", "音阶模式 %r 非法（只能是 %s）" % (meta.mode, " / ".join(MODES))))
        if not (0 <= meta.reference_midi <= 127):
            issues.append(Issue("warning", "referenceMidi %s 超出 MIDI 范围 0~127，将按 60 处理" % meta.reference_midi))
        if meta.is_chromatic and meta.chromatic_preset not in preset_names_for(MODE_CHROMATIC):
            issues.append(Issue("warning", "半音阶预设 %r 未知，将按默认预设处理" % meta.chromatic_preset))

        if not self.notes:
            issues.append(Issue("warning", "曲谱还没有任何音符"))
            return issues

        started: Dict[float, ScoreNote] = {}
        for note in self.notes:
            if note.beat < -EPS:
                issues.append(Issue("error", "存在起始位置为负数的音符（%.3f 格）" % note.beat))
            if note.duration <= EPS:
                issues.append(Issue("error", "位于 %.3f 格的音符时值为 %s 格，必须大于 0" % (note.beat, note.duration)))
            if not note.keys:
                issues.append(Issue("error", "位于 %.3f 格的音符没有指定任何按键" % note.beat))
            seen_keys = set()
            for key in note.keys:
                if not key.is_valid(MODE_CHROMATIC):
                    issues.append(
                        Issue(
                            "error",
                            "位于 %.3f 格的音符 %s 超出游戏音域（音区 -1~1，半音 0~11）" % (note.beat, key.label()),
                            note.beat,
                        )
                    )
                elif meta.mode == MODE_DIATONIC and key.is_sharp:
                    issues.append(
                        Issue(
                            "error",
                            "位于 %.3f 格的音符 %s 是半音（黑键），当前是自然阶模式："
                            "按 F7 开启半音阶模式，或用「工具 → 黑键处理」把它就近归入自然音"
                            % (note.beat, key.label()),
                            note.beat,
                        )
                    )
                if key in seen_keys:
                    issues.append(
                        Issue("warning", "位于 %.3f 格的音符重复出现了 %s" % (note.beat, key.label()), note.beat)
                    )
                seen_keys.add(key)
            # 同一起始位置重复
            rounded = round(note.beat, 6)
            if rounded in started:
                issues.append(Issue("error", "同一位置 %.3f 格出现了多个音符，请合并为和弦" % note.beat, note.beat))
            started[rounded] = note

        # 重叠检测：前一个音的结束位置不得越过后一个音的起点
        ordered = sorted(self.notes, key=lambda n: n.beat)
        for prev, nxt in zip(ordered, ordered[1:]):
            if prev.end > nxt.beat + EPS:
                issues.append(
                    Issue(
                        "warning",
                        "位于 %.2f 格的音符（时值 %.2f）与 %.2f 格的音符重叠，重叠部分将按后一个音处理"
                        % (prev.beat, prev.duration, nxt.beat),
                        prev.beat,
                    )
                )

        if keymap is not None:
            for problem in keymap.validate():
                issues.append(Issue("error", "按键映射有误：%s" % problem))
        return issues

    # ---- 序列化（文件头由 scoreio.json_io 负责） ----------------------- #
    def to_json(self, downgrade: bool = True) -> Dict[str, object]:
        """导出为 JSON 结构。

        ``downgrade=True`` 时，半音阶曲谱里的黑键会额外附一个近似 ``degree``，
        方便旧版程序降级读取（读到一个近似的自然音，而不是直接报错）。
        """
        self.sort()
        return {
            "format": SCORE_FORMAT,
            "version": SCORE_VERSION,
            "meta": self.meta.to_json(),
            "notes": [note.to_json(self.meta.mode, downgrade) for note in self.notes],
        }

    @staticmethod
    def from_json(data: object) -> "Score":
        if not isinstance(data, dict):
            raise ScoreError("曲谱根节点必须是对象")
        fmt = data.get("format", SCORE_FORMAT)
        if fmt != SCORE_FORMAT:
            raise ScoreError("不是本程序的曲谱文件（format=%r，应为 %r）" % (fmt, SCORE_FORMAT))
        version = str(data.get("version", SCORE_VERSION))
        warnings: List[str] = []
        if version not in SUPPORTED_VERSIONS:
            warnings.append("曲谱版本 %s 不是当前支持版本 %s，已尝试兼容读取" % (version, SCORE_VERSION))
        if "notes" not in data:
            raise ScoreError("曲谱缺少 notes 字段")
        raw_notes = data["notes"]
        if not isinstance(raw_notes, list):
            raise ScoreError("notes 必须是数组")
        score = Score(ScoreMeta.from_json(data.get("meta")))
        for item in raw_notes:
            score.notes.append(ScoreNote.from_json(item))
        score.warnings = warnings
        score.sort()
        return score
