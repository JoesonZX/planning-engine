#!/usr/bin/env python3
"""晚间报告生成器：读 vault → 确定性解析 → （可选）GLM 摘要 → tomorrow.md。

物流模式：只统计任务与日程；skip_files（情绪/感情类）既不解析也不进 LLM。
零第三方依赖。失败策略：GLM 挂了不影响报告主体（正文是确定性的）。
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import subprocess
import sys
import urllib.request
from pathlib import Path
from zoneinfo import ZoneInfo

from parser import Entry, generated_files, load_config, load_vault

WEEKDAY_CN = ["一", "二", "三", "四", "五", "六", "日"]


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
            # 去掉 checkbox 壳，保留内容
            for token in ("- [x]", "- [X]"):
                text = text.replace(token, "", 1)
            done_lines.append(text.strip())
    return done_lines


def changed_files(root: Path, days: int = 7, exclude_prefixes: tuple[str, ...] = ("reports/", ".github/")) -> list[str]:
    log = _git(root, "log", f"--since={days} days ago", "--name-only", "--pretty=format:")
    seen: dict[str, None] = {}
    for line in log.splitlines():
        line = line.strip()
        if not line or line.startswith(exclude_prefixes):
            continue
        seen.setdefault(line, None)
    return list(seen)


# ---------------------------------------------------------------- 报告主体

def _fmt_day(d: dt.date) -> str:
    return f"{d.month}/{d.day} 周{WEEKDAY_CN[d.weekday()]}"


def _li(entry: Entry) -> str:
    return f"- {entry.display}"


def collect_stale(entries: list[Entry], file_ages: dict[str, float],
                  today: dt.date, stale_days: int) -> list[str]:
    """滑落项：过期 ≥stale_days 的带日期任务 + 文件陈旧的无日期任务。"""
    stale: list[str] = []
    for e in entries:
        if e.done is not False:
            continue
        latest = max(e.dates) if e.dates else None
        if latest and (today - latest).days >= stale_days:
            stale.append(f"- 已拖 {(today - latest).days} 天 ｜ {e.display}")
        elif not latest:
            age = file_ages.get(e.file)
            if age is not None and age >= stale_days:
                stale.append(f"- 文件 {age:.0f} 天未动 ｜ {e.display}")
    return stale


def build_report(root: Path, cfg: dict, now: dt.datetime) -> str:
    tz = ZoneInfo(cfg["timezone"])
    today = now.date()
    tomorrow = today + dt.timedelta(days=1)
    horizon_end = tomorrow + dt.timedelta(days=int(cfg["horizon_days"]))
    stale_days = int(cfg["stale_days"])

    entries = load_vault(root, cfg, today=today)
    # inbox 由第五区呈现，仪表盘/画像是生成物——都不参与解析，防重复计数
    excluded = generated_files(cfg)
    entries = [e for e in entries if e.file not in excluded]
    file_ages = file_last_commit_days(root)

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
    stale = collect_stale(entries, file_ages, today, stale_days)
    lines.append(f"## 三、滑落项（≥{stale_days} 天未动）")
    lines.append("")
    if stale:
        lines.extend(stale[:15])
        if len(stale) > 15:
            lines.append(f"- ……另有 {len(stale) - 15} 条，翻文件看全量")
    else:
        lines.append("（没有滑落项，干得漂亮）")
    lines.append("")

    # 四、今日完成
    done_today = added_done_checkboxes(root, 24)
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
    inbox_name = cfg.get("inbox_file", "inbox.md")
    inbox_lines: list[str] = []
    inbox_path = root / inbox_name
    if inbox_path.exists():
        for raw in inbox_path.read_text(encoding="utf-8").splitlines():
            s = raw.strip()
            if s and not s.startswith("#"):
                inbox_lines.append(s)
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
    changed = changed_files(root, 7)
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


# ---------------------------------------------------------------- GLM 摘要

GLM_URL = "https://open.bigmodel.cn/api/paas/v4/chat/completions"

SYSTEM_PROMPT = (
    "你是用户的规划助手。用户会给你一份自动生成的任务报告。"
    "你只谈任务、日程、优先级，绝不评论用户的情绪、感情或状态。"
    "语气平实、简短，像同事不像教练。"
)


def month_spend(usage_path: Path, now: dt.datetime) -> float:
    if not usage_path.exists():
        return 0.0
    total = 0.0
    for line in usage_path.read_text(encoding="utf-8").splitlines():
        try:
            rec = json.loads(line)
            if rec.get("month") == now.strftime("%Y-%m"):
                total += float(rec.get("est_cost_usd", 0.0))
        except (json.JSONDecodeError, ValueError, TypeError):
            continue
    return total


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
    payload = json.dumps({
        "model": cfg.get("model", "glm-4-flash"),
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.3,
        "max_tokens": 300,
    }).encode("utf-8")
    req = urllib.request.Request(
        GLM_URL, data=payload, method="POST",
        headers={"Authorization": f"Bearer {api_key}",
                 "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=45) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        content = data["choices"][0]["message"]["content"].strip()
        usage = data.get("usage", {})
        price = float(cfg.get("price_per_1k_usd", 0.0))
        est = (usage.get("total_tokens", 0) / 1000.0) * price
        record = {
            "month": now.strftime("%Y-%m"),
            "ts": now.isoformat(timespec="seconds"),
            "model": cfg.get("model"),
            "prompt_tokens": usage.get("prompt_tokens"),
            "completion_tokens": usage.get("completion_tokens"),
            "est_cost_usd": round(est, 6),
        }
        usage_path.parent.mkdir(parents=True, exist_ok=True)
        with usage_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
        return content, record
    except Exception as exc:  # noqa: BLE001 —— GLM 挂了不影响报告主体
        print(f"[warn] GLM summary skipped: {exc}", file=sys.stderr)
        return None, None


# ---------------------------------------------------------------- 仪表盘

def build_dashboard(root: Path, cfg: dict, now: dt.datetime) -> str:
    """每晚随报告生成根目录 仪表盘.md（全周视角首屏）。"""
    today = now.date()
    horizon_end = today + dt.timedelta(days=int(cfg["horizon_days"]))
    stale_days = int(cfg["stale_days"])

    entries = load_vault(root, cfg, today=today)
    excluded = generated_files(cfg)
    entries = [e for e in entries if e.file not in excluded]
    file_ages = file_last_commit_days(root)

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

    stale = collect_stale(entries, file_ages, today, stale_days)
    lines.append(f"## 🐌 滑落 ≥{stale_days} 天")
    lines.append("")
    if stale:
        lines.extend(stale[:10])
        if len(stale) > 10:
            lines.append(f"- ……另有 {len(stale) - 10} 条（周日周报看全量）")
    else:
        lines.append("（没有滑落项）")
    lines.append("")

    inbox_name = cfg.get("inbox_file", "inbox.md")
    inbox_path = root / inbox_name
    inbox_n = 0
    if inbox_path.exists():
        inbox_n = sum(1 for raw in inbox_path.read_text(encoding="utf-8").splitlines()
                      if raw.strip() and not raw.strip().startswith("#"))
    lines.append("## 📥 inbox")
    lines.append("")
    lines.append(f"待分拣 **{inbox_n}** 条（每周日 20:00 自动分拣；⏳ 项需你人工处理）")
    lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------- 入口

BOT_NAMES = {"planning-bot", "planning-bot@users.noreply.github.com"}


def vault_quiet(root: Path) -> bool:
    """24h 内无非 bot 提交 → vault 无变化，跳过 GLM 摘要（省钱省幂）。

    bot 判定用名字/邮箱精确匹配（与 state.py 统计同口径）。
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
    tz = ZoneInfo(cfg["timezone"])
    now = dt.datetime.now(tz)

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

    body = build_report(root, cfg, now)

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
    dash.write_text(build_dashboard(root, cfg, now) + "\n", encoding="utf-8")
    print(f"[ok] wrote {dash}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
