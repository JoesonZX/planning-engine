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

from zoneinfo import ZoneInfo
NOW = datetime(2026, 9, 11, 20, 0, 0, tzinfo=ZoneInfo("America/Los_Angeles"))

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


class TestReviewFixes(unittest.TestCase):
    """v5 复查（2026.9.11）补的守卫：尝试上限 / 预算帽 / 孤儿清理 / 行数闸。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "规划").mkdir()
        (self.root / "u.json").write_text("", encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def _vault(self, lines: str):
        (self.root / "规划" / "x.md").write_text(lines, encoding="utf-8")

    def test_attempts_cap_counts_failures(self):
        self._vault("".join(f"- [ ] ⭐缴费{i} 9/14\n" for i in range(6)))
        with mock.patch("briefs.call_glm", return_value=(NO_CITE, {})) as m:
            run(self.root, CFG, "key", self.root / "u.json", NOW)
            self.assertLessEqual(m.call_count, 3)  # 失败也计数，不无限重试

    def test_budget_gate_blocks_new_briefs(self):
        self._vault("- [ ] ⭐缴费 9/14\n")
        (self.root / "u.json").write_text(
            '{"month": "2026-09", "est_cost_usd": 9.9}\n', encoding="utf-8")
        with mock.patch("briefs.call_glm") as m:
            made = run(self.root, CFG, "key", self.root / "u.json", NOW)
            self.assertEqual(made, [])
            m.assert_not_called()

    def test_orphan_cleanup_with_seed_and_shared_file(self):
        self._vault("- [ ] ⭐缴费 9/14\n")
        from briefs import BRIEFS_DIR, save_index
        (self.root / BRIEFS_DIR).mkdir(parents=True)
        (self.root / BRIEFS_DIR / "manual.md").write_text("手工简报", encoding="utf-8")
        (self.root / BRIEFS_DIR / "auto.md").write_text("自动简报", encoding="utf-8")
        from briefs import task_key
        k_live = task_key("规划/x.md", "- [ ] ⭐缴费 9/14")
        k_gone = task_key("规划/x.md", "- [ ] 已删除的任务 9/14")
        k_seed = task_key("规划/x.md", "- [ ] 已删除但有手工简报 9/14")
        save_index(self.root, {
            k_live: {"file": "manual.md", "ts": "2026-09-11T12:00:00-07:00"},
            k_gone: {"file": "auto.md", "ts": "2026-09-11T12:00:00-07:00"},
            k_seed: {"file": "manual.md", "seeded": "manual",
                     "ts": "2026-09-11T12:00:00-07:00"},
        })
        with mock.patch("briefs.call_glm", return_value=(GOOD_BRIEF, {})):
            run(self.root, CFG, "key", self.root / "u.json", NOW)
        index = load_index(self.root)
        self.assertIn(k_live, index)
        self.assertNotIn(k_gone, index)      # 已删任务 → 清
        self.assertIn(k_seed, index)         # seeded → 永不清
        self.assertFalse((self.root / BRIEFS_DIR / "auto.md").exists())
        self.assertTrue((self.root / BRIEFS_DIR / "manual.md").exists())  # 仍被引用

    def test_seeded_entry_never_regenerated(self):
        self._vault("- [ ] ⭐缴费 9/14\n")
        from briefs import BRIEFS_DIR, save_index, task_key
        (self.root / BRIEFS_DIR).mkdir(parents=True)
        save_index(self.root, {task_key("规划/x.md", "- [ ] ⭐缴费 9/14"):
                              {"file": "manual.md", "seeded": "manual",
                               "ts": "2026-09-01T12:00:00-07:00"}})
        with mock.patch("briefs.call_glm") as m:
            run(self.root, CFG, "key", self.root / "u.json", NOW)
            m.assert_not_called()

    def test_line_limit_guard(self):
        long_brief = "# 📋 任务简报\n" + "\n".join(
            f"- 行 {i} https://example.com" for i in range(80))
        e = _entry("- [ ] ⭐缴费 9/14")
        with mock.patch("briefs.call_glm", return_value=(long_brief, {})):
            fname = __import__("briefs").generate_brief(
                e, self.root, CFG, "key", self.root / "u.json", NOW, "toolong")
        self.assertIsNone(fname)
