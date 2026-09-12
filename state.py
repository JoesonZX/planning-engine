#!/usr/bin/env python3
"""state/stats 生成器：给前端任务中心视图的结构化数据（Q4a：解析只在 Python）。

state.json  = 今天/未来7天/滑落 三组结构化条目 + 日常时间线模板
stats.json  = 近 84 天每日 {非 bot 提交数, 新勾选数}（热力图数据源）
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
from pathlib import Path
from zoneinfo import ZoneInfo

import re

from parser import dedup_entries, expand_schedule, load_config

# 日程资格：表格行 / 引用行 / 日期开头行 / 显式全角竖线分段行。
# 备注子弹（- 开头）、标题加粗（** 开头）是笔记，不算日程（v8 噪音过滤）
SCHED_LEAD = re.compile(r"^(?:[|>]|\d{1,2}[./]\d{1,2}(?!\d))")
from vault import Snapshot
import briefs
from distill import rule_na


def _item(e, today: dt.date, brief: str | None = None) -> dict:
    item = {
        "t": e.text,
        "s": e.star,
        "d": e.done,
        "f": e.file,
        "l": e.line,
        "dates": [d.isoformat() for d in e.dates],
    }
    na = rule_na(e.text)
    if na != e.text:
        item["na"] = na  # 稀疏字段：仅长行蒸馏时出现
    if brief:
        item["brief"] = f"{briefs.BRIEFS_DIR}/{brief}"
    if len(getattr(e, "src", [])) > 1:
        item["src"] = [list(x) for x in e.src]
    return item


def render_state(snap: Snapshot) -> dict:
    today, cfg = snap.today, snap.cfg
    horizon_end = today + dt.timedelta(days=int(cfg["horizon_days"]))
    # 任务（checkbox，可勾可统计）与日程（表格/普通日期行，只按日展示）彻底分离（v7）
    all_entries = dedup_entries(snap.entries)
    entries = [e for e in all_entries if e.done is not None]          # 任务
    schedule = [e for e in all_entries if e.done is None and e.dates
                and (SCHED_LEAD.search(e.raw.lstrip()) or "｜" in e.raw)]  # 日程
    file_ages = snap.file_ages
    index = briefs.load_index(snap.root)

    def _brief_of(e) -> str | None:
        meta = index.get(briefs.task_key(e.file, e.raw))
        return meta.get("file") if meta else None

    today_items, week, stale_src = [], {}, []
    seen_week: set[str] = set()
    sched_today = sorted([e for e in schedule if today in e.dates],
                         key=lambda e: e.dates)
    sched_week: dict[str, list] = {}
    for e in schedule:
        # v8：日程 blob 行（「SD 9.11–9.14 ｜ Joshua Tree 9.15 ｜ …」）逐日展开；
        # 表格行与普通行退回「首个未来日期」原行为
        expanded = expand_schedule(e.raw, today) if not e.raw.lstrip().startswith("|") else []
        if len(expanded) >= 1:
            for d, seg in expanded:
                if today <= d <= horizon_end:
                    it = _item(e, today)
                    it["t"] = seg
                    it["seg"] = True
                    sched_week.setdefault(d.isoformat(), []).append(it)
            continue
        future = sorted(d for d in e.dates if today < d <= horizon_end)
        if future:
            sched_week.setdefault(future[0].isoformat(), []).append(_item(e, today))
    for e in entries:
        b = _brief_of(e)
        if today in e.dates:
            today_items.append(_item(e, today, b))
        future = sorted(d for d in e.dates if today < d <= horizon_end)
        if future:
            d = future[0]
            if (e.file, e.line) not in seen_week:
                seen_week.add((e.file, e.line))
                week.setdefault(d.isoformat(), []).append(_item(e, today, b))
        if e.done is False:
            latest = max(e.dates) if e.dates else None
            if (latest and (today - latest).days >= snap.stale_days) or \
               (not latest and (file_ages.get(e.file) or 0) >= snap.stale_days):
                stale_src.append(e)

    stale = sorted(stale_src,
                   key=lambda e: -((today - max(e.dates)).days if e.dates
                                   else file_ages.get(e.file, 0)))
    week_out = []
    for d in sorted(set(week) | set(sched_week)):
        week_out.append({
            "date": d,
            "items": week.get(d, []),
            "sched": sched_week.get(d, []),
        })
    return {
        "generated_at": snap.now.isoformat(timespec="seconds"),
        "today": today.isoformat(),
        "timeline": snap.timeline,
        "today_items": sorted(today_items, key=lambda x: (not x["s"],)),
        "sched_today": [_item(e, today) for e in sched_today],
        "week": week_out,
        "stale": [_item(e, today) for e in stale[:12]],
    }


def build_state(root: Path, cfg: dict, now: dt.datetime) -> dict:
    """兼容入口（golden/外部调用）。"""
    return render_state(Snapshot.load(root, cfg, now))


def build_stats(root: Path, cfg: dict, now: dt.datetime, days: int = 84) -> dict:
    """近 N 天每日：非 bot 提交数（c）与新增勾选数（x）。"""
    import subprocess

    from vault import BOT_NAMES
    since = (now - dt.timedelta(days=days)).strftime("%Y-%m-%d")

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
        if an in BOT_NAMES or ae in BOT_NAMES:
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

    snap = Snapshot.load(root, cfg, now)
    state = render_state(snap)
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
