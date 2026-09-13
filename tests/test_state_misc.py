#!/usr/bin/env python3
"""v9 misc 三分：带日期、非 checkbox、不满足日程资格的行进 misc——
「其他带日期」折叠区的数据源（修复隐形行：此前任何视图不出现、无法删除）。"""

from __future__ import annotations

import datetime as dt
import tempfile
import unittest
from pathlib import Path

from state import render_state
from vault import Snapshot

PT = dt.timezone(dt.timedelta(hours=-7))
NOW = dt.datetime(2026, 9, 13, 9, 0, tzinfo=PT)


def _state(body: str) -> dict:
    root = Path(tempfile.mkdtemp(prefix="misc-vault-"))
    (root / "main.md").write_text(body, encoding="utf-8")
    return render_state(Snapshot.load(root, now=NOW))


class MiscClassifyTests(unittest.TestCase):
    def test_bold_dated_line_is_misc(self):
        st = _state("# 主\n\n**9/18 Orientation**（带问题）；**9/23 缴费截止**\n")
        self.assertEqual(st["misc_today"], [])
        day = next(g for g in st["week"] if g["date"] == "2026-09-18")
        self.assertEqual(len(day["misc"]), 1)
        self.assertIn("Orientation", day["misc"][0]["t"])
        # 行内更远的日期（9/23）不再参与归因（首个未来日期归因）
        self.assertFalse(any(g["misc"] for g in st["week"] if g["date"] == "2026-09-23"))

    def test_misc_today(self):
        st = _state("- ⚠️ 当天杂事：**预约网络安装**（9/13 要排期）\n")
        self.assertEqual(len(st["misc_today"]), 1)
        self.assertIn("网络安装", st["misc_today"][0]["t"])

    def test_numbered_line_is_misc(self):
        st = _state("2. **9/14 问教授**（带一页纸）\n")
        day = next(g for g in st["week"] if g["date"] == "2026-09-14")
        self.assertEqual(len(day["misc"]), 1)

    def test_first_future_date_attribution(self):
        st = _state("**9/20 事项甲**；**9/25 事项乙**\n")
        days_with_misc = [g["date"] for g in st["week"] if g["misc"]]
        self.assertEqual(days_with_misc, ["2026-09-20"])  # 不逐日展开：备注不是日程

    def test_bullet_dated_line_is_misc(self):
        # v8 把子弹备注清出日程；v9 起进 misc 而非继续隐形
        st = _state("- ~~Channel Islands~~ 已取消（父母 9/18 航班）\n")
        day = next(g for g in st["week"] if g["date"] == "2026-09-18")
        self.assertEqual(len(day["misc"]), 1)
        self.assertEqual(day["sched"], [])


class MiscBoundaryTests(unittest.TestCase):
    def test_table_quote_datelead_stay_schedule(self):
        st = _state("| 9.14 一 | 行程甲 |\n\n> 9/15 引用行\n\n9/16 日期开头行\n")
        sched_days = {g["date"] for g in st["week"] if g["sched"]}
        self.assertEqual(sched_days, {"2026-09-14", "2026-09-15", "2026-09-16"})
        self.assertFalse(any(g["misc"] for g in st["week"]))

    def test_checkbox_never_misc(self):
        st = _state("- [ ] 普通任务 **9/14**\n- [x] 已完成 9/14\n")
        day = next(g for g in st["week"] if g["date"] == "2026-09-14")
        self.assertEqual(len(day["items"]), 2)  # 未完成+已完成任务都进 items（既有行为）
        self.assertFalse(any(g["misc"] for g in st["week"]))
        self.assertEqual(st["misc_today"], [])

    def test_undated_lines_never_misc(self):
        st = _state("**纯加粗无日期**\n普通段落 3.12 与 v1.29\n")
        self.assertEqual(st["misc_today"], [])
        self.assertEqual(st["week"], [])

    def test_multiline_emoji_safe(self):
        st = _state("- ⚠️ 杂事：**9/17 预约公寓网络**（Spectrum/Cox）\n")
        day = next(g for g in st["week"] if g["date"] == "2026-09-17")
        self.assertEqual(len(day["misc"]), 1)

    def test_star_only_no_date_not_misc(self):
        st = _state("- ⭐ 无日期硬节点\n")
        self.assertEqual(st["misc_today"], [])
        self.assertEqual(st["week"], [])


if __name__ == "__main__":
    unittest.main()
