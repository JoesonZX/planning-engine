#!/usr/bin/env python3
"""state/stats 生成器：给前端任务中心视图的结构化数据（Q4a：解析只在 Python）。

state.json  = 今天/未来7天/滑落 三组结构化条目 + 日常时间线模板
stats.json  = 近 84 天每日 {非 bot 提交数, 新勾选数}（热力图数据源）
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import subprocess
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

from parser import load_config, load_vault
from report import collect_stale, file_last_commit_days


def _item(e, today: dt.date) -> dict:
    return {
        "t": e.text,
        "s": e.star,
        "d": e.done,
        "f": e.file,
        "l": e.line,
        "dates": [d.isoformat() for d in e.dates],
    }


def build_state(root: Path, cfg: dict, now: dt.datetime) -> dict:
    today = now.date()
    horizon_end = today + dt.timedelta(days=int(cfg["horizon_days"]))
    entries = load_vault(root, cfg, today=today)
    excluded = {cfg.get("inbox_file", "inbox.md"),
                cfg.get("dashboard_file", "仪表盘.md")}
    entries = [e for e in entries if e.file not in excluded]
    file_ages = file_last_commit_days(root)

    today_items, week, stale_src = [], {}, []
    seen_week: set[str] = set()
    for e in entries:
        if today in e.dates:
            today_items.append(_item(e, today))
        future = sorted(d for d in e.dates if today < d <= horizon_end)
        if future:
            d = future[0]
            if (e.file, e.line) not in seen_week:
                seen_week.add((e.file, e.line))
                week.setdefault(d.isoformat(), []).append(_item(e, today))
        if e.done is False:
            latest = max(e.dates) if e.dates else None
            if (latest and (today - latest).days >= int(cfg["stale_days"])) or \
               (not latest and (file_ages.get(e.file) or 0) >= int(cfg["stale_days"])):
                stale_src.append(e)

    stale = sorted(stale_src,
                   key=lambda e: -((today - max(e.dates)).days if e.dates
                                   else file_ages.get(e.file, 0)))
    # timeline 归一化：config 里每行是 '- ["08:30", "标签"]' 字符串 → 解析成 [time, label]
    timeline = []
    for row in cfg.get("timeline", []):
        if isinstance(row, str):
            try:
                row = json.loads(row)
            except (json.JSONDecodeError, ValueError):
                continue
        if isinstance(row, (list, tuple)) and len(row) == 2:
            timeline.append([str(row[0]), str(row[1])])
    return {
        "generated_at": now.isoformat(timespec="seconds"),
        "today": today.isoformat(),
        "timeline": timeline,
        "today_items": sorted(today_items, key=lambda x: (not x["s"],)),
        "week": [{"date": d, "items": week[d]} for d in sorted(week)],
        "stale": [_item(e, today) for e in stale[:12]],
    }


def build_stats(root: Path, cfg: dict, now: dt.datetime, days: int = 84) -> dict:
    """近 N 天每日：非 bot 提交数（c）与新增勾选数（x）。"""
    since = (now - dt.timedelta(days=days)).strftime("%Y-%m-%d")
    bot_names = {"planning-bot", "planning-bot@users.noreply.github.com"}

    def run(*args):
        try:
            return subprocess.run(
                ["git", "-C", str(root), "-c", "core.quotepath=false", *args],
                capture_output=True, text=True, encoding="utf-8",
                errors="replace", timeout=60, check=True).stdout
        except (subprocess.SubprocessError, OSError):
            return ""

    counts: dict[str, dict] = {}

    # 提交数（排除 bot）
    log = run("log", f"--since={since}", "--date=short", "--pretty=format:%ad|%an|%ae")
    for line in log.splitlines():
        parts = line.split("|")
        if len(parts) != 3:
            continue
        d, an, ae = parts
        if an in bot_names or ae in bot_names:
            continue
        counts.setdefault(d, {"c": 0, "x": 0})["c"] += 1

    # 勾选数：diff 新增 - [x]
    plog = run("log", "-p", "--unified=0", f"--since={since}",
               "--date=short", "--pretty=format:@@@%ad", "--", "*.md")
    cur = None
    for line in plog.splitlines():
        if line.startswith("@@@"):
            cur = line[3:].strip()[:10]
        elif line.startswith("+") and not line.startswith("+++") and cur:
            if "- [x]" in line or "- [X]" in line:
                counts.setdefault(cur, {"c": 0, "x": 0})["x"] += 1

    # 补齐空日
    out = {}
    for i in range(days):
        d = (now - dt.timedelta(days=i)).strftime("%Y-%m-%d")
        out[d] = counts.get(d, {"c": 0, "x": 0})
    return {"generated_at": now.isoformat(timespec="seconds"), "days": out}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--vault", required=True)
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()
    root = Path(args.vault).resolve()
    cfg = load_config(root)
    now = dt.datetime.now(ZoneInfo(cfg["timezone"]))

    state = build_state(root, cfg, now)
    stats = build_stats(root, cfg, now)
    (root / "reports" / "state.json").write_text(
        json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
    (root / "reports" / "stats.json").write_text(
        json.dumps(stats, ensure_ascii=False), encoding="utf-8")
    print(f"[ok] state.json ({len(state['today_items'])} today, "
          f"{len(state['week'])} days, {len(state['stale'])} stale) + stats.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
