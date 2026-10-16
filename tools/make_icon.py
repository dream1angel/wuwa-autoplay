# -*- coding: utf-8 -*-
"""生成 assets/app.ico（纯 Python，不依赖任何第三方库）。

build_exe.ps1 在找不到图标时会自动调用它。
设计：深蓝渐变圆角方块 + 白色八分音符；用 3 倍超采样做抗锯齿。

用法::

    python tools/make_icon.py [输出路径]
"""

from __future__ import annotations

import math
import os
import struct
import sys

SIZES = (16, 24, 32, 48, 64, 128, 256)
SUPERSAMPLE = 3

ACCENT_TOP = (61, 123, 253)
ACCENT_BOTTOM = (30, 62, 148)
WHITE = (255, 255, 255)
OUTLINE = (18, 22, 32)


def _inside_rounded_rect(u: float, v: float, radius: float) -> bool:
    """(u, v) ∈ [0,1]²，判断是否落在圆角方块内。"""
    if not (0.0 <= u <= 1.0 and 0.0 <= v <= 1.0):
        return False
    left, top, right, bottom = radius, radius, 1.0 - radius, 1.0 - radius
    cx = min(max(u, left), right)
    cy = min(max(v, top), bottom)
    return (u - cx) ** 2 + (v - cy) ** 2 <= radius * radius + 1e-9


def _segment_distance(u: float, v: float, x1: float, y1: float, x2: float, y2: float) -> float:
    dx, dy = x2 - x1, y2 - y1
    length_sq = dx * dx + dy * dy
    if length_sq <= 1e-12:
        return math.hypot(u - x1, v - y1)
    t = max(0.0, min(1.0, ((u - x1) * dx + (v - y1) * dy) / length_sq))
    return math.hypot(u - (x1 + t * dx), v - (y1 + t * dy))


def _note_mask(u: float, v: float) -> bool:
    """白色八分音符：椭圆符头 + 符干 + 符尾。"""
    # 符头
    if ((u - 0.375) / 0.175) ** 2 + ((v - 0.715) / 0.125) ** 2 <= 1.0:
        return True
    # 符干
    if 0.505 <= u <= 0.565 and 0.235 <= v <= 0.715:
        return True
    # 符尾（两段粗线拼出的弯钩）
    if _segment_distance(u, v, 0.535, 0.245, 0.735, 0.415) <= 0.055:
        return True
    if _segment_distance(u, v, 0.735, 0.415, 0.645, 0.545) <= 0.045:
        return True
    return False


def _mix(a, b, t: float):
    return tuple(int(round(a[i] + (b[i] - a[i]) * t)) for i in range(3))


def render(size: int):
    """返回顶层到下层排列的 RGBA 行列表。"""
    scale = SUPERSAMPLE
    big = size * scale
    accum = [[[0, 0, 0, 0] for _ in range(size)] for _ in range(size)]

    for y in range(big):
        v = (y + 0.5) / big
        for x in range(big):
            u = (x + 0.5) / big
            if not _inside_rounded_rect(u, v, 0.22):
                continue
            color = _mix(ACCENT_TOP, ACCENT_BOTTOM, v)
            if _note_mask(u, v):
                color = WHITE
            target = accum[y // scale][x // scale]
            target[0] += color[0]
            target[1] += color[1]
            target[2] += color[2]
            target[3] += 255

    rows = []
    total = scale * scale
    for y in range(size):
        row = []
        for x in range(size):
            r, g, b, a = accum[y][x]
            if a == 0:
                row.append((0, 0, 0, 0))
            else:
                samples = a / 255.0
                row.append((int(r / samples), int(g / samples), int(b / samples), int(a / total)))
        rows.append(row)
    return rows


def _ico_image(size: int, rows) -> bytes:
    """把 RGBA 行按 ICO 里的 BMP 格式打包（含 AND 掩码）。"""
    header = struct.pack(
        "<IiiHHIIiiII",
        40,          # biSize
        size,        # biWidth
        size * 2,    # biHeight（XOR + AND）
        1,           # biPlanes
        32,          # biBitCount
        0,           # biCompression = BI_RGB
        size * size * 4,
        0, 0, 0, 0,
    )
    xor = bytearray()
    for row in reversed(rows):  # BMP 是自下而上
        for r, g, b, a in row:
            xor += bytes((b, g, r, a))
    and_mask = bytes(size * (((size + 31) // 32) * 4))
    return header + bytes(xor) + and_mask


def build_ico(path: str) -> str:
    images = [(size, _ico_image(size, render(size))) for size in SIZES]
    header = struct.pack("<HHH", 0, 1, len(images))
    offset = len(header) + 16 * len(images)

    directory = bytearray()
    payload = bytearray()
    for size, data in images:
        directory += struct.pack(
            "<BBBBHHII",
            0 if size >= 256 else size,
            0 if size >= 256 else size,
            0,
            0,
            1,
            32,
            len(data),
            offset + len(payload),
        )
        payload += data

    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    with open(path, "wb") as handle:
        handle.write(header + bytes(directory) + bytes(payload))
    return os.path.abspath(path)


def main(argv) -> int:
    target = argv[0] if argv else os.path.join("assets", "app.ico")
    path = build_ico(target)
    print("已生成图标：%s（%d 种尺寸，%.1f KB）" % (path, len(SIZES), os.path.getsize(path) / 1024.0))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
