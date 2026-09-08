#!/usr/bin/env python3
"""parser 单测：日期防御规则与 checkbox/⭐ 解析（冻结语法的守卫）。"""

from __future__ import annotations

import sys
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from parser import extract_dates, parse_markdown, pick_year  # noqa: E402

TODAY = date(2026, 9, 8)


def d(month: int, day: int, year: int = 2026) -> date:
    return date(year, month, day)


class TestExtractDates(unittest.TestCase):
    def test_basic_forms(self):
        self.assertEqual(extract_dates("9/24 开学", TODAY), [d(9, 24)])
        self.assertEqual(extract_dates("9.24 开学", TODAY), [d(9, 24)])
        self.assertEqual(extract_dates("10/9 waitlist 截止", TODAY), [d(10, 9)])
        self.assertEqual(extract_dates("12/30 缴费", TODAY), [d(12, 30)])

    def test_range_both_ends(self):
        self.assertEqual(extract_dates("阶段二（9.9–9.17）", TODAY),
                         [d(9, 9), d(9, 17)])

    def test_reject_decimals_and_versions(self):
        self.assertEqual(extract_dates("约 2.5–3h 车程", TODAY), [])
        self.assertEqual(extract_dates("账单 ≈$7.7k", TODAY), [])
        self.assertEqual(extract_dates("效应量 d≈0.65", TODAY), [])
        self.assertEqual(extract_dates("winget v1.29.290", TODAY), [])
        self.assertEqual(extract_dates("arXiv 2401.05459", TODAY), [])
        self.assertEqual(extract_dates("模型 GLM-5.3-Flash", TODAY), [])
        self.assertEqual(extract_dates("Python 3.12 安装", TODAY), [])

    def test_reject_times(self):
        self.assertEqual(extract_dates("8:30–9:00 抽血", TODAY), [])
        self.assertEqual(extract_dates("17:00 落地 LAX", TODAY), [])

    def test_weekday_and_star_attached(self):
        self.assertEqual(extract_dates("9.10 四 ⭐", TODAY), [d(9, 10)])
        self.assertEqual(extract_dates("9.9三✈️", TODAY), [d(9, 9)])


class TestPickYear(unittest.TestCase):
    def test_rolling_year(self):
        self.assertEqual(pick_year(9, 24, TODAY), d(9, 24))
        self.assertEqual(pick_year(12, 30, TODAY), d(12, 30))
        # 1 月的日期在 9 月看来是明年
        self.assertEqual(pick_year(1, 15, TODAY), d(1, 15, 2027))
        # 8 月的日期刚过去不久 → 解析为最近过去的那个（overdue 检测用）
        self.assertEqual(pick_year(8, 20, TODAY), d(8, 20))
        # 12 月的文件在 1 月读：滚动为去年 12 月
        jan = date(2027, 1, 5)
        self.assertEqual(pick_year(12, 30, jan), d(12, 30, 2026))


class TestParseMarkdown(unittest.TestCase):
    def test_checkboxes_and_star(self):
        md = """# 清单

- [ ] ⭐预约精神科 9/22 之后的号
- [x] 登录测试 9/7
- 普通列表行 9/9
- [ ] 无日期无星任务
"""
        entries = parse_markdown(md, file="t.md", today=TODAY)
        tasks = [e for e in entries if e.done is not None]
        self.assertEqual(len(tasks), 3)
        star_task = tasks[0]
        self.assertFalse(star_task.done)
        self.assertTrue(star_task.star)
        self.assertEqual(star_task.dates, [d(9, 22)])
        self.assertTrue(tasks[1].done)
        # 普通行因带日期也被收录（用于死线扫描）
        plain = [e for e in entries if e.done is None]
        self.assertEqual(len(plain), 1)
        self.assertEqual(plain[0].dates, [d(9, 9)])

    def test_heading_context(self):
        md = """## 阶段三

- [ ] 缴学费 9/23
"""
        entries = parse_markdown(md, file="t.md", today=TODAY)
        self.assertEqual(entries[0].heading, "阶段三")

    def test_table_rows_kept(self):
        md = "| 9.10 四 ⭐ | 抽血 |"
        entries = parse_markdown(md, file="t.md", today=TODAY)
        self.assertEqual(len(entries), 1)
        self.assertTrue(entries[0].star)
        self.assertEqual(entries[0].dates, [d(9, 10)])


if __name__ == "__main__":
    unittest.main()
