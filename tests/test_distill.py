#!/usr/bin/env python3
"""distill 单测：展示层蒸馏的规则边界（宁长勿缺）。"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from distill import rule_na  # noqa: E402


class TestRuleNa(unittest.TestCase):
    def test_short_line_untouched(self):
        self.assertEqual(rule_na("- 明天的事（09/12 之前）"), "- 明天的事（09/12 之前）")

    def test_dmv_real_case(self):
        long_line = ("加州 DMV 预约（线上约圣迭戈任一网点；约到 10 月也没关系，"
                     "先占位——瓶颈是预约，不是流程）——**中国打不开官网（9/5），"
                     "推迟到落地后 9/10–9/11 第一时间线上补约**")
        self.assertEqual(rule_na(long_line), "加州 DMV 预约")

    def test_dash_split_keeps_balanced_parens(self):
        line = ("预约 UCSD 精神科开药：MyStudentChart 里可约，或电话 CAPS（858 开头）"
                "——首诊等待 1–2 周，别再等邮件了，今晚 23 点后打，打不成次日接着打")
        na = rule_na(line)
        self.assertTrue(na.startswith("预约 UCSD 精神科开药"))
        self.assertIn("CAPS", na)
        self.assertNotIn("——", na)

    def test_no_clean_cut_keeps_original(self):
        line = "这是一条没有分隔符也没有括号但特别长的任务描述" * 4
        self.assertEqual(rule_na(line), line)

    def test_head_too_short_falls_back(self):
        line = "短：这是一个用来凑长度的说明部分，写了很多很多很多字，超过六十个字符的阈值了写了很多很多很多字"
        na = rule_na(line)
        self.assertTrue(len(na) <= 60 or na == line)


class TestStateItemFields(unittest.TestCase):
    """state._item 的稀疏 na/brief 字段（golden 对新字段是盲区，此处直测）。"""

    def test_short_item_has_no_na(self):
        from state import _item
        from parser import parse_markdown
        e = parse_markdown("- [ ] 短任务 9/12", file="a.md",
                           today=__import__("datetime").date(2026, 9, 11))[0]
        item = _item(e, e.dates[0])
        self.assertNotIn("na", item)
        self.assertNotIn("brief", item)

    def test_long_item_gets_na_and_brief(self):
        from state import _item
        from parser import parse_markdown
        raw = "- [ ] 加州 DMV 预约（线上约圣迭戈任一网点；约到 10 月也没关系，先占位——瓶颈是预约不是流程）——**中国打不开官网（9/5），推迟到落地后 9/10–9/11 第一时间线上补约**"
        e = parse_markdown(raw, file="a.md",
                           today=__import__("datetime").date(2026, 9, 11))[0]
        item = _item(e, e.dates[0], brief="dmv.md")
        self.assertEqual(item["na"], "加州 DMV 预约")
        self.assertEqual(item["brief"], "reports/briefs/dmv.md")
