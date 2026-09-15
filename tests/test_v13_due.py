#!/usr/bin/env python3
"""v13 单测：决策卡复盘到期（周报节+ICS 事件）、待定问题年龄、slugify 词边界。"""

from __future__ import annotations

import datetime as dt
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from briefs import slugify  # noqa: E402
from ics import generate_ics, load_card_reviews  # noqa: E402
from parser import load_config  # noqa: E402
from vault import Snapshot  # noqa: E402
from weekly_review import render_due_and_pending  # noqa: E402

PT = dt.timezone(dt.timedelta(hours=-7))
NOW = dt.datetime(2026, 9, 13, 20, 0, tzinfo=PT)
CFG = {"timezone": "America/Los_Angeles", "horizon_days": 7, "stale_days": 14,
       "monthly_budget_usd": 3.0, "inbox_file": "inbox.md"}


def make_vault(root: Path, card_line: str = "**复盘日期**：2026-09-16（随月度复盘）\n") -> None:
    (root / "决策").mkdir(parents=True)
    (root / "决策" / "2026-09-14 学习卡.md").write_text(
        f"# 决策卡 · 2026-09-14 · 学习卡\n\n- **拍板**：B\n{card_line}\n",
        encoding="utf-8")
    (root / "规划").mkdir()
    (root / "规划" / "term.md").write_text(
        "# 学期\n- [ ] 开学\n\n## 待定问题\n1. 主目标教授人选（10 月定）\n2. 项目连续性\n",
        encoding="utf-8")


class TestCardReviews(unittest.TestCase):
    def test_load_and_due_in_weekly(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_vault(root)  # 复盘 9/16，now 9/13 → 3 天后，在 7 天窗内
            cards = load_card_reviews(root)
            self.assertEqual([(d, t) for d, t in cards],
                             [(dt.date(2026, 9, 16), "决策复盘 · 学习卡")])
            snap = Snapshot.load(root, CFG, NOW)
            lines = render_due_and_pending(snap, snap.today)
            body = "\n".join(lines)
            self.assertIn("决策卡复盘到期", body)
            self.assertIn("《学习卡》——3 天后", body)

    def test_overdue_and_far_future_and_monthly(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_vault(root,
                       card_line="**复盘日期**：2026-09-10（逾期）；2026-12 中期检查\n")
            cards = load_card_reviews(root)
            # 月级模糊日期（2026-12）不进日历；完整日期保留
            self.assertEqual([d for d, _ in cards], [dt.date(2026, 9, 10)])
            snap = Snapshot.load(root, CFG, NOW)
            body = "\n".join(render_due_and_pending(snap, snap.today))
            self.assertIn("⚠️ 逾期 3 天", body)

    def test_pending_questions_with_age(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_vault(root)
            snap = Snapshot.load(root, CFG, NOW)
            body = "\n".join(render_due_and_pending(snap, snap.today))
            # 非 git 目录 → 无年龄标注（年龄路径由带 git 历史的 golden 覆盖）
            self.assertIn("《term.md》待定问题（2 条）：", body)
            self.assertIn("主目标教授人选", body)
            self.assertIn("项目连续性", body)

    def test_card_review_event_in_ics(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_vault(root)
            ics = generate_ics(root, CFG, dt.date(2026, 9, 13),
                               dt.datetime(2026, 9, 13, 12, 0)).decode("utf-8")
            self.assertIn("SUMMARY:决策复盘 · 学习卡", ics)
            self.assertIn("DTSTART;VALUE=DATE:20260916", ics)


class TestSlugifyWordBoundary(unittest.TestCase):
    def test_truncation_backs_off_ascii_token(self):
        taken: set[str] = set()
        slug = slugify(f"9/26 见教授 #1：当前 LAM/VLA 项目组核心问题Xiaolong 走后", taken)
        self.assertLessEqual(len(slug), 20)
        self.assertFalse(slug.endswith("X"))  # 不留半个词

    def test_weekday_marker_stripped(self):
        slug = slugify("9/26（六）见教授 #1：项目归属", set())
        self.assertNotIn("六见", slug)


if __name__ == "__main__":
    unittest.main()
