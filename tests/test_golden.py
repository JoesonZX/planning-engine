#!/usr/bin/env python3
"""golden 比对测试：当前代码输出必须与快照逐字节一致（规范化时间戳后）。

快照仅捕获当日有效（相对日期跨天即失配）——golden 是重构期的 approval 护栏，不是永久回归
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


class TestGolden(unittest.TestCase):
    def setUp(self):
        manifest = GOLDEN_DIR / "manifest.json"
        if not manifest.exists():
            self.skipTest("no golden captured (run: python tests/capture_golden.py)")
        captured = dt.datetime.fromisoformat(
            json.loads(manifest.read_text(encoding="utf-8"))["captured_at"])
        now = dt.datetime.now(dt.timezone.utc)
        # fixture 的日期相对捕获日生成（today+1 等），跨天即失配——
        # golden 仅捕获当日有效（重构期工具，过期自动 skip 不挡生产 CI；
        # v6 事故：48h 窗太宽，晨间 workflow 被隔日快照挡住）
        if captured.date() != now.date():
            self.skipTest(f"golden captured {captured.date()} ≠ today — re-capture for refactor work")

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
