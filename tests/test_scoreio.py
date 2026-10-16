# -*- coding: utf-8 -*-
"""阶段 3 测试：曲谱 JSON / 文本谱读写与导出。

临时文件写在 ``tests/_tmp``（跑完由 run_all.py 自动清理），不会污染项目目录。
"""

from __future__ import annotations

import json
import os
import shutil

from core.model import Score, ScoreNote
from core.notes import Key, KeyMap
from scoreio import json_io, text_io
from scoreio.json_io import ScoreIOError

TMP_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_tmp")


def _tmp(name: str) -> str:
    os.makedirs(TMP_DIR, exist_ok=True)
    return os.path.join(TMP_DIR, name)


def _sample_score() -> Score:
    """含和弦、延音、跨小节乐句的样例曲谱。"""
    score = Score.empty(title="样例曲", bpm=132, time_signature=(4, 4), subdivision=8)
    score.meta.author = "测试"
    score.notes = [
        ScoreNote(0, 4, [Key.from_degree(0, 1)]),
        ScoreNote(4, 2, [Key.from_degree(0, 3), Key.from_degree(1, 5)]),
        ScoreNote(6, 10, [Key.from_degree(-1, 7)]),      # 跨小节的延音
        ScoreNote(16, 2, [Key.from_degree(1, 1), Key.from_degree(0, 5), Key.from_degree(-1, 3)]),
        ScoreNote(20.5, 1, [Key.from_degree(0, 2)]),     # 小数位置：JSON 支持，文本谱会取整
        ScoreNote(24, 8, [Key.from_degree(0, 4)]),
    ]
    score.sort()
    return score


def _compare(left: Score, right: Score, check_beats: bool = True) -> None:
    assert left.meta.title == right.meta.title
    assert left.meta.bpm == right.meta.bpm
    assert left.meta.time_signature == right.meta.time_signature
    assert left.meta.subdivision == right.meta.subdivision
    assert len(left.notes) == len(right.notes), (len(left.notes), len(right.notes))
    for a, b in zip(left.notes, right.notes):
        if check_beats:
            assert abs(a.beat - b.beat) < 1e-6, (a.beat, b.beat)
        assert abs(a.duration - b.duration) < 1e-6
        assert sorted(a.keys) == sorted(b.keys)


# --------------------------------------------------------------------------- #
# JSON
# --------------------------------------------------------------------------- #

def test_json_dumps_shape() -> None:
    text = json_io.dumps_score(_sample_score())
    payload = json.loads(text)
    assert payload["format"] == "wuthering-score"
    assert payload["version"] == "1.1"
    assert payload["meta"]["timeSignature"] == [4, 4]
    assert payload["meta"]["mode"] == "diatonic"
    assert payload["notes"][1]["keys"][0] == {"octave": 1, "degree": 5}
    assert text.endswith("\n")


def test_json_file_roundtrip() -> None:
    score = _sample_score()
    # 不写后缀也应自动补成 .score.json
    path = json_io.save_score(score, _tmp("roundtrip"))
    assert path.endswith(".score.json")
    assert os.path.exists(path)

    loaded = json_io.load_score(path)
    _compare(score, loaded)
    assert loaded.path == os.path.abspath(path)
    assert not [i for i in loaded.validate() if i.level == "error"]


def test_json_save_is_atomic_no_temp_left() -> None:
    json_io.save_score(_sample_score(), _tmp("atomic.score.json"))
    leftovers = [f for f in os.listdir(TMP_DIR) if f.startswith(".score-") and f.endswith(".tmp")]
    assert leftovers == [], "保存后不应残留临时文件：%s" % leftovers
    # 覆盖保存同一文件应当成功
    json_io.save_score(_sample_score(), _tmp("atomic.score.json"))


def test_json_error_messages() -> None:
    cases = [
        (os.path.join(TMP_DIR, "不存在.score.json"), "找不到文件"),
        (_tmp("empty.score.json"), "内容为空"),
        (_tmp("broken.score.json"), "不是合法的 JSON"),
        (_tmp("other.score.json"), "不是本程序的曲谱文件"),
        (_tmp("nokeys.score.json"), "解析失败"),
    ]
    with open(_tmp("empty.score.json"), "w", encoding="utf-8") as handle:
        handle.write("   \n")
    with open(_tmp("broken.score.json"), "w", encoding="utf-8") as handle:
        handle.write('{"format": "wuthering-score", "notes": [,]}')
    with open(_tmp("other.score.json"), "w", encoding="utf-8") as handle:
        handle.write('{"format": "another-app", "notes": []}')
    with open(_tmp("nokeys.score.json"), "w", encoding="utf-8") as handle:
        handle.write('{"format": "wuthering-score", "version": "1.0", "notes": [{"beat": 0, "duration": 1}]}')

    for path, expected in cases:
        try:
            json_io.load_score(path)
            raise AssertionError("应当报错：%s" % path)
        except ScoreIOError as exc:
            assert expected in str(exc), "%s 的提示里应包含 %r，实际：%s" % (path, expected, exc)


def test_json_version_warning_but_loadable() -> None:
    payload = _sample_score().to_json()
    payload["version"] = "9.9"
    score = json_io.loads_score(json.dumps(payload))
    assert score.warnings and "9.9" in score.warnings[0]
    assert len(score.notes) == 6


# --------------------------------------------------------------------------- #
# 文本谱
# --------------------------------------------------------------------------- #

