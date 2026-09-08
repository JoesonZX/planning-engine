#!/usr/bin/env python3
"""ics 单测：RFC 5545 合规性（CRLF / 折行 / 转义 / 结构）。"""

from __future__ import annotations

import sys
import unittest
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ics import (build_vevent, clean_text, escape_ics,  # noqa: E402
                 fold_line, generate_ics)
from parser import load_config  # noqa: E402

NOW = datetime(2026, 9, 8, 12, 0, 0)


class TestEscape(unittest.TestCase):
    def test_escapes_specials(self):
        self.assertEqual(escape_ics("a;b,c\\d"), "a\\;b\\,c\\\\d")
        self.assertEqual(escape_ics("line1\nline2"), "line1\\nline2")


class TestClean(unittest.TestCase):
    def test_strips_markdown(self):
        self.assertEqual(clean_text("**9/10** 抽血（带护照）"), "9/10 抽血（带护照）")
        self.assertEqual(clean_text("- [ ] 买票"), "买票")


class TestFold(unittest.TestCase):
    def test_short_line(self):
        self.assertEqual(fold_line("short"), ["short"])

    def test_multibyte_boundary(self):
        line = "SUMMARY:" + "划" * 60  # 180 字节
        folded = fold_line(line)
        # RFC 展开规则：去掉续行的前导空格后应还原原文
        unfolded = "".join(p[1:] if i > 0 else p for i, p in enumerate(folded))
        self.assertEqual(unfolded, line)
        # 每个物理行 ≤75 字节且不截断多字节字符
        for piece in folded:
            b = piece.encode("utf-8")
            self.assertLessEqual(len(b), 75)
            piece.encode("utf-8").decode("utf-8")

    def test_continuation_prefix(self):
        folded = fold_line("x" * 100)
        self.assertTrue(folded[1].startswith(" "))


class TestVEvent(unittest.TestCase):
    def test_structure(self):
        lines = build_vevent(date(2026, 9, 10), "TB 抽血, 8:30", True,
                             "规划/x.md", 12, NOW)
        joined = "\n".join(lines)
        self.assertIn("BEGIN:VEVENT", joined)
        self.assertIn("DTSTART;VALUE=DATE:20260910", joined)
        self.assertIn("DTEND;VALUE=DATE:20260911", joined)
        self.assertIn("SUMMARY:⭐ TB 抽血\\, 8:30", joined)
        self.assertIn("TRIGGER:-P1D", joined)
        self.assertIn("END:VEVENT", joined)
        self.assertTrue(any(l.startswith("UID:") for l in lines))


class TestGenerate(unittest.TestCase):
    def test_full_calendar_crlf(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "a.md").write_text(
                "- [ ] ⭐9/24 开学\n- [ ] 买票 10/1\n普通行\n",
                encoding="utf-8")
            cfg = load_config(root)
            data = generate_ics(root, cfg, date(2026, 9, 8), NOW)
            text = data.decode("utf-8")
            self.assertIn("BEGIN:VCALENDAR", text)
            self.assertIn("DTSTART;VALUE=DATE:20260924", text)
            self.assertIn("DTSTART;VALUE=DATE:20261001", text)
            self.assertNotIn("\n", text.replace("\r\n", ""))  # 全部 CRLF
            self.assertTrue(text.endswith("\r\n"))
            # 超出 120 天视野的日期不该出现
            self.assertNotIn("20270115", text)


if __name__ == "__main__":
    unittest.main()
