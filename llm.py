#!/usr/bin/env python3
"""LLM 调用层：全引擎唯一的 GLM 请求/记账/容错实现。

预算帽检查留在各调用方（规则不同：晚报对周日豁免，周报/分拣/画像一刀切），
本模块只负责：构造请求 → 调用 → usage.json 记账 → 失败返回 None。
"""

from __future__ import annotations

import datetime as dt
import json
import sys
import urllib.request
from pathlib import Path

GLM_URL = "https://open.bigmodel.cn/api/paas/v4/chat/completions"


def month_spend(usage_path: Path, now: dt.datetime) -> float:
    """usage.json（jsonl）里当前月的预估花费合计。"""
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


def _record_usage(usage: dict, cfg: dict, usage_path: Path,
                  now: dt.datetime) -> None:
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


def _collect_links(node, out: list) -> None:
    """递归收集响应里所有 link/url 字段（web_search 结果的确切位置随版本变）。"""
    if isinstance(node, dict):
        for k, v in node.items():
            if k in ("link", "url") and isinstance(v, str) and v.startswith("http"):
                out.append(v)
            else:
                _collect_links(v, out)
    elif isinstance(node, list):
        for v in node:
            _collect_links(v, out)


def call_glm(cfg: dict, messages: list[dict], *, api_key: str | None,
             usage_path: Path, now: dt.datetime, max_tokens: int,
             temperature: float, timeout: int = 45, label: str = "glm",
             tools: list[dict] | None = None,
             return_full: bool = False):
    """单轮补全。失败/无 key 返回 None（确定性产物不受影响）。

    tools：可选的服务端工具（web_search 联网检索，见 briefs.py）。
    return_full=True 时返回 (content, 完整响应 dict)——调用方需要
    search_result 引用列表时用（引用在响应数据里，不在正文中）。
    """
    if not api_key:
        return (None, None) if return_full else None
    payload: dict = {
        "model": cfg.get("model", "glm-4-flash"),
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if tools:
        payload["tools"] = tools
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        GLM_URL, data=body, method="POST",
        headers={"Authorization": f"Bearer {api_key}",
                 "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        content = data["choices"][0]["message"]["content"].strip()
        _record_usage(data.get("usage", {}), cfg, usage_path, now)
        return (content, data) if return_full else content
    except Exception as exc:  # noqa: BLE001
        print(f"[warn] {label} skipped: {exc}", file=sys.stderr)
        return (None, None) if return_full else None
