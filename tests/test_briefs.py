#!/usr/bin/env python3
"""briefs 单测：触发启发式 / 任务键 / slug / 生成信任闸 / 幂等（全 mock）。"""

from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from briefs import (clean_task_text, generate_brief, is_candidate,  # noqa: E402
                    load_index, run, slugify, task_key)
from parser import parse_markdown  # noqa: E402

NOW = datetime(2026, 9, 11, 20, 0, 0)

CFG = {"timezone": "America/Los_Angeles", "horizon_days": 7,
       "model": "glm-4-flash", "monthly_budget_usd": 3.0}


def _entry(raw: str, file: str = "规划/x.md", today: date = NOW.date()):
    return parse_markdown(raw, file=file, today=today)[0]


class TestCandidate(unittest.TestCase):
    def test_starred_outside_window_is_candidate(self):
        # ⭐ 放宽窗口要求，但仍需流程关键词（⭐Orientation 这类无需检索）
        e = _entry("- [ ] ⭐预约换证 10/30")
        self.assertTrue(is_candidate(e, NOW.date()))
        e2 = _entry("- [ ] ⭐Orientation 10/30")
        self.assertFalse(is_candidate(e2, NOW.date()))

    def test_window_and_keyword(self):
        e = _entry(f"- [ ] 缴费 {date(2026, 9, 14).strftime('%m/%d')}")
        self.assertTrue(is_candidate(e, NOW.date()))

    def test_window_without_keyword_not_candidate(self):
        e = _entry(f"- [ ] 读书 {date(2026, 9, 14).strftime('%m/%d')}")
        self.assertFalse(is_candidate(e, NOW.date()))

    def test_manual_tag_bypasses_keyword(self):
        e = _entry(f"- [ ] 研究一下比特币 {date(2026, 9, 14).strftime('%m/%d')} #简报")
        self.assertTrue(is_candidate(e, NOW.date()))

    def test_done_task_never_candidate(self):
        e = _entry(f"- [x] 缴费 {date(2026, 9, 14).strftime('%m/%d')}")
        self.assertFalse(is_candidate(e, NOW.date()))

    def test_outside_window_no_star_not_candidate(self):
        e = _entry("- [ ] 缴费 12/25")
        self.assertFalse(is_candidate(e, NOW.date()))


class TestKeyAndSlug(unittest.TestCase):
    def test_key_stable_and_file_sensitive(self):
        a = task_key("规划/x.md", "- [ ] 缴费 9/14")
        b = task_key("规划/x.md", "- [x] 缴费 9/14")
        c = task_key("规划/y.md", "- [ ] 缴费 9/14")
        self.assertEqual(a, b)      # 勾选态不换键
        self.assertNotEqual(a, c)   # 文件参与键

    def test_clean_text_strips_checkbox(self):
        self.assertEqual(clean_task_text("- [x] 加州 DMV 预约"), "加州 DMV 预约")

    def test_slugify_cjk_and_collision(self):
        taken: set[str] = set()
        s1 = slugify("加州 DMV 预约（线上约……）", taken)
        s2 = slugify("加州 DMV 预约（线上约……）", taken)
        self.assertTrue(all(ch.isalnum() or "\u4e00" <= ch <= "\u9fff" for ch in s1))
        self.assertNotEqual(s1, s2)


GOOD_BRIEF = "# 📋 任务简报 · 测试\n## 来源\n- https://example.com\n"
NO_CITE = "# 📋 任务简报 · 测试\n没有任何链接。"


class TestGenerate(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.entry = _entry("- [ ] ⭐缴费 9/14")

    def tearDown(self):
        self.tmp.cleanup()

    def _gen(self, mock_resp, mock_data=None):
        with mock.patch("briefs.call_glm", return_value=(mock_resp, mock_data)):
            return generate_brief(self.entry, self.root, CFG, "key",
                                  self.root / "u.json", NOW, "ceofei")

    def test_writes_brief_with_citation(self):
        fname = self._gen(GOOD_BRIEF)
        self.assertEqual(fname, "ceofei.md")
        body = (self.root / "reports/briefs/ceofei.md").read_text(encoding="utf-8")
        self.assertIn("任务简报", body)

    def test_no_citation_dropped(self):
        self.assertIsNone(self._gen(NO_CITE, {}))
        self.assertFalse((self.root / "reports/briefs").exists())

    def test_links_from_response_data_accepted_and_appended(self):
        # bigmodel 联网对话的 URL 在响应数据的 search_result 里，正文只有 ref_N 引用
        data = {"search_result": [{"title": "DMV REAL ID", "link": "https://www.dmv.ca.gov/real-id"}]}
        fname = self._gen(NO_CITE, data)
        self.assertEqual(fname, "ceofei.md")
        body = (self.root / "reports/briefs/ceofei.md").read_text(encoding="utf-8")
        self.assertIn("## 来源（检索结果）", body)
        self.assertIn("https://www.dmv.ca.gov/real-id", body)

    def test_fences_stripped(self):
        fname = self._gen(f"```markdown\n{GOOD_BRIEF}```")
        body = (self.root / "reports/briefs/ceofei.md").read_text(encoding="utf-8")
        self.assertNotIn("```", body)
        self.assertEqual(fname, "ceofei.md")


class TestRun(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "规划").mkdir()
        (self.root / "规划" / "x.md").write_text(
            "- [ ] ⭐缴费学费 9/14\n- [ ] 读书 9/14\n", encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def test_run_generates_and_is_idempotent(self):
        with mock.patch("briefs.call_glm", return_value=(GOOD_BRIEF, {})):
            made = run(self.root, CFG, "key", self.root / "u.json", NOW)
            self.assertEqual(len(made), 1)
            with mock.patch("briefs.call_glm") as m2:
                made2 = run(self.root, CFG, "key", self.root / "u.json", NOW)
                self.assertEqual(made2, [])
                m2.assert_not_called()          # 幂等：不再调 LLM
        self.assertEqual(len(load_index(self.root)), 1)

    def test_no_key_generates_nothing(self):
        made = run(self.root, CFG, None, self.root / "u.json", NOW)
        self.assertEqual(made, [])


if __name__ == "__main__":
    unittest.main()
