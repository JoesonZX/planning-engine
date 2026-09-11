#!/usr/bin/env python3
"""deadlines.ics 生成器：vault 的 ⭐ 硬节点与未来日期 → 全天事件日历。

每晚随晚间报告运行；前端/手机日历订阅或导入即得系统级提醒。
RFC 5545 合规：CRLF、75 字节折行、文本转义。
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
from pathlib import Path

from parser import load_config
from vault import Snapshot

PAST_DAYS = 7
FUTURE_DAYS = 120


def escape_ics(text: str) -> str:
    """RFC 5545 §3.3.11 文本转义。"""
    return (text.replace("\\", "\\\\")
                .replace(";", "\\;")
                .replace(",", "\\,")
                .replace("\n", "\\n"))


def fold_line(line: str) -> list[str]:
    """75 字节折行（续行前缀空格）。不在多字节字符中间切断。"""
    raw = line.encode("utf-8")
    if len(raw) <= 75:
        return [line]
    out: list[str] = []
    first = True
    i = 0
    while i < len(raw):
        limit = 75 if first else 74
        chunk_end = min(i + limit, len(raw))
        # 回退到 UTF-8 字符边界
        while chunk_end < len(raw) and (raw[chunk_end] & 0xC0) == 0x80:
            chunk_end -= 1
        piece = raw[i:chunk_end].decode("utf-8", "replace")
        out.append(piece if first else " " + piece)
        first = False
        i = chunk_end
    return out


def clean_text(text: str) -> str:
    """给日历用的单行摘要：去掉 markdown 记号、压缩空白、截断。"""
    import re
    t = re.sub(r"^\s*[-*]\s*\[[ xX]\]\s*", "", text)  # checkbox 壳
    t = re.sub(r"[*_`>|#]+", "", t)
    t = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", t)  # 链接留文字
    t = re.sub(r"\s+", " ", t).strip()
    return t[:90]


def build_vevent(d: dt.date, text: str, star: bool, file: str, line: int,
                 now_utc: dt.datetime) -> list[str]:
    key = hashlib.md5(f"{file}:{line}:{d.isoformat()}".encode()).hexdigest()
    uid = f"{key}@planning.joesonzx"
    summary = ("⭐ " if star else "") + clean_text(text)
    dtstamp = now_utc.strftime("%Y%m%dT%H%M%SZ")
    ymd = d.strftime("%Y%m%d")
    lines = [
        "BEGIN:VEVENT",
        f"UID:{uid}",
        f"DTSTAMP:{dtstamp}",
        f"DTSTART;VALUE=DATE:{ymd}",
        f"DTEND;VALUE=DATE:{(d + dt.timedelta(days=1)).strftime('%Y%m%d')}",
        f"SUMMARY:{escape_ics(summary)}",
        f"DESCRIPTION:{escape_ics(clean_text(file))}",
        "BEGIN:VALARM",
        "ACTION:DISPLAY",
        f"TRIGGER:-P1D",
        f"DESCRIPTION:{escape_ics(summary)}",
        "END:VALARM",
        "END:VEVENT",
    ]
    return lines


def render_ics(snap: Snapshot, now_utc: dt.datetime) -> bytes:
    entries = snap.entries
    today = snap.today

    lo = today - dt.timedelta(days=PAST_DAYS)
    hi = today + dt.timedelta(days=FUTURE_DAYS)

    out: list[str] = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//planning-app//deadlines//CN",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        "X-WR-CALNAME:规划死线",
    ]
    seen: set[str] = set()
    for e in entries:
        if not (e.dates or e.star):
            continue
        # 硬节点无日期 → 挂在今天提醒；有日期的按日期
        dates = e.dates or [today]
        for d in dates:
            if not (lo <= d <= hi):
                continue
            key = f"{e.file}:{e.line}:{d.isoformat()}"
            if key in seen:
                continue
            seen.add(key)
            out.extend(build_vevent(d, e.text, e.star, e.file, e.line, now_utc))
    out.append("END:VCALENDAR")

    # CRLF + 折行
    physical: list[str] = []
    for line in out:
        physical.extend(fold_line(line))
    return ("\r\n".join(physical) + "\r\n").encode("utf-8")


def generate_ics(root: Path, cfg: dict, today: dt.date,
                 now_utc: dt.datetime) -> bytes:
    """兼容入口（test_ics/golden/外部调用）；today 由调用方注入。"""
    snap = Snapshot.load(root, cfg)
    snap.today = today
    return render_ics(snap, now_utc)


def main() -> int:
    ap = argparse.ArgumentParser(description="deadlines.ics generator")
    ap.add_argument("--vault", required=True)
    ap.add_argument("--out", default="reports/deadlines.ics")
    args = ap.parse_args()
    root = Path(args.vault).resolve()
    cfg = load_config(root)
    now_utc = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)

    data = render_ics(Snapshot.load(root, cfg), now_utc)
    out_path = root / args.out
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if out_path.exists() and out_path.read_bytes() == data:
        print("[ok] ics unchanged")
        return 0
    out_path.write_bytes(data)
    print(f"[ok] wrote {out_path} ({len(data)} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
