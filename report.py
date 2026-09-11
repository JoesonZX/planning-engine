#!/usr/bin/env python3
"""晚间报告生成器：Snapshot（一次加载）→ render_report / render_dashboard。

物流模式：只统计任务与日程；skip_files（情绪/感情类）物理进不了 Snapshot。
零第三方依赖。失败策略：GLM 挂了不影响报告主体（正文是确定性的）。
"""

from __future__ import annotations

import argparse
import datetime as dt
import re
from pathlib import Path
from zoneinfo import ZoneInfo

from parser import Entry, load_config
from vault import Snapshot, vault_quiet

# llm 置于 vault 之后导入（无环；仅为可读性）
from llm import call_glm, month_spend  # noqa: E402

WEEKDAY_CN = ["一", "二", "三", "四", "五", "六", "日"]


def _fmt_day(d: dt.date) -> str:
    return f"{d.month}/{d.day} 周{WEEKDAY_CN[d.weekday()]}"


def _li(entry: Entry) -> str:
    return f"- {entry.display}"


def collect_stale(snap: Snapshot) -> list[str]:
    """滑落项：过期 ≥stale_days 的带日期任务 + 文件陈旧的无日期任务。"""
    stale: list[str] = []
    for e in snap.entries:
        if e.done is not False:
            continue
        latest = max(e.dates) if e.dates else None
        if latest and (snap.today - latest).days >= snap.stale_days:
            stale.append(f"- 已拖 {(snap.today - latest).days} 天 ｜ {e.display}")
        elif not latest:
            age = snap.file_ages.get(e.file)
            if age is not None and age >= snap.stale_days:
                stale.append(f"- 文件 {age:.0f} 天未动 ｜ {e.display}")
    return stale


# ---------------------------------------------------------------- 报告主体

def render_report(snap: Snapshot) -> str:
    cfg, today, now = snap.cfg, snap.today, snap.now
    tz = ZoneInfo(cfg["timezone"])
    tomorrow = today + dt.timedelta(days=1)
    horizon_end = tomorrow + dt.timedelta(days=int(cfg["horizon_days"]))

    entries = snap.entries

    lines: list[str] = []
    lines.append(f"# 🌙 晚间报告 · 明天 {_fmt_day(tomorrow)}")
    lines.append("")
    lines.append(f"> 生成于 {now:%Y-%m-%d %H:%M} {str(tz)} ｜ 本报告只含任务与日程（物流模式）")
    lines.append("")

    # 一、明日事项
    tomorrow_items = [e for e in entries if any(d == tomorrow for d in e.dates)]
    tomorrow_items.sort(key=lambda e: (not e.star, e.file, e.line))
    lines.append(f"## 一、明日事项（{_fmt_day(tomorrow)}）")
    lines.append("")
    if tomorrow_items:
        for e in tomorrow_items:
            lines.append(_li(e))
    else:
        lines.append("（明天没有标注日期的事项——顺手把明天的锚点写进清单？）")
    lines.append("")

    # 二、未来 N 天死线（已在一区出现过的行不再重复）
    shown = {(e.file, e.line) for e in tomorrow_items}
    horizon_items: list[tuple[dt.date, Entry]] = []
    for e in entries:
        if (e.file, e.line) in shown:
            continue
        future = [d for d in e.dates if tomorrow < d <= horizon_end]
        if future:
            horizon_items.append((min(future), e))
    horizon_items.sort(key=lambda t: (t[0], not t[1].star, t[1].file))
    lines.append(f"## 二、未来 {cfg['horizon_days']} 天死线")
    lines.append("")
    if horizon_items:
        last_day = None
        for d, e in horizon_items:
            if d != last_day:
                lines.append(f"### {_fmt_day(d)}")
                lines.append("")
                last_day = d
            lines.append(_li(e))
    else:
        lines.append("（未来一周没有死线）")
    lines.append("")

    # 三、滑落项
    stale = collect_stale(snap)
    lines.append(f"## 三、滑落项（≥{snap.stale_days} 天未动）")
    lines.append("")
    if stale:
        lines.extend(stale[:15])
        if len(stale) > 15:
            lines.append(f"- ……另有 {len(stale) - 15} 条，翻文件看全量")
    else:
        lines.append("（没有滑落项，干得漂亮）")
    lines.append("")

    # 四、今日完成
    done_today = snap.done_24h
    open_count = sum(1 for e in entries if e.done is False)
    lines.append("## 四、今日完成")
    lines.append("")
    if done_today:
        lines.append(f"今天勾掉了 **{len(done_today)}** 件：")
        for text in done_today[:10]:
            lines.append(f"- ✅ {text}")
        if len(done_today) > 10:
            lines.append(f"- ……另有 {len(done_today) - 10} 件")
    else:
        lines.append("最近 24 小时没有新勾选（旅行/休息日不算失败）")
    lines.append("")
    lines.append(f"当前未完成任务总数：**{open_count}**")
    lines.append("")

    # 五、inbox 未分拣
    inbox_lines = snap.inbox_lines
    lines.append("## 五、inbox 未分拣")
    lines.append("")
    if inbox_lines:
        for s in inbox_lines[:10]:
            lines.append(f"- 📥 {s}")
        if len(inbox_lines) > 10:
            lines.append(f"- ……另有 {len(inbox_lines) - 10} 条")
        lines.append("")
        lines.append("（周末让我分拣进对应文件）")
    else:
        lines.append("（inbox 是空的）")
    lines.append("")

    # 六、近 7 天改动（语法漂移的人工兜底）
    changed = snap.changed_7d
    lines.append("## 六、近 7 天改动的文件（人眼扫一遍格式）")
    lines.append("")
    if changed:
        for f in changed[:20]:
            lines.append(f"- {f}")
        if len(changed) > 20:
            lines.append(f"- ……另有 {len(changed) - 20} 个文件")
    else:
        lines.append("（近 7 天无改动）")
    lines.append("")
    return "\n".join(lines)


