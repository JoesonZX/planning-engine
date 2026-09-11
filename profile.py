#!/usr/bin/env python3
"""画像层：每周日随周复盘更新 profile.md（Letta memory block 思想的 md 版）。

文件即记忆块，周 agent 即编辑者，git 即撤销。红线是结构性的，不靠 prompt 自觉：
1. 证据源只有周报 body（周报由 load_vault 产出，skip_files 物理进不来）
2. 手写区（user-start/user-end 标记之间）代码级提取回填，GLM 改不动
3. 产物过 PRIVATE_KEYWORDS 自检（复用 triage 关键词表），命中即放弃
4. 超过 MAX_LINES 行即放弃
失败策略：任何一步失败 = 保持上周版、退出码 0——画像坏了不传染周报，反之亦然。
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
from report import month_spend
from triage import is_private

TITLE = "# profile · 用户画像（每周日随复盘更新）"
USER_START = "<!-- user-start -->"
USER_END = "<!-- user-end -->"
MAX_LINES = 100
MAX_PROMPT_CHARS = 1500  # 注入晚报摘要时的截断上限

TEMPLATE = f"""{TITLE}

{USER_START}
（手写区：想让所有 agent 永远知道的几句话，一行一条。agent 逐字保留，永不改写）
{USER_END}

## 当前重心（本季度）
- （待周报归纳）

## 节律与习惯
- （待周报归纳）

## 规划校准（估时 vs 实际）
- （待周报归纳）

## 沟通偏好（报告与建议的语气）
- 平实同事语气；建议具体到何时何地（既有定案）

## 常见滑落模式
- （待周报归纳）

