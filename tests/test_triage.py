#!/usr/bin/env python3
"""triage 单测：红线三重防御与 inbox 重写规则（数据永不丢失的守卫）。"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from triage import (SECTION_TITLE, append_to_target, is_private,  # noqa: E402
                    rewrite_inbox, triage)

CFG = {
    "timezone": "America/Los_Angeles",
    "inbox_file": "inbox.md",
    "triage_targets": ["todo.md"],
    "model": "glm-4-flash",
    "monthly_budget_usd": 3.0,
    "skip_files": [],
    "skip_dirs": [".git", "reports", ".github"],
}


class TestPrivateKeywords(unittest.TestCase):
    def test_hits(self):
        for text in ("记录一下今天的心情", "焦虑发作了", "和她恋爱的想法",
                     "最近失眠严重", "emo了"):
            self.assertTrue(is_private(text), text)

    def test_non_private(self):
        self.assertFalse(is_private("买火车票 9/17"))
        self.assertFalse(is_private("TB 抽血预约"))


class TestAppend(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "todo.md").write_text("# 清单\n\n- [ ] 原有条目\n", encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def test_appends_under_section_and_preserves_original(self):
        self.assertTrue(append_to_target(self.root, "todo.md", "买票", __import__("datetime").date(2026, 9, 13)))
        body = (self.root / "todo.md").read_text(encoding="utf-8")
        self.assertIn("- [ ] 原有条目", body)
        self.assertIn(SECTION_TITLE, body)
        self.assertIn("- [ ] 买票 （📥 9/13 来自 inbox）", body)

    def test_rejects_missing_file(self):
        self.assertFalse(append_to_target(self.root, "nope.md", "x", __import__("datetime").date(2026, 9, 13)))


class TestTriageFlow(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "todo.md").write_text("# 清单\n", encoding="utf-8")
        (self.root / "inbox.md").write_text(
            "# inbox\n\n- [ ] 买火车票\n今天心情很差\n模糊的一条\n", encoding="utf-8")
        (self.root / "reports").mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def test_no_llm_key_holds_everything(self):
        summary = triage(self.root, CFG, api_key=None)
        self.assertEqual(summary["classified"], 0)
        self.assertEqual(summary["total"], 3)
        body = (self.root / "inbox.md").read_text(encoding="utf-8")
        self.assertIn("买火车票", body)
        # 数据永不丢失：所有条目都还在 inbox 里
        self.assertEqual(body.count("待人工"), 3)

    def test_private_never_leaves_inbox(self):
        # 无 key 情况下全部 hold，但私人项必须被单独计数（没进 LLM）
        summary = triage(self.root, CFG, api_key=None)
        self.assertEqual(summary["held_private"], 1)

    def test_rewrite_inbox_format(self):
        rewrite_inbox(self.root, CFG, ["a", "b"])
        body = (self.root / "inbox.md").read_text(encoding="utf-8")
        self.assertIn("⏳ 待人工：a", body)
        self.assertIn("⏳ 待人工：b", body)

    def test_empty_inbox_stays_header_only(self):
        (self.root / "inbox.md").write_text("# inbox\n", encoding="utf-8")
        triage(self.root, CFG, api_key=None)
        body = (self.root / "inbox.md").read_text(encoding="utf-8")
        self.assertTrue(body.startswith("# inbox"))
        self.assertNotIn("待人工：", body)


if __name__ == "__main__":
    unittest.main()
