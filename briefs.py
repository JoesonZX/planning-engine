#!/usr/bin/env python3
"""任务简报层：外部流程型任务 → 带引用的操作手册（v5 核心增量）。

nightly 随晚报运行：⭐ 或 7 天窗口内、命中外部流程启发式（预约/申请/缴费…）
或带 #简报 标记的任务，用 GLM 联网检索（bigmodel web_search 工具）生成
「怎么做」简报写入 reports/briefs/<slug>.md；index.json 记账做幂等。

信任纪律：产物必须含 ≥1 个 URL（联网检索证据），否则不落盘——
宁可没有简报，不可编造流程。失败策略与画像一致：不生成 ≠ 报错，
晚间其他产出物不受任何影响。输入只有任务行文本（skip_files 物理隔离不变）。
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

from parser import load_config
from vault import Snapshot
from llm import call_glm

BRIEFS_DIR = "reports/briefs"
MAX_NEW_PER_NIGHT = 3
BRIEF_TTL_DAYS = 21
MAX_BRIEF_LINES = 60

# 外部流程启发式：命中任一即视为「需要操作手册」的任务
TRIGGER_KEYWORDS = [
    "预约", "申请", "注册", "缴费", "交费", "办理", "签证", "面签", "笔试",
    "体检", "疫苗", "保险", "挂号", "开户", "激活", "过户", "年检", "审稿",
    "提交", "补办", "换证", " interview", "appointment", "apply", "renew",
    "register", "book", "submit",
]
MANUAL_TAG = "#简报"

BRIEF_SYSTEM = (
    "你是任务简报撰写器，联网检索后产出一份「怎么做」的操作手册（markdown）。"
    "结构固定，依次为：\n"
    "# 📋 任务简报 · <任务名>\n"
    "## 一句话（先做什么；最省时间的路径，15 分钟能完成的先说）\n"
    "## ① 前提自查（- [ ] 逐项列出需要的文件/条件/资格，每项注明要求）\n"
    "## ② 核心步骤（编号步骤，官方链接给出；能今天做的放最前）\n"
    "## ③ 现场/后续流程（办理时会发生什么：测试/缴费/周期）\n"
    "## 已知坑（每条带来源）\n"
    "## 来源（官方 URL 列表）\n"
    "规则：只写检索到的事实，关键事实后带直链；查不到的写「未查证，需官方确认」，"
    "绝不编造流程、金额、时限；物流语气、中文；全文 ≤"
    f"{MAX_BRIEF_LINES} 行。直接输出简报正文，不要输出其他解释。"
)

SEARCH_TOOLS = [{
    "type": "web_search",
    "web_search": {
        "enable": True,
        "search_engine": "search_std",
        "search_result": True,
        "count": 5,
    },
}]


# ---------------------------------------------------------------- 索引与键

def clean_task_text(raw: str) -> str:
    """任务键的文本部分：剥 checkbox 壳与空白（状态翻转换不掉键）。"""
    s = raw.strip()
    s = re.sub(r"^[-*]\s+\[[ xX]\]\s*", "", s)
    return s.strip()


def task_key(file: str, raw: str) -> str:
    return hashlib.sha1(f"{file}|{clean_task_text(raw)}".encode("utf-8")).hexdigest()[:12]


def load_index(root: Path) -> dict:
    path = root / BRIEFS_DIR / "index.json"
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def save_index(root: Path, index: dict) -> None:
    path = root / BRIEFS_DIR / "index.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(index, ensure_ascii=False, indent=1),
                    encoding="utf-8")


def brief_path(root: Path, key: str, index: dict) -> Path | None:
    """该任务已有简报则返回路径，否则 None。"""
    meta = index.get(key)
    if meta and meta.get("file"):
        return root / BRIEFS_DIR / meta["file"]
    return None


# ---------------------------------------------------------------- 触发

def is_candidate(entry, today: dt.date, horizon_days: int = 7) -> bool:
    """⭐ 或 7 天窗口内 + 外部流程启发式 / #简报 标记 + 未完成。"""
    if entry.done is True:
        return False
    in_window = entry.star or any(
        today <= d <= today + dt.timedelta(days=horizon_days) for d in entry.dates)
    if not in_window:
        return False
    if MANUAL_TAG in entry.raw:
        return True
    low = entry.raw.lower()
    return any(k in low for k in TRIGGER_KEYWORDS)


def slugify(text: str, taken: set[str]) -> str:
    from distill import rule_na
    base_src = rule_na(clean_task_text(text))          # 蒸馏出动作段再做文件名
    base_src = re.sub(r"^[\d.／/\-–—]+\s*", "", base_src)  # 剥行首日期
    base = re.sub(r"[^\w\u4e00-\u9fff]+", "", base_src)[:20] or "brief"
    slug, n = base, 2
    while slug in taken:
        slug = f"{base}-{n}"
        n += 1
    taken.add(slug)
    return slug


# ---------------------------------------------------------------- 生成

def _strip_fences(text: str) -> str:
    m = re.search(r"```(?:markdown)?\s*\n(.*)\n```\s*$", text, re.S)
    return (m.group(1) if m else text).strip()


