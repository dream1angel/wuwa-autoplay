# -*- coding: utf-8 -*-
"""``.score.json`` 读写（主格式）。

要点
----
* 文件头固定为 ``{"format": "wuthering-score", "version": "1.0"}``；
* 版本不同只提示不阻断（尽力兼容读取）；
* 保存采用「临时文件 + 原子替换」，避免写一半崩掉把原曲谱写坏；
* 所有错误都转成 :class:`ScoreIOError`，消息是给用户看的中文。
"""

from __future__ import annotations

import json
import os
import tempfile
from typing import Any, Dict, List, Optional, Tuple

from core.model import SCORE_FORMAT, SCORE_VERSION, Score, ScoreError

__all__ = ["ScoreIOError", "dumps_score", "loads_score", "save_score", "load_score", "read_score_text"]


class ScoreIOError(Exception):
    """曲谱文件读写错误（中文消息，可直接弹窗）。"""


def dumps_score(score: Score, indent: int = 2) -> str:
    """曲谱 → JSON 文本。"""
    payload = score.to_json()
    return json.dumps(payload, ensure_ascii=False, indent=indent) + "\n"


def loads_score(text: str, source: str = "内存") -> Score:
    """JSON 文本 → 曲谱；失败时给出带位置的友好提示。"""
    if text is None or not text.strip():
        raise ScoreIOError("文件内容为空：%s" % source)
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ScoreIOError(
            "%s 不是合法的 JSON：第 %d 行第 %d 列 %s" % (source, exc.lineno, exc.colno, exc.msg)
        ) from exc
    try:
        return Score.from_json(payload)
    except ScoreError as exc:
        raise ScoreIOError("%s 解析失败：%s" % (source, exc)) from exc
    except Exception as exc:  # pragma: no cover - 防御式
        raise ScoreIOError("%s 解析失败：%s" % (source, exc)) from exc


def read_score_text(path: str) -> str:
    """按 UTF-8（容忍 BOM）读取文本。"""
    try:
        with open(path, "r", encoding="utf-8-sig") as handle:
            return handle.read()
    except FileNotFoundError as exc:
        raise ScoreIOError("找不到文件：%s" % path) from exc
    except PermissionError as exc:
        raise ScoreIOError("没有权限读取：%s" % path) from exc
    except OSError as exc:
        raise ScoreIOError("读取失败：%s（%s）" % (path, exc)) from exc


def load_score(path: str) -> Score:
    """读取 ``.score.json``。成功时把 :attr:`Score.path` 指向该文件。"""
    text = read_score_text(path)
    score = loads_score(text, source=os.path.basename(path))
    score.path = os.path.abspath(path)
    return score


def save_score(score: Score, path: str) -> str:
    """原子写入曲谱，返回实际写入的绝对路径。"""
    target = os.path.abspath(path)
    if not target.lower().endswith(".json"):
        target += ".score.json"
    directory = os.path.dirname(target) or "."
    try:
        os.makedirs(directory, exist_ok=True)
    except OSError as exc:
        raise ScoreIOError("无法创建目录：%s（%s）" % (directory, exc)) from exc

    text = dumps_score(score)
    handle = None
    temp_path = ""
    try:
        fd, temp_path = tempfile.mkstemp(prefix=".score-", suffix=".tmp", dir=directory)
        handle = os.fdopen(fd, "w", encoding="utf-8", newline="\n")
        handle.write(text)
        handle.flush()
        os.fsync(handle.fileno())
        handle.close()
        handle = None
        os.replace(temp_path, target)
    except OSError as exc:
        raise ScoreIOError("保存失败：%s（%s）" % (target, exc)) from exc
    finally:
        if handle is not None:  # pragma: no cover - 异常清理
            handle.close()
        if temp_path and os.path.exists(temp_path):  # pragma: no cover
            try:
                os.remove(temp_path)
            except OSError:
                pass

    score.path = target
    return target
