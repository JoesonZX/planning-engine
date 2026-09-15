#!/usr/bin/env python3
"""state/stats 生成器：给前端任务中心视图的结构化数据（Q4a：解析只在 Python）。

state.json  = 今天/未来7天/滑落 三组结构化条目 + 日常时间线模板
stats.json  = 近 84 天每日 {非 bot 提交数, 新勾选数}（热力图数据源）
"""

from __future__ import annotations

import argparse
import calendar
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
    # 任务（checkbox，可勾可统计）与日程（表格/普通日期行，只按日展示）彻底分离（v7）；
    # v9 三分：其余带日期行进 misc（「其他带日期」折叠区，可删可改期——不再隐形）
    all_entries = dedup_entries(snap.entries)
    entries = [e for e in all_entries if e.done is not None]          # 任务

    def _sched_qualifies(e) -> bool:
        return bool(SCHED_LEAD.search(e.raw.lstrip()) or "｜" in e.raw)

    schedule = [e for e in all_entries
                if e.done is None and e.dates and _sched_qualifies(e)]   # 日程
    misc = [e for e in all_entries
            if e.done is None and e.dates and not _sched_qualifies(e)]   # 其他带日期
    file_ages = snap.file_ages
    index = briefs.load_index(snap.root)

    def _brief_of(e) -> str | None:
        meta = index.get(briefs.task_key(e.file, e.raw))
        return meta.get("file") if meta else None

    today_items, week, stale_src = [], {}, []
    seen_week: set[str] = set()
    sched_today: list = []
    sched_week: dict[str, list] = {}
    misc_today: list = []
    misc_week: dict[str, list] = {}
    # misc：带日期的非任务非日程行——按首个未来日期归因，不做逐日展开（备注不是日程）
    for e in misc:
        if today in e.dates:
            misc_today.append(_item(e, today))
        future = sorted(d for d in e.dates if today < d <= horizon_end)
        if future:
            misc_week.setdefault(future[0].isoformat(), []).append(_item(e, today))
    for e in schedule:
        # v8：日程 blob 行（「SD 9.11–9.14 ｜ Joshua Tree 9.15 ｜ …」）逐日展开；
        # 今天视图与周视图走同一展开（复查修正：原 sched_today 用旧 parser 日期，
        # 框架行出现在周视图 9/12 组、今天视图却缺席）；表格行走首格归因
        expanded = expand_schedule(e.raw, today) if not e.raw.lstrip().startswith("|") else []
        if len(expanded) >= 1:
            for d, seg in expanded:
                if not (today <= d <= horizon_end):
                    continue
                it = _item(e, today)
                it["t"] = seg
                it["seg"] = True
                if d == today:
                    sched_today.append(it)
                else:
                    sched_week.setdefault(d.isoformat(), []).append(it)
            continue
        if today in e.dates:
            sched_today.append(_item(e, today))
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
    for d in sorted(set(week) | set(sched_week) | set(misc_week)):
        week_out.append({
            "date": d,
            "items": week.get(d, []),
            "sched": sched_week.get(d, []),
            "misc": misc_week.get(d, []),
        })

    # ---- v10 月窗：自然月全量（过去天只含未完成任务）。v13：spans 停止生成
    #（月视图 v12 已退役，前端无消费方）；跨多日条目保持「抽离不复制」语义，只是不再输出 ----
    m_start = today.replace(day=1)
    m_end = m_start.replace(day=calendar.monthrange(m_start.year, m_start.month)[1])
    m_days: dict[str, dict] = {}

    def _day(d: dt.date) -> dict:
        return m_days.setdefault(d.isoformat(), {"items": [], "sched": [], "misc": []})

    def _month_dates(e) -> list:
        return sorted(d for d in e.dates if m_start <= d <= m_end)

    for e in schedule:  # 日程（含展开段）按天呈现
        expanded = expand_schedule(e.raw, today) if not e.raw.lstrip().startswith("|") else []
        if expanded:
            for d, seg in expanded:
                if m_start <= d <= m_end:
                    it = _item(e, today)
                    it["t"] = seg
                    it["seg"] = True
                    _day(d)["sched"].append(it)
        else:
            dm = _month_dates(e)
            if dm:
                _day(dm[0])["sched"].append(_item(e, today))
    for e in misc:
        dm = _month_dates(e)
        if len(dm) < 2:  # 跨多日条目原归 spans，现直接不进月窗
            for d in dm:
                _day(d)["misc"].append(_item(e, today))
    for e in entries:  # checkbox 任务：多日期行是「跨期间的约定」，抽离不复制
        dm = _month_dates(e)
        if len(dm) >= 2:
            continue
        for d in dm:
            if d < today and e.done is not False:
                continue  # 过去天只收未完成
            _day(d)["items"].append(_item(e, today))

    month = {
        "label": f"{m_start.year}年{m_start.month}月",
        "start": m_start.isoformat(),
        "end": m_end.isoformat(),
        "days": {k: m_days[k] for k in sorted(m_days)},
    }
    return {
        "generated_at": snap.now.isoformat(timespec="seconds"),
        "today": today.isoformat(),
        "timeline": snap.timeline,
        "today_items": sorted(today_items, key=lambda x: (not x["s"],)),
        "sched_today": sched_today,
        "misc_today": misc_today,
        "week": week_out,
        "stale": [_item(e, today) for e in stale[:12]],
        "month": month,
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
