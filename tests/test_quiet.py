#!/usr/bin/env python3
"""vault_quiet 单测：安静日判定（v3 反转 bug 的回归守卫）。

定义：24h 内每一条提交都是 bot（或完全没有提交）= 安静；
出现任何非 bot 提交 = 活跃，摘要必须生成。
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from report import vault_quiet  # noqa: E402


def _git(root: Path, *args: str, env_extra: dict | None = None) -> None:
    import os
    env = dict(os.environ)
    env.update({"GIT_AUTHOR_DATE": "2026-09-11T10:00:00 -0700",
                "GIT_COMMITTER_DATE": "2026-09-11T10:00:00 -0700"})
    if env_extra:
        env.update(env_extra)
    subprocess.run(["git", "-C", str(root), *args], check=True,
                   capture_output=True, env=env)


class TestVaultQuiet(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        _git(self.root, "init", "-q", "-b", "main")
        _git(self.root, "config", "user.name", "someone")
        _git(self.root, "config", "user.email", "someone@example.com")
        (self.root / "a.md").write_text("- [ ] x\n", encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def _commit_as(self, name: str, email: str):
        _git(self.root, "add", "-A")
        _git(self.root, "-c", f"user.name={name}", "-c", f"user.email={email}",
             "commit", "-q", "-m", "test commit")

    def test_no_commits_is_quiet(self):
        self.assertTrue(vault_quiet(self.root))

    def test_bot_only_commits_are_quiet(self):
        self._commit_as("planning-bot", "planning-bot@users.noreply.github.com")
        self.assertTrue(vault_quiet(self.root))

    def test_user_commit_is_not_quiet(self):
        self._commit_as("planning-bot", "planning-bot@users.noreply.github.com")
        self._commit_as("Xuan Zhou", "zhou2073@umn.edu")
        self.assertFalse(vault_quiet(self.root),  # 活跃日必须生成摘要
                         "非 bot 提交存在时 quiet 必须为 False（v3 反转 bug 回归）")

    def test_user_email_bot_name_counts_as_user(self):
        self._commit_as("someone", "planning-bot@users.noreply.github.com")
        self.assertFalse(vault_quiet(self.root))


if __name__ == "__main__":
    unittest.main()