def test_text_roundtrip() -> None:
    keymap = KeyMap()
    score = Score.empty(title="文本往返", bpm=100, time_signature=(4, 4), subdivision=4)
    score.notes = [
        ScoreNote(0, 1, [Key.from_degree(0, 1)]),
        ScoreNote(1, 4, [Key.from_degree(0, 3), Key.from_degree(1, 5)]),   # 和弦 + 延音
        ScoreNote(8, 1, [Key.from_degree(-1, 1)]),
        ScoreNote(9, 2, [Key.from_degree(1, 7)]),
    ]
    score.sort()
    text = text_io.dumps_text(score, keymap, bars_per_line=2)
    assert "t+d" in text, text  # 和弦：高音5(t) + 中音3(d)，高音在前
    assert "|" in text and "-" in text and "0" in text
    again = text_io.loads_text(text, keymap)
    _compare(score, again)
    assert again.meta.bpm == 100 and again.meta.subdivision == 4


def test_text_roundtrip_keeps_ties_and_rests() -> None:
    """延音与休止必须精确还原。"""
    raw = "Q 0 E - - R | 0 0 A+F - 0 0 0 0"
    score = text_io.loads_text(raw)
    notes = score.notes
    # Q 在第 0 格、E 在第 2 格（延音到 3、4 格）、R 在第 5 格、A+F 在第 8 格
    assert [n.beat for n in notes] == [0.0, 2.0, 5.0, 8.0], [n.beat for n in notes]
    assert notes[1].keys == [Key.from_degree(1, 3)]
    assert notes[1].duration == 3
    assert notes[3].keys == [Key.from_degree(0, 1), Key.from_degree(0, 4)]
    assert notes[3].duration == 2


def test_text_import_syntax_variants() -> None:
    raw = "\n".join(
        [
            "# wuthering-score text 1.0",
            "# title: 语法测试",
            "# bpm: 90",
            "# time: 3/4",
            "# sub: 16",
            "1 2 ^3 +4 -5 高6",
            "中7 [AF] a+f z 0",
        ]
    )
    score = text_io.loads_text(raw)
    assert score.meta.title == "语法测试"
    assert score.meta.bpm == 90
    assert score.meta.time_signature == (3, 4)
    assert score.meta.subdivision == 16
    expected = [
        (0, Key.from_degree(0, 1)),
        (1, Key.from_degree(0, 2)),
        (2, Key.from_degree(1, 3)),
        (3, Key.from_degree(1, 4)),
        (4, Key.from_degree(-1, 5)),
        (5, Key.from_degree(1, 6)),
        (6, Key.from_degree(0, 7)),
        (7, Key.from_degree(0, 1)),   # [AF] → a + f
        (8, Key.from_degree(0, 1)),   # a+f
        (9, Key.from_degree(-1, 1)),  # z
    ]
    for (beat, key), note in zip(expected, score.notes[: len(expected)]):
        assert note.beat == float(beat), (note.beat, beat)
        assert key in note.keys, (key, note.keys)
    assert score.notes[7].keys == [Key.from_degree(0, 1), Key.from_degree(0, 4)]
    assert len(score.notes) == 10  # 最后的 0 是休止


def test_text_custom_keymap() -> None:
    keymap = KeyMap({"1,1": "o"})
    score = Score.empty(subdivision=4)
    score.notes = [ScoreNote(0, 1, [Key.from_degree(1, 1)])]
    text = text_io.dumps_text(score, keymap)
    assert "o" in text, text
    again = text_io.loads_text(text, keymap)
    assert again.notes[0].keys == [Key.from_degree(1, 1)]


def test_text_save_and_load_file() -> None:
    score = Score.empty(title="文件往返", subdivision=4)
    score.notes = [ScoreNote(0, 1, [Key.from_degree(0, 1)]), ScoreNote(4, 4, [Key.from_degree(0, 5)])]
    path = text_io.save_text(score, _tmp("sample"), keymap=KeyMap())
    assert path.endswith(".txt") and os.path.exists(path)
    loaded = text_io.load_text(path)
    _compare(score, loaded)
    assert loaded.meta.title == "文件往返"


def test_text_error_messages() -> None:
    cases = [
        ("", "没有任何记号"),
        ("K L P", "不在当前按键映射表"),
        ("- q w", "前面没有音符"),
        ("# bpm: abc\nq", "bpm 不是数字"),
        ("1 8", "音级超出"),
    ]
    for raw, expected in cases:
        try:
            text_io.loads_text(raw)
            raise AssertionError("应当报错：%r" % raw)
        except ScoreIOError as exc:
            assert expected in str(exc), "%r 的提示应包含 %r，实际：%s" % (raw, expected, exc)


def test_text_export_snaps_fractional_beats() -> None:
    score = Score.empty(subdivision=4)
    score.notes = [ScoreNote(0.4, 1, [Key.from_degree(0, 1)])]
    text = text_io.dumps_text(score)
    assert "四舍五入" in text
    assert text_io.loads_text(text).notes[0].beat == 0.0


# --------------------------------------------------------------------------- #
# 图片 / PDF 导出
# --------------------------------------------------------------------------- #

def test_render_exports_if_qt_available() -> None:
    try:
        import PySide6  # noqa: F401
    except Exception:
        print("    （未安装 PySide6，跳过图片/PDF 导出测试）")
        return
    from scoreio import render_io

    score = _sample_score()
    png = render_io.export_png(score, _tmp("score.png"), KeyMap())
    pdf = render_io.export_pdf(score, _tmp("score.pdf"), KeyMap())
    assert os.path.getsize(png) > 2000, "PNG 内容过小，可能没有渲染成功"
    assert os.path.getsize(pdf) > 1000, "PDF 内容过小，可能没有渲染成功"
    with open(pdf, "rb") as handle:
        assert handle.read(5) == b"%PDF-"


def cleanup() -> None:
    """由 run_all.py 在结束时调用。"""
    shutil.rmtree(TMP_DIR, ignore_errors=True)
