# -*- coding: utf-8 -*-
"""纯文本谱读写（社区分享用）。

文本格式说明
------------
每个记号（用空格分开）代表**一格**（最小细分单位）：

=========================  ==========================================
记号                        含义
=========================  ==========================================
``Q W E R T Y U``          高音 1~7（大小写均可，字母谱按当前按键映射解析）
``A S D F G H J``          中音 1~7
``Z X C V B N M``          低音 1~7
``-``                      延音：把前一个音的时值 +1 格
``0`` / ``.`` / ``_``      休止符（占一格，不发声）
``|``                      小节线，仅用于排版，导入时忽略
``A+F``（或 ``[AF]``）     和弦：同一格同时按下多个音
``1``~``7``                中音简谱（**优先按音高解释**，与键位无关）
``#1`` ``#2`` ``#4`` ...   升半音（黑键），例如 ``#1`` = 中音 #do
``+#1`` / ``^#4``          高音区的升半音
``-#1``                    低音区的升半音
``C#4`` / ``Db5`` / ``A4`` 音名写法（4 = 中音，3 = 低音，5 = 高音）
``高3`` / ``中3`` / ``低3``  中文音区写法
=========================  ==========================================

文件头（可选，写在最前面）：::

    # wuthering-score text 1.0
    # title: 小星星
    # author: 佚名
    # bpm: 120
    # time: 4/4
    # sub: 4
    # mode: diatonic        （或 chromatic，缺省 diatonic）
"""

from __future__ import annotations

import math
import os
import re
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from core.model import ALLOWED_SUBDIVISIONS, Score, ScoreMeta, ScoreNote
from core.notes import (
    MODE_CHROMATIC,
    MODE_DIATONIC,
    MODES,
    OCTAVE_NAMES,
    SEMITONE_JIANPU,
    Key,
    KeyMap,
    default_preset,
)

from .json_io import ScoreIOError

__all__ = ["dumps_text", "loads_text", "load_text", "save_text", "TEXT_HEADER"]

TEXT_HEADER = "# wuthering-score text 1.0"

_REST_TOKENS = {"0", ".", "_", "休", "休止", "r", "rest"}
_TIE_TOKENS = {"-", "－", "‾", "~"}

#: 音名 → 半音（不含升降号）
_NOTE_LETTERS = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}
_NOTE_NAME_RE = re.compile(r"^([A-Ga-g])([#bｂ♯♭]?)([0-9])?$")

#: 音区前缀
_OCTAVE_PREFIXES = {
    "+": 1, "^": 1, "高": 1,
    "中": 0,
    "-": -1, "－": -1, "低": -1,
}
_SHARP_CHARS = "#＃♯升"


# --------------------------------------------------------------------------- #
# 导出
# --------------------------------------------------------------------------- #

def _token_for_key(key: Key, mode: str, keymap: KeyMap) -> str:
    """把一个音写成文本记号。"""
    if mode == MODE_CHROMATIC:
        prefix = "+" if key.octave > 0 else ("-" if key.octave < 0 else "")
        return prefix + SEMITONE_JIANPU[key.semitone]
    char = keymap.char_for(key)
    return char if char else "?"


