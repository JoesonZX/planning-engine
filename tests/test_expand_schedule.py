#!/usr/bin/env python3
"""expand_schedule 单测：日程 blob 行逐日展开（v8 V4）。"""
from __future__ import annotations
import sys, unittest
from datetime import date
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from parser import expand_schedule

TODAY = date(2026, 9, 12)

class TestExpand(unittest.TestCase):
    def test_frame_line_expands(self):
        line = "日程框架（9/7 定）：SD 9.11-9.14 ｜ Joshua Tree 9.15 ｜ LA 9.16-9.17 ｜ 9.18 Orientation"
        days = expand_schedule(line, TODAY)
        by_day = {d: s for d, s in days}
        self.assertEqual(len(days), 8)  # 11,12,13,14,15,16,17,18
        self.assertIn("SD", by_day[date(2026, 9, 12)])
        self.assertIn("Joshua Tree", by_day[date(2026, 9, 15)])
        self.assertIn("Orientation", by_day[date(2026, 9, 18)])

    def test_far_dates_not_stretched(self):
        line = "9.11 买票 ｜ 12.30 跨年"
        days = expand_schedule(line, TODAY)
        self.assertEqual(sorted(d for d, _ in days), [date(2026,9,11), date(2026,12,30)])

    def test_table_row_returns_empty(self):
        self.assertEqual(expand_schedule("| 9.11 五 | 玩 |", TODAY), [])

    def test_no_date_segments_dropped(self):
        line = "随便写写 ｜ 9.15 Joshua Tree"
        days = expand_schedule(line, TODAY)
        self.assertEqual([d for d, _ in days], [date(2026, 9, 15)])

if __name__ == "__main__":
    unittest.main()
