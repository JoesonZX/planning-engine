#!/usr/bin/env python3
"""golden 公共管线：构建 fixture → 跑全部产出物 → 规范化时间戳。

capture_golden.py（写快照）与 test_golden.py（比对快照）共用本文件。
规范化只抹去四类必然随时间漂移的字段：报告头时间、generated_at、
DTSTAMP、文件年龄天数（浮点 .0f 取整的边界抖动）。
"""

from __future__ import annotations

import datetime as dt
import json
import re
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from golden_vault import build_fixture  # noqa: E402


def collect_outputs(root: Path) -> dict[str, str]:
    from ics import generate_ics
    from report import build_dashboard, build_report
    from state import build_state, build_stats
    from weekly_review import build_weekly

    cfg = load_config(root)
    now = dt.datetime.now(ZoneInfo(cfg["timezone"]))
    now_utc = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
    return {
        "report.md": build_report(root, cfg, now),
        "dashboard.md": build_dashboard(root, cfg, now),
        "state.json": json.dumps(build_state(root, cfg, now),
                                 ensure_ascii=False, indent=1),
        "stats.json": json.dumps(build_stats(root, cfg, now), ensure_ascii=False),
        "deadlines.ics": generate_ics(root, cfg, now.date(), now_utc).decode("utf-8"),
        "weekly.md": build_weekly(root, cfg, now)[0],
    }


def load_config(root: Path):
    from parser import load_config as _load
    return _load(root)


def normalize(text: str) -> str:
    text = re.sub(r"生成于 \d{4}-\d{2}-\d{2} \d{2}:\d{2}", "生成于 <TS>", text)
    text = re.sub(r"仪表盘 · .*", "仪表盘 · <NOW>", text)  # 头部带当时时分
    text = re.sub(r'"generated_at": "[^"]+"', '"generated_at": "<TS>"', text)
    text = re.sub(r"DTSTAMP:\d{8}T\d{6}Z", "DTSTAMP:<TS>", text)
    text = re.sub(r"\d+ 天未动", "N 天未动", text)
    return text
