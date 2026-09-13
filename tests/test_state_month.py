#!/usr/bin/env python3
"""v10 月窗：state.month = 自然月全量（过去天只含未完成）+ 跨多日任务/备注抽离 spans。

日程（表格/展开段）不进 spans——按天呈现；无日期任务不进月视图；week/today 零改动。
"""

from __future__ import annotations

import datetime as dt
import tempfile
import unittest
from pathlib import Path

from state import render_state
from vault import Snapshot

PT = dt.timezone(dt.timedelta(hours=-7))
NOW = dt.datetime(2026, 9, 13, 9, 0, tzinfo=PT)  # 周日，月中


def _state(body: str) -> dict:
    root = Path(tempfile.mkdtemp(prefix="month-vault-"))
    (root / "main.md").write_text(body, encoding="utf-8")
    return render_state(Snapshot.load(root, now=NOW))


class MonthMetaTests(unittest.TestCase):
    def test_meta_and_empty(self):
        st = _state("# 主\n- [ ] 无日期任务\n")
        m = st["month"]
        self.assertEqual(m["label"], "2026年9月")
        self.assertEqual(m["start"], "2026-09-01")
        self.assertEqual(m["end"], "2026-09-30")
        self.assertEqual(m["days"], {})
        self.assertEqual(m["spans"], [])

    def test_week_untouched(self):
        st = _state("- [ ] 未来任务 9/15\n")
        self.assertEqual([g["date"] for g in st["week"]], ["2026-09-15"])  # week 原样


class MonthDaysTests(unittest.TestCase):
    def test_past_only_open(self):
        st = _state("- [x] 完成了 9/5\n- [ ] 没做 9/6\n")
        self.assertNotIn("2026-09-05", st["month"]["days"])  # 过去已完成不显示
        day = st["month"]["days"]["2026-09-06"]
        self.assertEqual(len(day["items"]), 1)
        self.assertIn("没做", day["items"][0]["t"])

    def test_future_and_today_include_done(self):
        st = _state("- [x] 已勾 9/15\n- [ ] 未勾 9/15\n- [ ] 今天的事 9/13\n")
        self.assertEqual(len(st["month"]["days"]["2026-09-15"]["items"]), 2)
        self.assertEqual(len(st["month"]["days"]["2026-09-13"]["items"]), 1)

    def test_undated_not_in_month(self):
        st = _state("- [ ] 无日期悬空\n")
        self.assertEqual(st["month"]["days"], {})

    def test_out_of_month_ignored(self):
        st = _state("- [ ] 十月的事 10/15\n- [ ] 八月的事 8/20\n")
        self.assertEqual(st["month"]["days"], {})


class SpanTests(unittest.TestCase):
    def test_multi_date_task_to_spans(self):
        # 「B 9/17」这类 ASCII 字母邻接会被版本号防御拒——真实数据用括注/范围形态
        st = _state("- [ ] 酒店：A 店（9/14）、B 店（9/17）\n")
        self.assertEqual(len(st["month"]["spans"]), 1)
        self.assertEqual(st["month"]["spans"][0]["k"], "task")
        self.assertEqual(st["month"]["spans"][0]["dates"], ["2026-09-14", "2026-09-17"])
        self.assertNotIn("2026-09-14", st["month"]["days"])  # 不再入单日组

    def test_multi_date_misc_to_spans(self):
        st = _state("**9/18 Orientation**（带问题）；**9/23 缴费截止**\n")
        self.assertEqual(len(st["month"]["spans"]), 1)
        self.assertEqual(st["month"]["spans"][0]["k"], "misc")

    def test_single_date_task_not_span(self):
        st = _state("- [ ] 单点任务 9/20\n")
        self.assertEqual(st["month"]["spans"], [])
        self.assertEqual(len(st["month"]["days"]["2026-09-20"]["items"]), 1)

    def test_schedule_never_span(self):
        st = _state("> 9.14 SD 行程 ｜ 9.15 JT 行程\n\n| 9.16 三 | 表格行程 |\n")
        self.assertEqual(st["month"]["spans"], [])
        self.assertEqual(len(st["month"]["days"]["2026-09-14"]["sched"]), 1)
        self.assertEqual(len(st["month"]["days"]["2026-09-15"]["sched"]), 1)
        self.assertEqual(len(st["month"]["days"]["2026-09-16"]["sched"]), 1)

    def test_span_sorted_by_first_date(self):
        st = _state("- [ ] 后写的 9/20、9/25\n- [ ] 先到的 9/14、9/15\n")
        spans = st["month"]["spans"]
        self.assertEqual([s["dates"][0] for s in spans], ["2026-09-14", "2026-09-20"])

    def test_multi_date_out_of_month_counts_window_only(self):
        # 月内只 1 个日期 → 不抽（另一日期在月外）
        st = _state("- [ ] 跨月的事 9/28、10/15\n")
        self.assertEqual(st["month"]["spans"], [])
        self.assertEqual(len(st["month"]["days"]["2026-09-28"]["items"]), 1)


if __name__ == "__main__":
    unittest.main()
