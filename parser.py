#!/usr/bin/env python3
"""规划 vault 最小语法解析器（冻结语法，见《规划系统设计蓝图》）。

只认三样东西：
1. checkbox      : 行首(可缩进) `- [ ]` / `- [x]`
2. 行内日期       : M/D 或 M.D（含边界防御，避免把小数/版本号当天日期）
3. ⭐ 硬节点标记   : 行内出现字面 `⭐`

其余一切当纯文本。零第三方依赖（标准库 only）。
"""

from __future__ import annotations

import calendar
import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

# 日期主体：M/D 或 M.D；左右边界都不是数字或点
_DATE_CORE = re.compile(r"(?<![\d.])(\d{1,2})[./](\d{1,2})(?![\d.])")
# 时间 8:30 之类不匹配（冒号不在分隔符里），无需处理
_CHECKBOX = re.compile(r"^\s*[-*]\s+\[([ xX])\]\s*(.*)$")
_HEADING = re.compile(r"^(#{1,6})\s+(.*)$")
_ASCII_LETTER = re.compile(r"[A-Za-z]")

MONTHS = 12
DAYS_MAX = 31


@dataclass
class Entry:
    """vault 中一行有信息量的内容（有 checkbox / 日期 / ⭐ 之一）。"""

    file: str  # 相对 vault 根的路径
    line: int  # 1-based 行号
    heading: str  # 所在小节标题（无则空串）
    text: str  # 行内容（去除 checkbox 标记，截断展示用）
    raw: str  # 原始行（去首尾空白）
    done: bool | None  # 仅 checkbox 行有值
    star: bool
    dates: list[date] = field(default_factory=list)

    @property
    def display(self) -> str:
        prefix = "⭐" if self.star else ""
        # 短引用：只留文件 basename（去 .md）。小节名一律不带——截断的元数据
        # 在阅读界面看起来就是"内容不全"
        base = self.file.rsplit("/", 1)[-1]
        base = base[:-3] if base.endswith(".md") else base
        where = f"（{base}）"
        text = self.text
        # 截断可能切断 ** 对——落单的 ** 会在前端原样显示，补成偶数
        if text.count("**") % 2:
            text += "**"
        if text.count("`") % 2:
            text += "`"
        # 文件引用用斜体包裹：前端渲染为弱化样式
        return f"{prefix}{text} *{where}*".strip()


def _valid_month_day(month: int, day: int) -> bool:
    return 1 <= month <= MONTHS and 1 <= day <= DAYS_MAX


def pick_year(month: int, day: int, today: date, horizon: int = 200) -> date:
    """滚动年份：在 今年/明年/去年 三个候选里选离 today 最近的那个。

    vault 是滚动规划的（12 月的文件写在 12 月，1 月读仍是"今年"），
    用绝对距离而不是简单进位，保证任何月份写下时都解析回作者本意。
    """
    candidates = []
    for year in (today.year - 1, today.year, today.year + 1):
        last = calendar.monthrange(year, month)[1]
        candidates.append(date(year, month, min(day, last)))
    candidates.sort(key=lambda d: abs((d - today).days))
    nearest = candidates[0]
    # 超出视野的"最近日期"通常是解析噪音，返回它但调用方可用 horizon 过滤
    return nearest


def extract_dates(text: str, today: date, horizon: int = 200) -> list[date]:
    """提取行内日期，带回防御：

    - 左边界不能是数字/点/ASCII 字母（排除 2401.05459、v1.29）
    - 右边界不能是数字/点
    - 紧跟 ASCII 字母 → 拒（排除 2.5h、7.7k）
    - 紧跟连字符范围（8.28-9.8）→ 后半必须也是合法日期，否则整体拒（排除 GLM-5.3-Flash）
    - 超出 horizon 天的候选丢弃（噪音兜底）
    """
    out: list[date] = []
    for m in _DATE_CORE.finditer(text):
        month, day = int(m.group(1)), int(m.group(2))
        if not _valid_month_day(month, day):
            continue
        end = m.end()
        rest = text[end:]
        # 紧跟 ASCII 字母：单位后缀
        if rest and _ASCII_LETTER.match(rest[0]):
            continue
        # 连字符范围：后半必须是合法日期
        if rest[:1] in ("-", "–", "—"):
            after_dash = rest[1:]
            m2 = _DATE_CORE.match(after_dash)
            if not (m2 and _valid_month_day(int(m2.group(1)), int(m2.group(2)))):
                continue
        # 左边紧跟 ASCII 字母：版本号 v1.29 / GPT-4.0
        if m.start() > 0 and _ASCII_LETTER.match(text[m.start() - 1]):
            continue
        # 左边隔空格挨着 ASCII 字母结尾的词（Python 3.12 / Office 365）→ 版本号
        k = m.start() - 1
        while k >= 0 and text[k] == " ":
            k -= 1
        if k >= 0 and _ASCII_LETTER.match(text[k]):
            continue
        d = pick_year(month, day, today, horizon)
        if abs((d - today).days) > horizon:
            continue
        out.append(d)
    return sorted(set(out))