def build_report(root: Path, cfg: dict, now: dt.datetime) -> str:
    """兼容入口（golden/外部调用）；一次运行的 main 共享 Snapshot。"""
    return render_report(Snapshot.load(root, cfg, now))


# ---------------------------------------------------------------- GLM 摘要

SYSTEM_PROMPT = (
    "你是用户的规划助手。用户会给你一份自动生成的任务报告。"
    "你只谈任务、日程、优先级，绝不评论用户的情绪、感情或状态。"
    "语气平实、简短，像同事不像教练。"
)


def _build_summary_prompt(report_body: str, profile_text: str = "") -> str:
    """摘要 prompt；有画像时注入（2026.9.11 定案：晚报使用画像）。"""
    base = (
        "下面是明天的规划报告。用 2-3 句中文总结：明天最重要的事是什么、"
        "有没有需要今晚先动一步的；再给一条具体的明日建议。不要复述全部内容。"
    )
    if profile_text:
        base = ("用户画像（个性化参考，只用于调整建议的侧重点与措辞，"
                f"不要复述画像内容）：\n{profile_text}\n\n{base}")
    return base + "\n\n" + report_body


def glm_summary(report_body: str, cfg: dict, api_key: str | None,
                usage_path: Path, now: dt.datetime,
                profile_text: str = "") -> tuple[str | None, dict | None]:
    """预算帽：本月花费超预算且今天不是周日 → 跳过（降频为周日模式）。"""
    if not api_key:
        return None, None
    budget = float(cfg.get("monthly_budget_usd", 3.0))
    spent = month_spend(usage_path, now)
    is_sunday = now.date().weekday() == 6
    if spent >= budget and not is_sunday:
        return None, {"skipped": "budget_reached", "spent": spent}

    prompt = _build_summary_prompt(report_body, profile_text)
    content = call_glm(
        cfg,
        [{"role": "system", "content": SYSTEM_PROMPT},
         {"role": "user", "content": prompt}],
        api_key=api_key, usage_path=usage_path, now=now,
        max_tokens=300, temperature=0.3, label="GLM summary")
    if content is None:
        return None, None
    return content, None


# ---------------------------------------------------------------- 仪表盘

