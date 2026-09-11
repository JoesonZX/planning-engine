#!/usr/bin/env python3
"""周复盘：周日 20:00 生成 reports/week-YYYY-Www.md（七板块，只统计不评判）。

数据源：Snapshot（滑落/硬节点/inbox）+ git diff（完成/新增计数）
+ GLM（下周三件事草稿）+ 顺带维护画像（profile.update_profile）。
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import subprocess
from pathlib import Path
from zoneinfo import ZoneInfo

from parser import load_config
from vault import Snapshot
from llm import call_glm, month_spend
from report import SYSTEM_PROMPT, collect_stale


# ---------------------------------------------------------------- git 统计

def added_checkboxes_by_file(root: Path, since: str, until: str | None = None,
                             done: bool = True) -> dict[str, int]:
    """时间窗内 diff 新增的 checkbox 行数，按文件分组。"""
    args = ["git", "-C", str(root), "-c", "core.quotepath=false",
            "log", "-p", "--unified=0", f"--since={since}"]
    if until:
        args.append(f"--until={until}")
    args += ["--", "*.md"]
    try:
        log = subprocess.run(args, capture_output=True, text=True,
                             encoding="utf-8", errors="replace",
                             timeout=60, check=True).stdout
    except (subprocess.SubprocessError, OSError):
        return {}
    counts: dict[str, int] = {}
    current = "?"
    for line in log.splitlines():
        if line.startswith("diff --git"):
            m = re.match(r"diff --git a/(.*?) b/", line)
            current = m.group(1) if m else "?"
        elif line.startswith("+") and not line.startswith("+++"):
            hit = ("- [x]" in line or "- [X]" in line) if done else ("- [ ]" in line)
            if hit:
                counts[current] = counts.get(current, 0) + 1
    return counts


# ---------------------------------------------------------------- 报告主体

def render_weekly(snap: Snapshot) -> tuple[str, str]:
    today, cfg, now = snap.today, snap.cfg, snap.now
    # 报告"刚结束的完整周"（周一~周日）：今天周日则本周日截止，否则上个周日。
    # 与生成时刻解耦——GitHub cron 延迟到周一/周二跑，报告的仍是正确的那一周。
    week_end = today if today.weekday() == 6 else today - dt.timedelta(days=today.weekday() + 1)
    week_start = week_end - dt.timedelta(days=6)
    iso = week_end.isocalendar()

    entries = snap.entries

    next_day = (week_end + dt.timedelta(days=1)).isoformat()
    prev_end = (week_start - dt.timedelta(days=1)).isoformat()
    done_now = added_checkboxes_by_file(snap.root, week_start.isoformat(), next_day, done=True)
    done_prev = added_checkboxes_by_file(snap.root, prev_end, week_start.isoformat(), done=True)
    new_now = added_checkboxes_by_file(snap.root, week_start.isoformat(), done=False)

    lines: list[str] = []
    title = f"周复盘 · {iso[0]}-W{iso[1]:02d}（{week_start:%m/%d}–{week_end:%m/%d}）"
    lines.append(f"# 📊 {title}")
    lines.append("")
    lines.append(f"> 生成于 {now:%Y-%m-%d %H:%M} ｜ 只统计不评判（物流模式）")
    lines.append("")

    # 一、本周完成
    total_now = sum(done_now.values())
    total_prev = sum(done_prev.values())
    lines.append("## 一、本周完成")
    lines.append("")
    if done_now:
        for f in sorted(done_now, key=done_now.get, reverse=True):
            lines.append(f"- {done_now[f]} ｜ {f}")
    else:
        lines.append("最近 7 天没有新勾选")
    lines.append("")
    lines.append(f"**{total_now}** 件（上周 {total_prev}，"
                 f"{'↑' if total_now > total_prev else '↓' if total_now < total_prev else '持平'}）")
    lines.append("")

    # 二、环比（并入一区顶部，这里给新增）
    lines.append("## 二、本周新增任务")
    lines.append("")
    new_total = sum(new_now.values())
    lines.append(f"新写了 **{new_total}** 条开放任务" +
                 (f"（其中 {new_now.get('?', 0)} 条来源未识别）" if new_now.get("?") else ""))
    lines.append("")

    # 三、滑落升级（全量，不截断）
    stale = collect_stale(snap)
    lines.append(f"## 三、滑落升级（≥{snap.stale_days} 天未动，全量）")
    lines.append("")
    if stale:
        lines.extend(stale)
    else:
        lines.append("（没有滑落项）")
    lines.append("")

    # 四、本周硬节点（报告周范围内的 ⭐）
    nodes = [e for e in entries if e.star
             and any(week_start <= d <= week_end for d in e.dates)]
    nodes.sort(key=lambda e: (e.file, e.line))
    lines.append("## 四、本周硬节点（⭐）")
    lines.append("")
    if nodes:
        for e in nodes:
            state = "✅" if e.done else "⬜" if e.done is False else "·"
            lines.append(f"- {state} {e.display}")
        lines.append("")
        lines.append("（⬜/· 状态请自查——表格行里的硬节点引擎无法判定完成态）")
    else:
        lines.append("（本周没有 ⭐ 硬节点）")
    lines.append("")

    # 五、inbox 与分拣残留
    held_n = sum(1 for s in snap.inbox_lines if s.startswith("⏳"))
    triage_info = ""
    triage_path = snap.root / "reports" / "triage-latest.json"
    if triage_path.exists():
        try:
            t = json.loads(triage_path.read_text(encoding="utf-8"))
            triage_info = f"上次分拣（{t.get('ts', '?')[:10]}）：归档 {t.get('classified', 0)} 条、待人工 {t.get('held', 0)} 条"
        except json.JSONDecodeError:
            pass
    lines.append("## 五、inbox 残留")
    lines.append("")
    lines.append(f"⏳ 待人工 **{held_n}** 条" + (f" ｜ {triage_info}" if triage_info else ""))
    lines.append("")

    # 六、用量
    usage_path = snap.root / "reports" / "usage.json"
    spent = month_spend(usage_path, now)
    budget = float(cfg.get("monthly_budget_usd", 3.0))
    lines.append("## 六、GLM 用量")
    lines.append("")
    lines.append(f"本月 ${spent:.2f} / 预算 ${budget:.2f}")
    lines.append("")
    return "\n".join(lines), title


def build_weekly(root: Path, cfg: dict, now: dt.datetime) -> tuple[str, str]:
    """兼容入口（golden/外部调用）。"""
    return render_weekly(Snapshot.load(root, cfg, now))


def glm_three_things(weekly_body: str, cfg: dict, api_key: str | None,
                     usage_path: Path, now: dt.datetime) -> str | None:
    if not api_key:
        return None
    budget = float(cfg.get("monthly_budget_usd", 3.0))
    if month_spend(usage_path, now) >= budget:
        return None
    prompt = (
        "下面是本周复盘数据。基于它起草「下周三件事」：三条具体的、"
        "可直接执行的任务式建议（每条一句话，物流语气，不评判、不谈状态）。"
        "只从报告里已有的事项中挑优先级最高的，格式：\n1. …\n2. …\n3. …\n\n"
        + weekly_body
    )
    return call_glm(
        cfg,
        [{"role": "system", "content": SYSTEM_PROMPT},
         {"role": "user", "content": prompt}],
        api_key=api_key, usage_path=usage_path, now=now,
        max_tokens=300, temperature=0.3, label="three-things")


def main() -> int:
    ap = argparse.ArgumentParser(description="weekly review")
    ap.add_argument("--vault", required=True)
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()
    root = Path(args.vault).resolve()
    cfg = load_config(root)
    now = dt.datetime.now(ZoneInfo(cfg["timezone"]))
    # 幂等去重：本 ISO 周的周报文件已存在即跳过（force 重生成）。
    # 与运行时刻解耦——cron 延迟到周一/周二，报告的仍是刚结束的那个周。
    iso = (now.date() if now.date().weekday() == 6
           else now.date() - dt.timedelta(days=now.date().weekday() + 1)).isocalendar()
    out = root / "reports" / f"week-{iso[0]}-W{iso[1]:02d}.md"
    if not args.force and out.exists():
        print(f"[skip] {out.name} already exists")
        return 0

    body, _title = render_weekly(Snapshot.load(root, cfg, now))

    draft = glm_three_things(body, cfg, os.environ.get("GLM_API_KEY"),
                             root / "reports" / "usage.json", now)
    lines = body.splitlines()
    insert = next((i for i, l in enumerate(lines) if l.startswith("## 五、")), len(lines))
    block = ["## ⏭️ 下周三件事（草稿，认可后自己抄进清单）", ""]
    block += (draft or "（本次 GLM 未生成——自己想三件事）").splitlines()
    block += [""]
    body = "\n".join(lines[:insert] + block + lines[insert:])

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(body + "\n", encoding="utf-8")
    print(f"[ok] wrote {out}")

    # 画像层：周报落盘后顺带维护（证据源 = 本周周报 body；失败保持上周版）
    from profile import update_profile
    updated = update_profile(root, cfg, os.environ.get("GLM_API_KEY"),
                             root / "reports" / "usage.json", now, body)
    print(f"[ok] profile {'updated' if updated else 'kept (revision skipped)'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