def parse_markdown(text: str, file: str = "", today: date | None = None) -> list[Entry]:
    """把一份 md 解析成 Entry 列表（只保留有 checkbox/日期/⭐ 的行）。"""
    today = today or date.today()
    entries: list[Entry] = []
    heading = ""
    for i, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.strip()
        if not line:
            continue
        hm = _HEADING.match(line)
        if hm:
            heading = hm.group(2).strip()
            continue
        cb = _CHECKBOX.match(line)
        if cb:
            done = cb.group(1).lower() == "x"
            content = cb.group(2).strip()
        else:
            done = None
            content = line

        has_star = "⭐" in line
        dates = extract_dates(content, today)

        # 展示降噪（在日期提取之后：日期格可以安全丢弃）
        if content.startswith("|"):
            cells = [c.strip() for c in content.strip().strip("|").split("|")]
            if len(cells) >= 2:
                rest = cells[1:] if extract_dates(cells[0], today) else cells
                content = " · ".join(c for c in rest if c)
        content = re.sub(r"^>\s*", "", content)

        if done is None and not dates and not has_star:
            continue
        # 不截断：报告是主阅读界面，内容必须完整（** 配平在 display 里做）
        entries.append(
            Entry(
                file=file,
                line=i,
                heading=heading,
                text=content,
                raw=line,
                done=done,
                star=has_star,
                dates=dates,
            )
        )
    return entries


def load_config(root: Path) -> dict:
    """读取 .agent-config.yml 的极简子集（key: value；列表支持 [a, b] 与 - 行）。

    不引 PyYAML，配置就是这几种形状，解析器 30 行搞定。
    """
    cfg: dict = {
        "timezone": "America/Los_Angeles",
        "report_hour": 21,
        "model": "glm-4-flash",
        "monthly_budget_usd": 3.0,
        "price_per_1k_usd": 0.0,
        "horizon_days": 7,
        "stale_days": 14,
        "skip_files": [],
        "skip_dirs": [".git", "reports", ".github"],
        "inbox_file": "inbox.md",
        "dashboard_file": "仪表盘.md",
        "profile_file": "profile.md",
        "weekly_hour": 20,
        "triage_targets": [],
    }
    path = root / ".agent-config.yml"
    if not path.exists():
        return cfg
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.rstrip()
        if not line or line.lstrip().startswith("#"):
            continue
        m = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)\s*:\s*(.*)$", line)
        if not m:
            continue
        key, val = m.group(1), m.group(2).strip()
        if key not in cfg:  # 未知键：按字面收下（向前兼容），列表形状单独处理
            if val.startswith("[") and val.endswith("]"):
                inner = val[1:-1].strip()
                cfg[key] = [v.strip().strip("\"'") for v in inner.split(",")] if inner else []
            elif val == "":
                cfg[key] = []
            else:
                cfg[key] = val.strip("\"'")
            continue
        if val.startswith("[") and val.endswith("]"):
            inner = val[1:-1].strip()
            cfg[key] = [v.strip().strip("\"'") for v in inner.split(",")] if inner else []
        elif val == "":
            cfg[key] = []  # block list，由后续 "- item" 行填充
        elif isinstance(cfg[key], list):
            cfg[key].append(val.strip("\"'"))
        elif isinstance(cfg[key], (int, float)) and not isinstance(cfg[key], bool):
            cfg[key] = type(cfg[key])(val)
        else:
            cfg[key] = val.strip("\"'")
        # block list 续行由下面 _extend_block 处理
    return _extend_block(path, cfg)


def _extend_block(path: Path, cfg: dict) -> dict:
    """支持 `- item` 形式的块列表（skip_files/skip_dirs 用）。"""
    current: str | None = None
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        m = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)\s*:\s*(.*)$", line)
        if m and m.group(1) in cfg:
            current = m.group(1) if m.group(2) == "" else None
            continue
        bm = re.match(r"^-\s+(.+)$", line)
        if bm and current and isinstance(cfg[current], list):
            cfg[current].append(bm.group(1).strip().strip("\"'"))
    return cfg


def generated_files(cfg: dict) -> set[str]:
    """引擎生成物集合：不参与解析，防止自我引用（画像修订记录的日期行会被当成任务）。

    新增生成物时只改这里——report/state/weekly/ics 全部从本函数取排除集。
    """
    return {cfg.get("inbox_file", "inbox.md"),
            cfg.get("dashboard_file", "仪表盘.md"),
            cfg.get("profile_file", "profile.md")}


def iter_vault_files(root: Path, cfg: dict) -> list[Path]:
    """列出参与解析的 md 文件：跳过 skip_dirs 目录与 skip_files 文件名。"""
    skip_dirs = set(cfg.get("skip_dirs", []))
    skip_files = set(cfg.get("skip_files", []))
    out: list[Path] = []
    for p in sorted(root.rglob("*.md")):
        rel = p.relative_to(root)
        if any(part in skip_dirs for part in rel.parts[:-1]):
            continue
        if p.name in skip_files or p.name.startswith("."):
            continue
        out.append(p)
    return out


def load_vault(root: Path, cfg: dict, today: date | None = None) -> list[Entry]:
    today = today or date.today()
    entries: list[Entry] = []
    for p in iter_vault_files(root, cfg):
        rel = p.relative_to(root).as_posix()
        try:
            text = p.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            text = p.read_text(encoding="utf-8", errors="replace")
        entries.extend(parse_markdown(text, file=rel, today=today))
    return entries
