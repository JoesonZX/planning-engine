#!/usr/bin/env python3
"""Web Push 通知（标准 VAPID，零第三方服务）。

用法：
  notify.py --vault . --kind evening          # 晚报 3 行摘要
  notify.py --vault . --kind weekly           # 周报
  notify.py --vault . --kind failure --msg "workflow failed"

订阅端点存 reports/push-sub.json（app 写入）。无订阅时静默成功。
需环境变量 VAPID_PRIVATE_KEY；依赖 pywebpush（workflow 内 pip install）。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

TITLE = {"evening": "🌙 晚间报告已生成", "weekly": "📊 周复盘已生成",
         "failure": "⚠️ 规划系统任务失败", "test": "🔔 测试推送"}


def summary_from_report(path: Path, kind: str, max_lines: int = 3) -> str:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return ""
    lines = []
    for raw in text.splitlines():
        s = raw.strip()
        if not s or s.startswith("#") or s.startswith(">"):
            continue
        clean = s.lstrip("-* ").strip()
        if clean.startswith(("💡", "##")):
            continue
        if clean:
            lines.append(clean[:70])
        if len(lines) >= max_lines:
            break
    return "\n".join(lines)


def latest_week_report(root: Path) -> Path | None:
    weeks = sorted((root / "reports").glob("week-*.md"))
    return weeks[-1] if weeks else None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--vault", required=True)
    ap.add_argument("--kind", default="evening",
                    choices=["evening", "weekly", "failure", "test"])
    ap.add_argument("--msg", default="")
    args = ap.parse_args()
    root = Path(args.vault).resolve()

    sub_path = root / "reports" / "push-sub.json"
    if not sub_path.exists():
        print("[skip] no push subscriptions yet")
        return 0
    try:
        subs = json.loads(sub_path.read_text(encoding="utf-8"))
        subs = subs.get("subscriptions", subs if isinstance(subs, list) else [])
    except (json.JSONDecodeError, OSError) as e:
        print(f"[warn] bad push-sub.json: {e}", file=sys.stderr)
        return 0
    if not subs:
        print("[skip] empty push subscriptions")
        return 0

    private_key = os.environ.get("VAPID_PRIVATE_KEY", "")
    if not private_key:
        print("[warn] VAPID_PRIVATE_KEY missing", file=sys.stderr)
        return 0

    if args.kind == "weekly":
        week_file = latest_week_report(root)
        body = args.msg or (summary_from_report(week_file, args.kind) if week_file else "周复盘已生成")
    else:
        body = args.msg or summary_from_report(root / "reports/tomorrow.md", args.kind)

    try:
        from pywebpush import webpush, WebPushException
    except ImportError:
        print("[warn] pywebpush not installed", file=sys.stderr)
        return 0

    ok = fail = 0
    dead: list = []
    for sub in subs:
        try:
            webpush(
                subscription_info=sub,
                data=json.dumps({"title": TITLE.get(args.kind, "规划"),
                                 "body": body}, ensure_ascii=False),
                vapid_private_key=private_key,
                vapid_claims={"sub": "mailto:planning@joesonzx.dev"},
            )
            ok += 1
        except WebPushException as e:
            fail += 1
            print(f"[warn] push failed: {e}", file=sys.stderr)
            if getattr(e, "response", None) is not None and \
               e.response.status_code in (404, 410):
                dead.append(sub)  # 订阅已过期，清理

    if dead and subs:
        kept = [s for s in subs if s not in dead]
        sub_path.write_text(json.dumps({"subscriptions": kept}), encoding="utf-8")
        print(f"[ok] pruned {len(dead)} dead subscriptions")
    print(f"[ok] push sent: {ok} ok, {fail} failed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