## 修订记录
"""

PROFILE_SYSTEM = (
    "你在维护一份用户画像文件（只记录规划行为模式）。规则："
    "只陈述可验证的行为模式，输出必须保留六个 ## 小节：当前重心/节律与习惯/"
    "规划校准/沟通偏好/常见滑落模式/修订记录，一节不少；"
    "每条一行、行尾带证据标注（如「W37·滑落区」「config·时间线」），每行不超过 100 字；"
    "无新证据的条目原样保留，不要改写；禁止推测或评价用户的情绪、心理、感情状态；"
    "禁止任何评价性、教练式语气；禁止把周报里的任务条目（⭐/checkbox 行）抄进画像。"
    "输出修订后的画像正文（不含标题、不含手写区、不含 user 标记），"
    "从第一个 ## 小节开始，不要输出任何其他文字。"
)

# 画像允许的小节（结构白名单：GLM 只能在这六节里写）
ALLOWED_SECTIONS = ("## 当前重心", "## 节律与习惯", "## 规划校准",
                    "## 沟通偏好", "## 常见滑落模式", "## 修订记录")
MAX_BULLET_CHARS = 120

# 周报里可作画像证据的统计区块（硬节点/inbox/三件事含具体任务，可能涉及医疗等
# 私务，绝不进画像的证据源）
SAFE_WEEKLY_SECTIONS = ("## 一、", "## 二、", "## 三、", "## 六、")


def sanitize_weekly_body(body: str) -> str:
    """只保留周报的统计区块（一/二/三/六），任务性内容（四/五/摘要/三件事）剔除。"""
    keep: list[str] = []
    cur_ok = False
    for line in body.splitlines():
        if line.startswith("## "):
            cur_ok = line.startswith(SAFE_WEEKLY_SECTIONS)
        if cur_ok:
            keep.append(line)
    return "\n".join(keep)


def _revision_guard_ok(revised: str) -> tuple[bool, str]:
    """产物侧结构白名单；返回（是否通过, 原因）。"""
    if "⭐" in revised or "- [" in revised:
        return False, "task syntax leaked (⭐/checkbox)"
    headers = [l.strip() for l in revised.splitlines()
               if l.strip().startswith("##")]
    if not headers:
        return False, "no ## sections"
    for h in headers:
        if not h.startswith(ALLOWED_SECTIONS):
            return False, f"unknown section: {h[:30]}"
    for line in revised.splitlines():
        s = line.strip()
        if s.startswith("-") and len(s) > MAX_BULLET_CHARS:
            return False, f"bullet over {MAX_BULLET_CHARS} chars (task dump)"
    return True, ""


def split_regions(text: str) -> tuple[str, str]:
    """返回（手写区内容, agent 区正文）。无标记时手写区为空、agent 区为全文。"""
    lines = text.splitlines()
    try:
        start = lines.index(USER_START)
        end = lines.index(USER_END)
        if start < end:
            handwritten = "\n".join(lines[start + 1:end])
            agent = "\n".join(lines[end + 1:]).strip()
            return handwritten, agent
    except ValueError:
        pass
    return "", text.strip()


def load_for_prompt(root: Path, cfg: dict) -> str:
    """给晚报 GLM 摘要注入用的画像文本（全文截断，不存在则空串）。"""
    path = root / cfg.get("profile_file", "profile.md")
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8")[:MAX_PROMPT_CHARS]


def _glm_revise(handwritten: str, old_agent: str, weekly_body: str,
                cfg: dict, api_key: str | None, usage_path: Path,
                now: dt.datetime) -> str | None:
    """GLM 修订 agent 区；预算帽/失败与周报同一套纪律，失败返回 None。"""
    if not api_key:
        return None
    budget = float(cfg.get("monthly_budget_usd", 3.0))
    if month_spend(usage_path, now) >= budget:
        print("[profile] budget reached, keeping last version", file=sys.stderr)
        return None

    from report import GLM_URL  # 局部导入避免 report ⇄ profile 环
    user_prompt = (
        f"【手写区（仅供理解用户自述，严禁输出或修改）】\n{handwritten or '（空）'}\n\n"
        f"【旧画像正文】\n{old_agent}\n\n"
        f"【本周复盘（唯一证据源）】\n{weekly_body}\n\n"
        "输出修订后的画像正文。只改有新证据的条目，其余原样保留，"
        "末尾更新修订记录（- M.D Wxx: 一句说明）。"
    )
    payload = json.dumps({
        "model": cfg.get("model", "glm-4-flash"),
        "messages": [{"role": "system", "content": PROFILE_SYSTEM},
                     {"role": "user", "content": user_prompt}],
        "temperature": 0.3,
        "max_tokens": 600,
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
        record = {"month": now.strftime("%Y-%m"), "ts": now.isoformat(timespec="seconds"),
                  "model": cfg.get("model"), "prompt_tokens": usage.get("prompt_tokens"),
                  "completion_tokens": usage.get("completion_tokens"),
                  "est_cost_usd": round(est, 6)}
        usage_path.parent.mkdir(parents=True, exist_ok=True)
        with usage_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
        return content
    except Exception as exc:  # noqa: BLE001 —— 失败 = 保持上周版
        print(f"[warn] profile revision skipped: {exc}", file=sys.stderr)
        return None


def update_profile(root: Path, cfg: dict, api_key: str | None,
                   usage_path: Path, now: dt.datetime, weekly_body: str) -> bool:
    """维护 profile.md；返回是否更新。任何守卫失败都保持原样。"""
    path = root / cfg.get("profile_file", "profile.md")
    old = path.read_text(encoding="utf-8") if path.exists() else TEMPLATE
    handwritten, old_agent = split_regions(old)

    # 守卫〇（证据侧）：只喂统计区块，任务性内容（硬节点/inbox/三件事）不进证据源
    evidence = sanitize_weekly_body(weekly_body)
    revised = _glm_revise(handwritten, old_agent, evidence,
                          cfg, api_key, usage_path, now)
    if not revised:
        return False

    # 守卫一：红线关键词（只查 agent 产物；手写区是用户自己的，不拦）
    if is_private(revised):
        print("[profile] guard: private keyword hit, keeping last version",
              file=sys.stderr)
        return False
    # 守卫二：结构白名单（小节/任务语法/行长度——v4 首跑抓出 flash 会整段抄任务）
    ok, reason = _revision_guard_ok(revised)
    if not ok:
        print(f"[profile] guard: {reason}, keeping last version", file=sys.stderr)
        return False
    # 剥离 GLM 产物里的手写区（标记对连内容整块删除，孤儿标记行也删）——
    # 手写区代码级回填，GLM 无权重建
    revised = re.sub(rf"{re.escape(USER_START)}.*?{re.escape(USER_END)}",
                     "", revised, flags=re.S)
    revised = "\n".join(l for l in revised.splitlines()
                        if l.strip() not in (USER_START, USER_END))
    # 组装（手写区来自旧文件原文，与 GLM 输出无关）
    new = f"{TITLE}\n\n{USER_START}\n{handwritten.rstrip()}\n{USER_END}\n\n{revised.strip()}\n"
    # 守卫三：行数上限（聊天上下文每文件 6000 字符 cap 的余量保证）
    if new.count("\n") + 1 > MAX_LINES:
        print(f"[profile] guard: exceeds {MAX_LINES} lines, keeping last version",
              file=sys.stderr)
        return False
    path.write_text(new, encoding="utf-8")
    return True


def main() -> int:
    ap = argparse.ArgumentParser(description="profile maintainer (standalone test)")
    ap.add_argument("--vault", required=True)
    ap.add_argument("--force", action="store_true",
                    help="画像无独立幂等门（周日随周报跑，重复调用无害）")
    args = ap.parse_args()
    root = Path(args.vault).resolve()
    cfg = load_config(root)
    now = dt.datetime.now(ZoneInfo(cfg["timezone"]))
    body_path = root / "reports"
    body = ""
    for p in sorted(body_path.glob("week-*.md")):
        body = p.read_text(encoding="utf-8")  # 取最新一份周报当证据源
    import os
    updated = update_profile(root, cfg, os.environ.get("GLM_API_KEY"),
                             root / "reports" / "usage.json", now, body)
    print(f"[ok] profile {'updated' if updated else 'kept (revision skipped)'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