def render_dashboard(snap: Snapshot) -> str:
    """全周视角首屏（每晚随报告写入根目录 仪表盘.md）。"""
    cfg, today, now = snap.cfg, snap.today, snap.now
    horizon_end = today + dt.timedelta(days=int(cfg["horizon_days"]))

    entries = snap.entries

    lines: list[str] = []
    lines.append(f"# 🧭 仪表盘 · {_fmt_day(today)} {now:%H:%M}")
    lines.append("")
    lines.append("> 每晚 21:00 随晚间报告自动更新 ｜ 只含任务与日程（物流模式）")
    lines.append("")

    today_items = [e for e in entries if any(d == today for d in e.dates)]
    today_items.sort(key=lambda e: (not e.star, e.file))
    lines.append("## ✅ 今天")
    lines.append("")
    if today_items:
        lines.extend(_li(e) for e in today_items)
    else:
        lines.append("（今天没有标注事项）")
    lines.append("")

    week: list[tuple[dt.date, Entry]] = []
    for e in entries:
        future = [d for d in e.dates if today < d <= horizon_end]
        if future:
            week.append((min(future), e))
    week.sort(key=lambda t: (t[0], not t[1].star))
    lines.append(f"## 📅 未来 {cfg['horizon_days']} 天")
    lines.append("")
    if week:
        last = None
        for d, e in week:
            if d != last:
                lines.append(f"**{_fmt_day(d)}**")
                last = d
            lines.append(_li(e))
    else:
        lines.append("（未来一周没有死线）")
    lines.append("")

    stale = collect_stale(snap)
    lines.append(f"## 🐌 滑落 ≥{snap.stale_days} 天")
    lines.append("")
    if stale:
        lines.extend(stale[:10])
        if len(stale) > 10:
            lines.append(f"- ……另有 {len(stale) - 10} 条（周日周报看全量）")
    else:
        lines.append("（没有滑落项）")
    lines.append("")

    lines.append("## 📥 inbox")
    lines.append("")
    lines.append(f"待分拣 **{len(snap.inbox_lines)}** 条（每周日 20:00 自动分拣；⏳ 项需你人工处理）")
    lines.append("")
    return "\n".join(lines)


def build_dashboard(root: Path, cfg: dict, now: dt.datetime) -> str:
    """兼容入口（golden/外部调用）。"""
    return render_dashboard(Snapshot.load(root, cfg, now))


# ---------------------------------------------------------------- 入口

def main() -> int:
    ap = argparse.ArgumentParser(description="evening report generator")
    ap.add_argument("--vault", required=True)
    ap.add_argument("--out", default="reports/tomorrow.md")
    ap.add_argument("--usage", default="reports/usage.json")
    ap.add_argument("--force", action="store_true",
                    help="跳过 21:00 时刻门（手动触发/测试用）")
    args = ap.parse_args()

    root = Path(args.vault).resolve()
    cfg = load_config(root)
    now = dt.datetime.now(ZoneInfo(cfg["timezone"]))

    # 幂等去重（取代时刻门：GitHub cron 实测延迟可达 4h+，掐时间必漏）：
    # 今天已生成且已过傍晚 → 跳过；清晨生成的版本允许傍晚刷新一次
    if not args.force:
        out_path = root / args.out
        if out_path.exists():
            head = out_path.read_text(encoding="utf-8")[:400]
            m = re.search(r"生成于 (\d{4}-\d{2}-\d{2})", head)
            if m and m.group(1) == now.strftime("%Y-%m-%d") and now.hour < 18:
                print("[skip] today's report already generated (pre-evening)")
                return 0

    snap = Snapshot.load(root, cfg, now)  # 报告与仪表盘共享一次加载
    body = render_report(snap)

    quiet = vault_quiet(root)
    if quiet:
        print("[info] vault unchanged in 24h — skipping GLM summary")
    # 画像注入（v4 定案）：只在真的要调 GLM 时读；画像缺失/读挂不拦报告
    profile_text = ""
    if not quiet:
        try:
            from profile import load_for_prompt
            profile_text = load_for_prompt(root, cfg)
        except Exception:  # noqa: BLE001
            profile_text = ""
    summary, _rec = (None, None) if quiet else glm_summary(
        body, cfg, __import__("os").environ.get("GLM_API_KEY"),
        root / args.usage, now, profile_text=profile_text)
    if summary:
        lines = body.splitlines()
        insert = next((i for i, l in enumerate(lines) if l.startswith("## 一、")), len(lines))
        block = ["## 💡 今晚摘要", "", summary, ""]
        body = "\n".join(lines[:insert] + block + lines[insert:])

    out_path = root / args.out
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(body + "\n", encoding="utf-8")
    print(f"[ok] wrote {out_path}")

    dash = root / cfg.get("dashboard_file", "仪表盘.md")
    dash.write_text(render_dashboard(snap) + "\n", encoding="utf-8")
    print(f"[ok] wrote {dash}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
