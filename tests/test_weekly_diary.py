#!/usr/bin/env python3
"""周报节律区单测（v13）：日记天数/睡眠均值/断线警报；行为区进、感受区零泄漏
（T6 红线的代码级守卫——睡眠数字同样只许来自「做了什么」，感受区里的数字不作数）。"""

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


def write_diary(root: Path, day: dt.date, behavior: str = "", feelings: str = "") -> None:
    parts = [f"## {day.isoformat()}\n"]
    if behavior:
        parts.append(f"**做了什么**\n{behavior}\n")
    if feelings:
        parts.append(f"**感受**\n{feelings}\n")
    (root / "日记" / f"{day.isoformat()}.md").write_text(
        "\n".join(parts), encoding="utf-8")


def week_days() -> list[dt.date]:
    start = dt.date(2026, 9, 7)  # NOW 所在周的周一（W37）
    return [start + dt.timedelta(days=i) for i in range(7)]


class TestWeeklyRhythm(unittest.TestCase):
    def test_behavior_in_feelings_out(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "reports").mkdir()
            (root / "日记").mkdir()
            write_diary(root, week_days()[6],
                        behavior="- 写了 v6 计划\n- 修了 DMV\n- 睡眠 6h",
                        feelings="- 有点累但对系统满意\n")
            snap = Snapshot.load(root, CFG, NOW)
            body, _ = render_weekly(snap)
            self.assertIn("本周节律", body)
            self.assertIn("写了 v6 计划", body)
            self.assertIn("1/7", body)
            self.assertIn("6.0h", body)
            self.assertNotIn("有点累", body)   # 感受区零泄漏
            self.assertNotIn("满意", body)

    def test_no_diary_now_alarms_instead_of_silent(self):
        # W37 盲区教训：无日记时节区必须出现并报警，不再静默缺席
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "reports").mkdir()
            snap = Snapshot.load(root, CFG, NOW)
            body, _ = render_weekly(snap)
            self.assertIn("本周节律", body)
            self.assertIn("0/7", body)
            self.assertIn("⚠️ 断线警报", body)
            self.assertIn("睡眠均值 无数据", body)

    def test_sleep_numbers_only_from_behavior_zone(self):
        # 感受区里出现的睡眠数字不得进入统计（感受区代码级不解析）
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "reports").mkdir()
            (root / "日记").mkdir()
            write_diary(root, week_days()[3],
                        behavior="- 上课",
                        feelings="- 只睡了 5 小时，好累\n")
            snap = Snapshot.load(root, CFG, NOW)
            body, _ = render_weekly(snap)
            self.assertIn("睡眠均值 无数据", body)
            self.assertNotIn("5.0h", body)

    def test_partial_week_below_threshold_no_alarm(self):
        # 缺 2 天（写了 5 天）不触发 ≥3 天断线警报，但缺失日仍列出
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "reports").mkdir()
            (root / "日记").mkdir()
            days = week_days()
            write_diary(root, days[0], behavior="- 睡眠 7h")
            write_diary(root, days[1], behavior="- 睡眠 5h")
            write_diary(root, days[3], behavior="- 睡了 6 小时")
            write_diary(root, days[5], behavior="")          # 感受-only 也算写了
            write_diary(root, days[6], behavior="- 睡眠 8h")
            snap = Snapshot.load(root, CFG, NOW)
            body, _ = render_weekly(snap)
            self.assertIn("5/7", body)
            self.assertIn("缺 09/09、09/11", body)
            self.assertNotIn("⚠️", body)
            self.assertIn("**6.5h**（4 天有数据）", body)


if __name__ == "__main__":
    unittest.main()
