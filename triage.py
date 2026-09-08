#!/usr/bin/env python3
"""inbox 分拣：把 inbox.md 条目归类进白名单文件（v1，物流红线三重防御）。

红线：私人内容（情绪/感情类）永不进 LLM、永不写入任何文件，一律留 inbox 标待人工。
失败策略：GLM 挂了 / 超预算 → 全部 hold，数据永不丢失（inbox 只清已归类项）。
撤销：git revert 分拣 commit 即可，写入只发生在白名单文件的「分拣区」。
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
import urllib.request
from pathlib import Path
from zoneinfo import ZoneInfo

from parser import load_config

GLM_URL = "https://open.bigmodel.cn/api/paas/v4/chat/completions"

# 代码侧红线第一重：命中即 hold，不进 LLM
PRIVATE_KEYWORDS = [
    "心情", "情绪", "恋爱", "感情", "情感", "焦虑", "孤独", "失眠",
    "难过", "抑郁", "崩溃", "心理", "暗恋", "分手", "相亲", "emo",
]

SECTION_TITLE = "## 📥 分拣区（auto）"


def is_private(text: str) -> bool:
    low = text.lower()
    return any(k in low for k in PRIVATE_KEYWORDS)


# ---------------------------------------------------------------- GLM 批量分类

CLASSIFY_SYSTEM = (
    "你是任务分拣器。用户给你若干条随手记和几个目标文件。"
    "你只做任务/日程类内容的分类，绝不处理情绪、感情、心情类内容。"
    "拿不准、不像任务、或像私人内容的一律返回 HOLD。"
    "严格输出 JSON 数组，不要输出任何其他文字。"
)


def classify_batch(items: list[str], targets: list[str], cfg: dict,
                   api_key: str | None, usage_path: Path,
                   now: dt.datetime) -> dict[int, str] | None:
    """返回 {序号: 目标路径}；失败/超预算返回 None（调用方全部 hold）。"""
    if not api_key or not items:
        return None
    from report import month_spend  # 复用预算检查
    budget = float(cfg.get("monthly_budget_usd", 3.0))
    if month_spend(usage_path, now) >= budget:
        print("[triage] budget reached, holding all", file=sys.stderr)
        return None

    target_list = "\n".join(f"- {t}" for t in targets)
    numbered = "\n".join(f"{i}. {t}" for i, t in enumerate(items, 1))
    prompt = (
        f"目标文件：\n{target_list}\n\n"
        f"随手记条目：\n{numbered}\n\n"
        "把每条分到最合适的目标文件，输出 JSON 数组，"
        '形如 [{"n":1,"target":"路径"},{"n":2,"target":"HOLD"}]。'
        "不合适归档的一律 HOLD。"
    )
    payload = json.dumps({
        "model": cfg.get("model", "glm-4-flash"),
        "messages": [
            {"role": "system", "content": CLASSIFY_SYSTEM},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0,
        "max_tokens": 500,
    }).encode("utf-8")
    req = urllib.request.Request(
        GLM_URL, data=payload, method="POST",
        headers={"Authorization": f"Bearer {api_key}",
                 "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
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

        m = re.search(r"\[.*\]", content, re.S)
        if not m:
            return None
        result: dict[int, str] = {}
        for row in json.loads(m.group(0)):
            n, target = int(row.get("n", 0)), str(row.get("target", "HOLD"))
            result[n] = target
        return result
    except Exception as exc:  # noqa: BLE001 —— 失败 = 全部 hold
        print(f"[warn] classify failed: {exc}", file=sys.stderr)
        return None


# ---------------------------------------------------------------- 写入与重写

def append_to_target(root: Path, target: str, text: str, today: dt.date) -> bool:
    """白名单文件末尾的分拣区追加；返回是否写入成功。"""
    path = root / target
    if not path.exists():
        return False
    body = path.read_text(encoding="utf-8")
    line = f"- [ ] {text} （📥 {today.month}/{today.day} 来自 inbox）"
    if SECTION_TITLE in body:
        body = body.rstrip("\n") + "\n" + line + "\n"
    else:
        body = body.rstrip("\n") + "\n\n" + SECTION_TITLE + "\n\n" + line + "\n"
    path.write_text(body, encoding="utf-8")
    return True


def rewrite_inbox(root: Path, cfg: dict, held: list[str]) -> None:
    path = root / cfg.get("inbox_file", "inbox.md")
    header = "# inbox（手机随手记落点；每周日自动分拣，⏳ 项等你人工处理）\n"
    body = header
    for h in held:
        body += f"\n⏳ 待人工：{h}"
    if held:
        body += "\n"
    path.write_text(body, encoding="utf-8")


def triage(root: Path, cfg: dict, api_key: str | None) -> dict:
    now = dt.datetime.now(ZoneInfo(cfg["timezone"]))
    today = now.date()
    usage_path = root / "reports" / "usage.json"
    inbox_path = root / cfg.get("inbox_file", "inbox.md")
    targets = list(cfg.get("triage_targets", []))

    items: list[str] = []
    if inbox_path.exists():
        for raw in inbox_path.read_text(encoding="utf-8").splitlines():
            s = raw.strip()
            if s and not s.startswith("#"):
                # 去掉旧的 ⏳ 前缀与 checkbox 壳（inbox 行常带 - [ ]，写入时统一加）
                s = re.sub(r"^⏳\s*待人工[：:]\s*", "", s)
                s = re.sub(r"^[-*]\s+\[[ xX]\]\s*", "", s)
                s = s.strip()
                if s:
                    items.append(s)

    held: list[str] = []
    assigned: dict[int, str] = {}
    private_n = 0

    # 第一重：代码侧关键词
    llm_candidates: list[int] = []
    for i, text in enumerate(items, 1):
        if is_private(text):
            held.append(text)
            private_n += 1
        else:
            llm_candidates.append(i)

    # 第二重：提示词约束 + 第三重：白名单校验
    if llm_candidates:
        cand_texts = [items[i - 1] for i in llm_candidates]
        result = classify_batch(cand_texts, targets, cfg, api_key, usage_path, now)
        if result is None:
            held.extend(cand_texts)  # 失败 → 全部 hold
        else:
            for i in llm_candidates:
                target = result.get(i, "HOLD")
                if target in targets and i <= len(items):
                    assigned[i] = target
                else:
                    held.append(items[i - 1])

    # 写入
    written: dict[str, int] = {}
    for i, target in sorted(assigned.items()):
        if append_to_target(root, target, items[i - 1], today):
            written[target] = written.get(target, 0) + 1
        else:
            held.append(items[i - 1])

    rewrite_inbox(root, cfg, held)
    summary = {
        "ts": now.isoformat(timespec="seconds"),
        "total": len(items), "classified": sum(written.values()),
        "held": len(held), "held_private": private_n,
        "written": written,
    }
    out = root / "reports" / "triage-latest.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    return summary


def main() -> int:
    ap = argparse.ArgumentParser(description="inbox triage")
    ap.add_argument("--vault", required=True)
    ap.add_argument("--force", action="store_true",
                    help="跳过「周日 20:00」时刻门（手动/测试用）")
    args = ap.parse_args()
    root = Path(args.vault).resolve()
    cfg = load_config(root)
    now = dt.datetime.now(ZoneInfo(cfg["timezone"]))
    weekly_hour = int(cfg.get("weekly_hour", 20))
    if not args.force and not (now.weekday() == 6 and now.hour == weekly_hour):
        print(f"[skip] not Sunday {weekly_hour}:00 local "
              f"(now: {now.weekday()}/{now.hour})")
        return 0
    summary = triage(root, cfg, __import__("os").environ.get("GLM_API_KEY"))
    print(f"[ok] triage: {summary['classified']} classified, "
          f"{summary['held']} held ({summary['held_private']} private)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
