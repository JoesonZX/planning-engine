#!/usr/bin/env python3
"""画像层单测：手写区回填、红线守卫、行数上限（GLM 全 mock，不打真 API）。"""

from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from profile import (MAX_LINES, TEMPLATE, load_for_prompt,  # noqa: E402
                     split_regions, update_profile)
from report import _build_summary_prompt  # noqa: E402

NOW = datetime(2026, 9, 13, 20, 0, 0)

CFG = {
    "timezone": "America/Los_Angeles",
    "inbox_file": "inbox.md",
    "dashboard_file": "仪表盘.md",
    "model": "glm-4-flash",
    "monthly_budget_usd": 3.0,
}

OLD_PROFILE = """# profile · 用户画像（每周日随复盘更新）

<!-- user-start -->
- 物流模式：只谈任务日程（手写区）
- 建议给出具体时刻
<!-- user-end -->

## 当前重心（本季度）
- 科研 ≥15h/周（W35·执行清单）

## 修订记录
- 2026-09-06 W36: 初版
"""


class TestSplitRegions(unittest.TestCase):
    def test_with_markers(self):
        hand, agent = split_regions(OLD_PROFILE)
        self.assertIn("手写区", hand)
        self.assertNotIn("user-start", agent)
        self.assertIn("当前重心", agent)

    def test_without_markers(self):
        hand, agent = split_regions("# 裸文件\n- 条目\n")
        self.assertEqual(hand, "")
        self.assertIn("条目", agent)


class TestUpdateProfile(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "profile.md").write_text(OLD_PROFILE, encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def _revised(self, text: str):
        return mock.patch("profile._glm_revise", return_value=text)

    def test_happy_path_updates_and_keeps_handwritten(self):
        revised = "## 当前重心（本季度）\n- 科研 ≥15h/周（W37·滑落区）\n\n## 修订记录\n- 2026-09-13 W37: 更新\n"
        with self._revised(revised):
            self.assertTrue(update_profile(self.root, CFG, "key",
                                           self.root / "u.json", NOW, "周报"))
        body = (self.root / "profile.md").read_text(encoding="utf-8")
        self.assertIn("手写区", body)          # 手写区原样保留
        self.assertIn("W37", body)             # agent 区已更新
        self.assertNotIn("W36: 初版", body)

    def test_glm_cannot_touch_handwritten_region(self):
        # GLM 试图自带标记对重建手写区 → 标记行被剥离，原手写区逐字保留，
        # 全文有且只有一对标记
        revised = ("<!-- user-start -->\n- 恶意覆盖的手写区\n<!-- user-end -->\n\n"
                   "## 当前重心\n- 正常修订（W37）\n")
        with self._revised(revised):
            update_profile(self.root, CFG, "key", self.root / "u.json", NOW, "周报")
        body = (self.root / "profile.md").read_text(encoding="utf-8")
        self.assertIn("物流模式：只谈任务日程（手写区）", body)
        self.assertNotIn("恶意覆盖的手写区", body)
        self.assertEqual(body.count("<!-- user-start -->"), 1)
        self.assertEqual(body.count("<!-- user-end -->"), 1)

    def test_private_keyword_guard_keeps_last_version(self):
        revised = "## 常见滑落模式\n- 用户最近情绪波动较大（W37）\n"
        with self._revised(revised):
            self.assertFalse(update_profile(self.root, CFG, "key",
                                            self.root / "u.json", NOW, "周报"))
        self.assertEqual((self.root / "profile.md").read_text(encoding="utf-8"),
                         OLD_PROFILE)

    def test_line_limit_guard(self):
        revised = "\n".join(f"- 条目 {i}" for i in range(MAX_LINES + 10))
        with self._revised(revised):
            self.assertFalse(update_profile(self.root, CFG, "key",
                                            self.root / "u.json", NOW, "周报"))
        self.assertEqual((self.root / "profile.md").read_text(encoding="utf-8"),
                         OLD_PROFILE)

    def test_glm_failure_keeps_last_version(self):
        with self._revised(None):
            self.assertFalse(update_profile(self.root, CFG, "key",
                                            self.root / "u.json", NOW, "周报"))
        self.assertEqual((self.root / "profile.md").read_text(encoding="utf-8"),
                         OLD_PROFILE)

    def test_no_key_creates_nothing(self):
        (self.root / "profile.md").unlink()
        self.assertFalse(update_profile(self.root, CFG, None,
                                        self.root / "u.json", NOW, "周报"))
        self.assertFalse((self.root / "profile.md").exists())

    def test_first_creation_from_template(self):
        (self.root / "profile.md").unlink()
        revised = "## 当前重心（本季度）\n- 新条目（W37）\n"
        with self._revised(revised):
            self.assertTrue(update_profile(self.root, CFG, "key",
                                           self.root / "u.json", NOW, "周报"))
        body = (self.root / "profile.md").read_text(encoding="utf-8")
        self.assertIn("user-start", body)
        self.assertIn("新条目", body)


class TestPromptLoading(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_missing_file_returns_empty(self):
        self.assertEqual(load_for_prompt(self.root, CFG), "")

    def test_truncation(self):
        (self.root / "profile.md").write_text("画" * 9999, encoding="utf-8")
        self.assertEqual(len(load_for_prompt(self.root, CFG)), 1500)

    def test_summary_prompt_injects_profile(self):
        p = _build_summary_prompt("报告正文", "用户画像内容")
        self.assertIn("用户画像", p)
        self.assertIn("用户画像内容", p)
        self.assertIn("报告正文", p)

    def test_summary_prompt_without_profile(self):
        p = _build_summary_prompt("报告正文")
        self.assertNotIn("画像", p)
        self.assertIn("报告正文", p)


class TestTemplate(unittest.TestCase):
    def test_template_parses_and_within_limits(self):
        hand, agent = split_regions(TEMPLATE)
        self.assertIn("手写区", hand)
        self.assertLessEqual(TEMPLATE.count("\n") + 1, MAX_LINES)


if __name__ == "__main__":
    unittest.main()
