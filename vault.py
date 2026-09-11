#!/usr/bin/env python3
"""Vault 快照层：一次加载，全部产出物共享（管线的公共前半段）。

Snapshot 把「读 vault + 读 git 历史」的所有中间量收敛为一个惰性对象——
同一次运行里 report/state/ics/weekly 不再各自解析全库、各自跑全史 git log。
新产出物 = 一个吃 Snapshot 的 render 函数，不再复制加载与排除逻辑。
"""

from __future__ import annotations

import datetime as dt
import json
import subprocess
from functools import cached_property
from pathlib import Path
from zoneinfo import ZoneInfo

from parser import generated_files, load_config, load_vault

BOT_NAMES = {"planning-bot", "planning-bot@users.noreply.github.com"}


# ---------------------------------------------------------------- git 辅助

def _git(root: Path, *args: str) -> str:
    try:
        return subprocess.run(
            ["git", "-C", str(root), "-c", "core.quotepath=false", *args],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=60, check=True,
        ).stdout
    except (subprocess.SubprocessError, FileNotFoundError, OSError):
        return ""


def file_last_commit_days(root: Path) -> dict[str, float]:
    """{相对路径: 距今最后一次提交的天数}。"""
    out: dict[str, float] = {}
    log = _git(root, "log", "--name-only", "--pretty=format:%ct")
    now = dt.datetime.now().timestamp()
    current_ts: float | None = None
    for line in log.splitlines():
        line = line.strip()
        if not line:
            current_ts = None
            continue
        if line.isdigit():
            current_ts = float(line)
        elif current_ts is not None:
            age = max(0.0, (now - current_ts) / 86400)
            out[line] = max(out.get(line, 0.0), age)
    return out


def added_done_checkboxes(root: Path, hours: int = 24) -> list[str]:
    """最近 N 小时 diff 里新增的已勾选行文本。"""
    log = _git(root, "log", "-p", "--unified=0",
               f"--since={hours} hours ago", "--", "*.md")
    done_lines: list[str] = []
    for line in log.splitlines():
        if line.startswith("+") and ("- [x]" in line or "- [X]" in line):
            text = line.lstrip("+").strip()
            for token in ("- [x]", "- [X]"):
                text = text.replace(token, "", 1)
            done_lines.append(text.strip())
    return done_lines


def changed_files(root: Path, days: int = 7,
                  exclude_prefixes: tuple[str, ...] = ("reports/", ".github/")) -> list[str]:
    log = _git(root, "log", f"--since={days} days ago",
               "--name-only", "--pretty=format:")
    seen: dict[str, None] = {}
    for line in log.splitlines():
        line = line.strip()
        if not line or line.startswith(exclude_prefixes):
            continue
        seen.setdefault(line, None)
    return list(seen)


def vault_quiet(root: Path) -> bool:
    """24h 内无非 bot 提交 → vault 无变化，跳过 GLM 摘要（省钱省幂）。

    bot 判定用名字/邮箱精确匹配（与统计同口径）。
    git 查询失败 → 返回 False（无法判断时保守生成摘要，宁可多花不漏摘要）。
    （v3 上线时比较逻辑写反：活跃日被当安静日跳过摘要——v4 端到端测试抓出）
    """
    try:
        proc = subprocess.run(
            ["git", "-C", str(root), "log", "--since=24 hours ago",
             "--pretty=format:%an|%ae"],
            capture_output=True, text=True, timeout=30, check=True)
    except (subprocess.SubprocessError, OSError):
        return False

    def _is_bot(line: str) -> bool:
        name, _, email = line.partition("|")
        return name in BOT_NAMES or email in BOT_NAMES

    return all(_is_bot(line) for line in proc.stdout.splitlines() if line)


# ---------------------------------------------------------------- 快照

class Snapshot:
    """一次运行的全部只读上下文。惰性求值：每个产物只算一次。"""

    def __init__(self, root: Path, cfg: dict, now: dt.datetime):
        self.root = root
        self.cfg = cfg
        self.now = now
        self.today = now.date()

    @classmethod
    def load(cls, root: Path, cfg: dict | None = None,
             now: dt.datetime | None = None) -> "Snapshot":
        root = Path(root)
        cfg = cfg or load_config(root)
        now = now or dt.datetime.now(ZoneInfo(cfg["timezone"]))
        return cls(root, cfg, now)

    @cached_property
    def entries(self) -> list:
        """全库解析结果（已剔除 inbox/仪表盘/画像等生成物）。"""
        entries = load_vault(self.root, self.cfg, today=self.today)
        excluded = generated_files(self.cfg)
        return [e for e in entries if e.file not in excluded]

    @cached_property
    def file_ages(self) -> dict[str, float]:
        return file_last_commit_days(self.root)

    @cached_property
    def inbox_lines(self) -> list[str]:
        path = self.root / self.cfg.get("inbox_file", "inbox.md")
        if not path.exists():
            return []
        return [s for raw in path.read_text(encoding="utf-8").splitlines()
                if (s := raw.strip()) and not s.startswith("#")]

    @cached_property
    def done_24h(self) -> list[str]:
        return added_done_checkboxes(self.root, 24)

    @cached_property
    def changed_7d(self) -> list[str]:
        return changed_files(self.root, 7)

    @cached_property
    def timeline(self) -> list[list[str]]:
        """config 里的日常模板，归一化为 [time, label] 对。"""
        rows = []
        for row in self.cfg.get("timeline", []):
            if isinstance(row, str):
                try:
                    row = json.loads(row)
                except (json.JSONDecodeError, ValueError):
                    continue
            if isinstance(row, (list, tuple)) and len(row) == 2:
                rows.append([str(row[0]), str(row[1])])
        return rows

    @cached_property
    def stale_days(self) -> int:
        return int(self.cfg.get("stale_days", 14))
