#!/usr/bin/env python3
"""v12：晚报「各项目状态」节（month_plan_file）+ 去重主来源优先「月计划」文件。

内容纪律：月计划里「状态：」行不得含行内日期（含日期会被解析成 misc 条目）——
解析层对此不做防御（冻结语法最小化），由测试钉住正确写法的行为。
"""

from __future__ import annotations

import datetime as dt
import tempfile
import unittest
from pathlib import Path

from parser import dedup_entries, load_config, parse_markdown
from report import render_report
from vault import Snapshot

PT = dt.timezone(dt.timedelta(hours=-7))
NOW = dt.datetime(2026, 9, 13, 9, 0, tzinfo=PT)

PLAN = """# 9月计划

## 选课与注册

状态：TB 已抽血等 clearance。

- [ ] ⭐9/20 查 TB 结果

## 归档（已完成与已决）

状态：不应出现在报告里。
"""


def _vault(files: dict[str, str], cfg_lines: str = "") -> Path:
    root = Path(tempfile.mkdtemp(prefix="v12-report-"))
    for rel, body in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body, encoding="utf-8")
    if cfg_lines:
        (root / ".agent-config.yml").write_text(cfg_lines, encoding="utf-8")
    return root


class MonthPlanStatusTests(unittest.TestCase):
    def test_status_section_rendered_when_configured(self):
        root = _vault({"规划/26fall 9月计划.md": PLAN},
                      "month_plan_file: 规划/26fall 9月计划.md\n")
        snap = Snapshot.load(root, load_config(root), NOW)
        out = render_report(snap)
        self.assertIn("## 各项目状态（月计划）", out)
        self.assertIn("- **选课与注册**：TB 已抽血等 clearance。", out)

    def test_status_section_absent_without_config(self):
        root = _vault({"规划/26fall 9月计划.md": PLAN})
        snap = Snapshot.load(root, load_config(root), NOW)
        self.assertNotIn("各项目状态", render_report(snap))

    def test_archived_group_status_excluded(self):
        root = _vault({"规划/p.md": PLAN}, "month_plan_file: 规划/p.md\n")
        snap = Snapshot.load(root, load_config(root), NOW)
        self.assertNotIn("不应出现", render_report(snap))

    def test_missing_plan_file_is_silent(self):
        root = _vault({}, "month_plan_file: 规划/不存在.md\n")
        snap = Snapshot.load(root, load_config(root), NOW)
        self.assertNotIn("各项目状态", render_report(snap))


class StatusLineDisciplineTests(unittest.TestCase):
    def test_date_free_status_line_produces_no_entry(self):
        entries = parse_markdown("状态：TB 已抽血等 clearance。\n", file="p.md",
                                 today=NOW.date())
        self.assertEqual(entries, [])

    def test_dated_status_line_would_leak_to_misc(self):
        """反向钉子：状态行里写裸日期会变成 misc 条目——内容纪律的失败模式。
        （注：ASCII 结尾词后的日期如「TB 9/10」会被版本号防御拒掉，是另一码事。）"""
        entries = parse_markdown("状态：已约号，9/10 做。\n", file="p.md", today=NOW.date())
        self.assertEqual(len(entries), 1)
        self.assertIsNone(entries[0].done)


class DedupPriorityTests(unittest.TestCase):
    def _entry(self, text, file):
        return parse_markdown(f"- [ ] {text}\n", file=file, today=NOW.date())[0]

    def test_month_plan_wins_primary_source(self):
        es = [self._entry("缴费", "规划/26fall 9月计划.md"),
              self._entry("缴费", "规划/攻略.md")]
        out = dedup_entries(es)
        self.assertEqual(len(out), 1)
        self.assertIn("月计划", out[0].file)
        self.assertEqual(len(out[0].src), 2)

    def test_legacy_checklist_still_pri(self):
        es = [self._entry("缴费", "规划/攻略.md"),
              self._entry("缴费", "规划/26fall 9月执行清单.md")]
        out = dedup_entries(es)
        self.assertIn("执行清单", out[0].file)


if __name__ == "__main__":
    unittest.main()
