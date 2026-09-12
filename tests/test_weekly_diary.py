#!/usr/bin/env python3
"""周报日记行为摘要单测：行为区进、感受区零泄漏（T6 红线的代码级守卫）。"""

from __future__ import annotations

import datetime as dt
import sys
import tempfile
import unittest
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from parser import load_config  # noqa: E402
from vault import Snapshot  # noqa: E402
from weekly_review import render_weekly  # noqa: E402

NOW = dt.datetime(2026, 9, 13, 20, 0, tzinfo=ZoneInfo("America/Los_Angeles"))
CFG = {"timezone": "America/Los_Angeles", "horizon_days": 7, "stale_days": 14,
       "monthly_budget_usd": 3.0, "inbox_file": "inbox.md"}


class TestWeeklyDiary(unittest.TestCase):
    def test_behavior_in_feelings_out(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "reports").mkdir()
            (root / "日记").mkdir()
            sun = root / "日记" / "2026-09-13.md"
            body_text = (
                "## 2026-09-13 周日\n\n"
                "**做了什么**\n- 写了 v6 计划\n- 修了 DMV\n\n"
                "**感受**\n- 有点累但对系统满意\n"
            )
            sun.write_text(body_text, encoding="utf-8")
            snap = Snapshot.load(root, CFG, NOW)
            body, _ = render_weekly(snap)
            self.assertIn("写了 v6 计划", body)
            self.assertIn("本周日记", body)
            self.assertNotIn("有点累", body)   # 感受区零泄漏
            self.assertNotIn("满意", body)

    def test_no_diary_no_section(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "reports").mkdir()
            snap = Snapshot.load(root, CFG, NOW)
            body, _ = render_weekly(snap)
            self.assertNotIn("本周日记", body)


if __name__ == "__main__":
    unittest.main()