def _extract_links(data: dict) -> list[tuple[str, str]]:
    """从响应数据里收集 (标题, 链接)——web_search 的引用在响应数据里，不在正文。"""
    out: list[tuple[str, str]] = []
    seen: set[str] = set()

    def walk(node):
        if isinstance(node, dict):
            link = node.get("link") or node.get("url")
            if isinstance(link, str) and link.startswith("http") and link not in seen:
                seen.add(link)
                out.append((str(node.get("title") or link), link))
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(data)
    return out


def generate_brief(entry, root: Path, cfg: dict, api_key: str | None,
                   usage_path: Path, now: dt.datetime, slug: str) -> str | None:
    """生成并落盘一份简报；返回文件名，失败/不可信返回 None。"""
    if not api_key:
        return None
    dates = "、".join(d.strftime("%m/%d") for d in entry.dates) or "无"
    content, data = call_glm(
        cfg,
        [{"role": "system", "content": BRIEF_SYSTEM},
         {"role": "user", "content":
          f"任务：{clean_task_text(entry.raw)}\n"
          f"来源文件：{entry.file}\n相关日期：{dates}\n"
          f"今天是 {now:%Y-%m-%d}。生成简报正文。"
          "（必须先使用 web_search 检索官方信息再作答，至少检索一次；"
          "正文中的关键事实引用检索到的真实链接。）"}],
        api_key=api_key, usage_path=usage_path, now=now,
        max_tokens=1500, temperature=0.2, timeout=90, label="brief",
        tools=SEARCH_TOOLS, return_full=True)
    if not content:
        return None
    content = _strip_fences(content)
    links = _extract_links(data or {})
    # 信任闸：正文无 URL 且响应无检索结果 = 没有联网证据，不落盘（宁可缺不编造）
    if "http" not in content and not links:
        print(f"[briefs] guard: no citation (head={content[:60]!r}), dropping",
              file=sys.stderr)
        return None
    if links and "http" not in content:
        # 正文引用是 [来源：ref_N] 式的 → 把真实链接补成来源区
        src = "\n".join(f"- [{t}]({u})" for t, u in links[:8])
        content += f"\n\n## 来源（检索结果）\n{src}\n"
    rel = f"{BRIEFS_DIR}/{slug}.md"
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content + "\n", encoding="utf-8")
    # 检索计费（search_std ≈¥0.01/次）单独记一笔，预算帽可见
    price = float(cfg.get("search_price_per_call_usd", 0.002))
    with usage_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps({"month": now.strftime("%Y-%m"),
                            "ts": now.isoformat(timespec="seconds"),
                            "model": "web-search-std", "est_cost_usd": price},
                           ensure_ascii=False) + "\n")
    return f"{slug}.md"


def run(root: Path, cfg: dict, api_key: str | None, usage_path: Path,
        now: dt.datetime, max_new: int = MAX_NEW_PER_NIGHT,
        force: bool = False) -> list[str]:
    """nightly 主流程：挑候选 → 查索引 → 生成 → 记账。返回新生成的文件列表。"""
    snap = Snapshot.load(root, cfg, now)
    index = load_index(root)
    today = snap.today
    taken = {meta.get("file", "").removesuffix(".md")
             for meta in index.values() if meta.get("file")}

    candidates = [e for e in snap.entries if is_candidate(e, today, int(cfg.get("horizon_days", 7)))]
    # ⭐ 优先，其次最近死线
    candidates.sort(key=lambda e: (not e.star, min(e.dates) if e.dates else today + dt.timedelta(days=999)))

    generated: list[str] = []
    for e in candidates:
        if len(generated) >= max_new:
            break
        key = task_key(e.file, e.raw)
        meta = index.get(key)
        if meta and not force:
            fresh = meta.get("dates") == sorted(d.isoformat() for d in e.dates)
            age = (now - dt.datetime.fromisoformat(meta["ts"])).days \
                if meta.get("ts") else BRIEF_TTL_DAYS
            if fresh and age < BRIEF_TTL_DAYS:
                continue
        slug = slugify(clean_task_text(e.raw), taken)
        fname = generate_brief(e, root, cfg, api_key, usage_path, now, slug)
        if fname:
            index[key] = {"file": fname, "ts": now.isoformat(timespec="seconds"),
                          "dates": sorted(d.isoformat() for d in e.dates)}
            generated.append(fname)
    if generated:
        save_index(root, index)
    print(f"[ok] briefs: {len(generated)} generated, "
          f"{len(candidates)} candidates, {len(index)} indexed")
    return generated


def main() -> int:
    ap = argparse.ArgumentParser(description="task brief generator")
    ap.add_argument("--vault", required=True)
    ap.add_argument("--force", action="store_true",
                    help="忽略索引新鲜度强制重查")
    args = ap.parse_args()
    root = Path(args.vault).resolve()
    cfg = load_config(root)
    now = dt.datetime.now(ZoneInfo(cfg["timezone"]))
    usage = root / "reports" / "usage.json"
    run(root, cfg, os.environ.get("GLM_API_KEY"), usage, now, force=args.force)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
