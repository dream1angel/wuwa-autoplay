# -*- coding: utf-8 -*-
"""曲谱文件读写层。

* :mod:`scoreio.json_io` —— 主格式 ``.score.json``（带版本号、原子写入）
* :mod:`scoreio.text_io` —— 社区分享用的纯文本谱
* :mod:`scoreio.render_io` —— 导出 PNG / PDF（按需惰性导入 PySide6）

本层之外只有 :mod:`ui` 会用到 Qt，``core`` 保持纯净。
"""

from __future__ import annotations

__all__ = ["SCORE_SUFFIX", "TEXT_SUFFIX", "JSON_FILTER", "TEXT_FILTER"]

#: 主格式文件后缀
SCORE_SUFFIX = ".score.json"
#: 文本谱后缀
TEXT_SUFFIX = ".txt"

JSON_FILTER = "曲谱文件 (*.score.json *.json);;所有文件 (*)"
TEXT_FILTER = "文本谱 (*.txt);;所有文件 (*)"
