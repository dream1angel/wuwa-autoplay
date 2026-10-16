# -*- coding: utf-8 -*-
"""测试入口：不依赖 pytest，直接 ``python tests/run_all.py``。

用法::

    python tests/run_all.py                # 运行全部用例
    python tests/run_all.py test_engine    # 只运行文件名包含该子串的用例
    python tests/run_all.py -v             # 打印每个用例的耗时
"""

from __future__ import annotations

import importlib.util
import os
import shutil
import sys
import time
import traceback
from typing import Callable, List, Tuple

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(TESTS_DIR)
TMP_DIR = os.path.join(TESTS_DIR, "_tmp")
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def _load_module(path: str):
    name = os.path.splitext(os.path.basename(path))[0]
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:  # pragma: no cover
        raise ImportError("无法加载测试模块 %s" % path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _collect(module) -> List[Tuple[str, Callable[[], None]]]:
    cases = []
    for attr in sorted(dir(module)):
        if attr.startswith("test_") and callable(getattr(module, attr)):
            cases.append((attr, getattr(module, attr)))
    return cases


def main(argv: List[str]) -> int:
    """入口：跑完自动清理 tests/_tmp，保证项目目录干净。"""
    shutil.rmtree(TMP_DIR, ignore_errors=True)
    try:
        return _run_all(argv)
    finally:
        shutil.rmtree(TMP_DIR, ignore_errors=True)


def _run_all(argv: List[str]) -> int:
    verbose = "-v" in argv or "--verbose" in argv
    filters = [a for a in argv if not a.startswith("-")]

    files = sorted(
        f for f in os.listdir(TESTS_DIR) if f.startswith("test_") and f.endswith(".py")
    )
    if filters:
        files = [f for f in files if any(flt in f for flt in filters)]
    if not files:
        print("没有匹配的测试文件：%s" % ", ".join(filters))
        return 1

    total = passed = 0
    failures: List[Tuple[str, str]] = []

    for filename in files:
        module = _load_module(os.path.join(TESTS_DIR, filename))
        cases = _collect(module)
        print("=" * 68)
        print("%s（%d 个用例）" % (filename, len(cases)))
        for name, func in cases:
            total += 1
            started = time.perf_counter()
            try:
                func()
            except Exception:
                failures.append(("%s::%s" % (filename, name), traceback.format_exc()))
                print("  [失败] %s" % name)
            else:
                passed += 1
                cost = (time.perf_counter() - started) * 1000.0
                if verbose or cost > 200:
                    print("  [通过] %s（%.1f ms）" % (name, cost))
                else:
                    print("  [通过] %s" % name)

    print("=" * 68)
    if failures:
        for title, tb in failures:
            print("\n---- %s ----" % title)
            print(tb.rstrip())
        print("结果：%d/%d 通过，%d 失败" % (passed, total, len(failures)))
        return 1
    print("结果：%d/%d 全部通过" % (passed, total))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
