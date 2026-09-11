#!/usr/bin/env python3
"""条目蒸馏：>60 字的任务行在展示层收敛为「下一步动作」（v5 P2）。

只发生在展示层——源文件永远保留原文。规则优先、宁长勿缺：
切不出干净的头部就保持原样，绝不为了短而丢信息。
"""

from __future__ import annotations

MAX_LEN = 60
MIN_HEAD = 6
_TRAILING = "；;、—–-，,。 "


def rule_na(text: str, max_len: int = MAX_LEN) -> str:
    """任务行的展示形态：优先取「——」前的动作段，括号悬空则回退到首括号前。"""
    if len(text) <= max_len:
        return text
    head = text.split("——")[0].rstrip()
    # 切分悬空的括号尾巴逐层剥掉
    while head.count("（") > head.count("）"):
        head = head.rsplit("（", 1)[0].rstrip()
    while head and head[-1] in _TRAILING:
        head = head[:-1].rstrip()
    if MIN_HEAD <= len(head) <= max_len:
        return head
    # 回退：截到第一个括号前（「任务（细节……）」形态）
    head2 = text.split("（")[0].rstrip().rstrip(_TRAILING)
    if MIN_HEAD <= len(head2) <= max_len:
        return head2
    return text  # 切不出干净的头部就保持原文