def dumps_text(score: Score, keymap: Optional[KeyMap] = None, bars_per_line: int = 4) -> str:
    """曲谱 → 文本谱。

    * 自然阶模式导出字母谱（``Q W E``），半音阶模式导出简谱（``1 #1 +2``）；
    * 非整数格的音符会四舍五入到最近格，并在文件头写一行注释提示；
    * 没有按键的音会写成 ``?``，导入时同样报错提示。
    """
    keymap = keymap or KeyMap(score.meta.mode, default_preset(score.meta.mode))
    meta = score.meta
    mode = meta.mode
    slots_per_bar = max(1, meta.slots_per_bar)
    total = int(round(score.total_slots()))
    bars = max(1, math.ceil(total / slots_per_bar))
    total_slots = bars * slots_per_bar

    start_map: Dict[int, ScoreNote] = {}
    coverage: Dict[int, int] = {}
    snapped = 0
    for note in score.notes:
        slot = int(round(float(note.beat)))
        if abs(float(note.beat) - slot) > 1e-6:
            snapped += 1
        start_map[slot] = note
        span = max(1, int(round(float(note.duration))))
        for offset in range(span):
            coverage[slot + offset] = slot

    lines: List[str] = [
        TEXT_HEADER,
        "# title: %s" % meta.title,
        "# author: %s" % meta.author,
        "# bpm: %d" % meta.bpm,
        "# time: %s" % meta.time_signature_text,
        "# sub: %d" % meta.subdivision,
        "# mode: %s" % mode,
    ]
    if snapped:
        lines.append("# 提示：%d 个音符的位置不是整数格，导出时已四舍五入" % snapped)
    lines.append("#")

    cells: List[str] = []
    for slot in range(total_slots):
        note = start_map.get(slot)
        if note is not None:
            chars: List[str] = []
            for key in note.sorted_keys():
                chars.append(_token_for_key(key, mode, keymap))
            cells.append("+".join(chars))
        elif slot in coverage:
            cells.append("-")
        else:
            cells.append("0")

    for bar in range(bars):
        chunk = cells[bar * slots_per_bar : (bar + 1) * slots_per_bar]
        segment = " ".join(chunk)
        lines.append("%s |" % segment)
        if bar % max(1, bars_per_line) == max(1, bars_per_line) - 1 or bar == bars - 1:
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def save_text(score: Score, path: str, keymap: Optional[KeyMap] = None, bars_per_line: int = 4) -> str:
    target = os.path.abspath(path)
    if not target.lower().endswith(".txt"):
        target += ".txt"
    try:
        with open(target, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(dumps_text(score, keymap, bars_per_line))
    except OSError as exc:
        raise ScoreIOError("保存文本谱失败：%s（%s）" % (target, exc)) from exc
    return target


# --------------------------------------------------------------------------- #
# 导入
# --------------------------------------------------------------------------- #

def _checked_key(octave: int, degree: int, token: str) -> Key:
    if not (1 <= degree <= 7):
        raise ScoreIOError("记号 %r 的音级超出 1~7" % token)
    if octave not in (-1, 0, 1):
        raise ScoreIOError("记号 %r 的音区超出范围" % token)
    return Key.from_degree(octave, degree)


def _parse_note_name(text: str) -> Optional[Key]:
    """解析 ``C#4`` / ``Db5`` / ``A4`` / ``F#`` 这类音名写法。"""
    match = _NOTE_NAME_RE.match(text)
    if not match:
        return None
    letter, accidental, octave_digit = match.groups()
    semitone = _NOTE_LETTERS[letter.upper()]
    if accidental in ("#", "♯"):
        semitone += 1
    elif accidental in ("b", "ｂ", "♭"):
        semitone -= 1
    octave = int(octave_digit) - 4 if octave_digit else 0
    return Key.from_pitch(octave * 12 + semitone)


def _parse_jianpu(text: str) -> Optional[Key]:
    """解析简谱：``1``~``7``、``#1``、``+#1``、``-#4``、``高3``。"""
    octave = 0
    sharp = False
    body = text
    while body:
        head = body[0]
        if head in _OCTAVE_PREFIXES:
            octave = _OCTAVE_PREFIXES[head]
            body = body[1:]
            continue
        if head in _SHARP_CHARS:
            sharp = True
            body = body[1:]
            continue
        break
    if not body.isdigit():
        return None
    degree = int(body)
    if not (1 <= degree <= 7):
        return None
    key = Key.from_degree(octave, degree)
    if sharp:
        # E# → F、B# → 下一个八度的 do，正好都落在 36 键范围内
        key = key.shifted_semitones(1)
    return key


def _parse_key_token(token: str, keymap: KeyMap) -> Key:
    """把一个单音记号解析成 :class:`~core.notes.Key`。

    解释优先级：**单字母按键** → **音名** → **简谱** → 兜底按键查找。
    数字优先按音高解释（``1`` 永远是中音 do），这样文本谱可以跨键位分享。
    """
    text = token.strip()
    if not text:
        raise ScoreIOError("空记号无法解析")

    if len(text) == 1 and text.isalpha():
        key = keymap.locate(text)
        if key is None:
            raise ScoreIOError("按键 %r 不在当前按键映射表中" % token)
        return key

    named = _parse_note_name(text)
    if named is not None:
        return named

    jianpu = _parse_jianpu(text)
    if jianpu is not None:
        return jianpu

    if text.isdigit():
        # 纯数字但不在 1~7：明显是想写简谱，给出更准确的提示
        raise ScoreIOError("记号 %r 的简谱音级超出 1~7" % token)

    key = keymap.locate(text)
    if key is not None:
        return key
    raise ScoreIOError("无法识别的记号 %r（可用：字母键位、1~7 简谱、#1 升半音、C#4 音名）" % token)


def _split_chord(text: str) -> List[str]:
    """按 ``+`` 拆分和弦，但保留 ``+4`` / ``^3`` 这类音区前缀记号。"""
    parts: List[str] = []
    current = ""
    for char in text:
        if char == "+":
            if current.strip():
                parts.append(current)
                current = ""
            else:
                current += char
        else:
            current += char
    if current.strip():
        parts.append(current)
    return parts


def _parse_cell(token: str, keymap: KeyMap) -> List[Key]:
    """解析一个格子的记号，返回其中的音（空列表表示休止）。"""
    text = token.strip()
    if not text:
        return []
    if text in _REST_TOKENS:
        return []
    if len(text) > 1 and text[0] == "[" and text[-1] == "]":
        text = text[1:-1]
    elif len(text) > 1 and text[0] == "(" and text[-1] == ")":
        text = text[1:-1]
    parts = [p for p in _split_chord(text.replace("＋", "+")) if p]
    if not parts:
        return []
    keys: List[Key] = []
    for part in parts:
        # 支持紧凑写法 [AF] / AF：每个字母都是一个独立按键
        if len(part) > 1 and part.isalpha():
            located = [keymap.locate(ch) for ch in part]
            if all(item is not None for item in located):
                for item in located:
                    if item is not None and item not in keys:
                        keys.append(item)
                continue
        key = _parse_key_token(part, keymap)
        if key not in keys:
            keys.append(key)
    return keys


def loads_text(
    text: str,
    keymap: Optional[KeyMap] = None,
    defaults: Optional[ScoreMeta] = None,
) -> Score:
    """文本谱 → 曲谱。"""
    keymap = keymap or KeyMap()
    meta = (defaults.clone() if defaults else ScoreMeta())
    meta.title = "导入的文本谱"

    body_tokens: List[str] = []
    for raw_line in (text or "").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        # 注释与文件头：`//` 开头、单独一个 `#`、或 `#` 后面紧跟空格
        # （注意不能把半音阶谱的 `#1` 当成注释——`#` 后面紧跟的是数字）
        is_comment = line.startswith("//") or line == "#" or (
            line.startswith("#") and len(line) > 1 and line[1] in " \t"
        )
        if is_comment:
            payload = line.lstrip("#/ ").strip()
            lowered = payload.lower()
            for key, field in (
                ("title:", "title"),
                ("作者:", "author"),
                ("author:", "author"),
                ("bpm:", "bpm"),
                ("time:", "time"),
                ("拍号:", "time"),
                ("sub:", "sub"),
                ("细分:", "sub"),
                ("mode:", "mode"),
                ("模式:", "mode"),
            ):
                if lowered.startswith(key):
                    value = payload[len(key):].strip()
                    if field == "title":
                        meta.title = value
                    elif field == "author":
                        meta.author = value
                    elif field == "bpm":
                        try:
                            meta.bpm = int(float(value))
                        except ValueError:
                            raise ScoreIOError("文件头 bpm 不是数字：%r" % value) from None
                    elif field == "time":
                        try:
                            num, _, den = value.partition("/")
                            meta.time_signature = (int(num), int(den))
                        except ValueError:
                            raise ScoreIOError("文件头拍号格式错误：%r" % value) from None
                    elif field == "sub":
                        try:
                            meta.subdivision = int(value)
                        except ValueError:
                            raise ScoreIOError("文件头细分不是整数：%r" % value) from None
                    elif field == "mode":
                        mode = value.strip().lower()
                        if mode not in MODES:
                            raise ScoreIOError("文件头 mode 只能是 diatonic 或 chromatic，实际为 %r" % value)
                        meta.mode = mode
                    break
            continue
        # 去掉行内的小节线
        cleaned = line.replace("|", " ").replace("｜", " ")
        body_tokens.extend(cleaned.split())

    if not body_tokens:
        raise ScoreIOError("文本谱里没有任何记号")
    if meta.subdivision not in ALLOWED_SUBDIVISIONS:
        meta.subdivision = 4

    score = Score(meta)
    cursor = 0
    notes: List[ScoreNote] = []
    for index, token in enumerate(body_tokens):
        if token in _TIE_TOKENS:
            if not notes:
                raise ScoreIOError("第 %d 个记号是延音 '-'，但它前面没有音符" % (index + 1))
            notes[-1].duration += 1.0
            cursor += 1
            continue
        keys = _parse_cell(token, keymap)
        if keys:
            notes.append(ScoreNote(float(cursor), 1.0, keys))
        cursor += 1
    score.notes = notes
    score.sort()

    # 含黑键却没有标成半音阶 → 自动纠正，避免导入后一片报错
    if not score.meta.is_chromatic and score.has_sharps():
        score.meta.mode = MODE_CHROMATIC
    return score


def load_text(path: str, keymap: Optional[KeyMap] = None, defaults: Optional[ScoreMeta] = None) -> Score:
    """从文件读取文本谱。"""
    try:
        with open(path, "r", encoding="utf-8-sig") as handle:
            content = handle.read()
    except FileNotFoundError as exc:
        raise ScoreIOError("找不到文件：%s" % path) from exc
    except PermissionError as exc:
        raise ScoreIOError("没有权限读取：%s" % path) from exc
    except OSError as exc:
        raise ScoreIOError("读取失败：%s（%s）" % (path, exc)) from exc
    score = loads_text(content, keymap, defaults)
    if not score.meta.title or score.meta.title == "导入的文本谱":
        score.meta.title = os.path.splitext(os.path.basename(path))[0]
    score.path = None
    return score
