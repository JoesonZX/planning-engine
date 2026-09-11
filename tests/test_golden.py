#!/usr/bin/env python3
"""golden 比对测试：当前代码输出必须与快照逐字节一致（规范化时间戳后）。

快照超过 48h 自动 skip——golden 是重构期的 approval 护栏，不是永久回归
测试（永久守卫是 parser/ics/triage/profile/quiet 的单测）。重构时先跑
capture_golden.py 再动代码。快照文件用 .golden 后缀防止被 parser 吞掉。
"""

from __future__ import annotations

import datetime as dt
import json
import shutil
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from golden_common import collect_outputs, normalize  # noqa: E402
from golden_vault import build_fixture  # noqa: E402

GOLDEN_DIR = Path(__file__).resolve().parent / "golden"
MAX_AGE = dt.timedelta(hours=48)


class TestGolden(unittest.TestCase):
    def setUp(self):
        manifest = GOLDEN_DIR / "manifest.json"
        if not manifest.exists():
            self.skipTest("no golden captured (run: python tests/capture_golden.py)")
        age = dt.datetime.now(dt.timezone.utc) - dt.datetime.fromisoformat(
            json.loads(manifest.read_text(encoding="utf-8"))["captured_at"])
        if age > MAX_AGE:
            self.skipTest(f"golden stale ({age}) — re-capture before refactoring")

    def test_outputs_unchanged(self):
        root, _ = build_fixture()
        try:
            actual = {name: normalize(text)
                      for name, text in collect_outputs(root).items()}
        finally:
            shutil.rmtree(root, ignore_errors=True)
        golden_files = sorted(p.name for p in GOLDEN_DIR.glob("*.golden"))
        self.assertTrue(golden_files, "golden dir empty")
        for fname in golden_files:
            name = fname[: -len(".golden")]
            with self.subTest(output=name):
                # newline=""：保留 CRLF 原样（默认 universal newlines 会吃掉 \r）
                with open(GOLDEN_DIR / fname, encoding="utf-8", newline="") as f:
                    expected = f.read()
                self.assertEqual(actual[name], expected)


if __name__ == "__main__":
    unittest.main()
