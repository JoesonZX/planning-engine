#!/usr/bin/env python3
"""golden fixture：合成 vault + 可复现的 git 历史（绝不放真实数据）。

日期全部相对运行时的"今天"生成，git 提交时间用环境变量定死在相对偏移上，
使解析结果（today+1 的死线、30 天前的陈旧文件）在每次构建 fixture 时保持
相同的相对形态。测试内 now = 真实 now，因此快照与真实运行同构。
"""

from __future__ import annotations

import datetime as dt
import subprocess
import tempfile
from pathlib import Path

USER = ("joeson", "joeson@example.com")
BOT = ("planning-bot", "planning-bot@users.noreply.github.com")


def _git(root: Path, *args: str, when: dt.datetime | None = None,
         who: tuple[str, str] = USER) -> None:
    env_extra = {}
    if when is not None:
        stamp = when.strftime("%Y-%m-%dT%H:%M:%S +0000")
        env_extra["GIT_AUTHOR_DATE"] = stamp
        env_extra["GIT_COMMITTER_DATE"] = stamp
    import os
    env = dict(os.environ)
    env.update(env_extra)
    subprocess.run(
        ["git", "-C", str(root), "-c", f"user.name={who[0]}",
         "-c", f"user.email={who[1]}", *args],
        check=True, capture_output=True, env=env)


def build_fixture() -> tuple[Path, dt.datetime]:
    """构建合成 vault，返回 (root, now)。每处日期的相对偏移都有注释。"""
    tmp = Path(tempfile.mkdtemp(prefix="golden-vault-"))
    now = dt.datetime.now(dt.timezone.utc)
    today = now.date()
    d = lambda days: (today + dt.timedelta(days=days)).strftime("%m/%d")  # noqa: E731

    (tmp / "规划").mkdir()
    (tmp / "reports").mkdir()

    # 主清单：覆盖冻结语法全部形态 + 已知解析噪音
    (tmp / "main.md").write_text(f"""# 主清单

## 近期
- [ ] 明天的事（{d(1)} 之前）
- [ ] ⭐⭐缴费截止 {d(3)}
- [x] 已完成的事
- [ ] 无日期悬空任务
| 时间 | 事项 |
| --- | --- |
| 9:30 | 表格行任务 {d(2)} |
| 11:00 | 表格行无日期 |

版本噪音：v1.29 与 Python 3.12 与论文 2401.05459 与 GLM-5.3-Flash 都不是日期
范围：{d(2)}-{d(5)} 是一个日期范围
""", encoding="utf-8")

    (tmp / "规划" / "term.md").write_text(f"""# 学期
- [ ] 开学 {d(1)}
- [ ] 期末 {d(12)}
""", encoding="utf-8")

    stale_body = f"""# 陈旧文件
- [ ] 陈旧无日期任务一
- [ ] 陈旧无日期任务二
- [ ] 陈旧带日期任务（{d(-20)}）
"""
    (tmp / "stale.md").write_text(stale_body, encoding="utf-8")

    (tmp / "skipme.md").write_text(f"- [ ] skip_files 内不应出现 {d(1)}\n",
                                    encoding="utf-8")
    (tmp / "仪表盘.md").write_text(f"# 仪表盘\n\n- [ ] 生成物里的假任务 {d(1)}\n",
                                    encoding="utf-8")
    (tmp / "profile.md").write_text(f"""# profile · 用户画像

<!-- user-start -->
- 物流模式（手写区）
<!-- user-end -->

## 修订记录
- 修订 {d(-3)} 初版（生成物，不应进死线报告）
""", encoding="utf-8")
    (tmp / "inbox.md").write_text("# inbox\n\n- [ ] inbox 条目一\n⏳ 待人工：inbox 条目二\n",
                                   encoding="utf-8")

    (tmp / ".agent-config.yml").write_text("""timezone: America/Los_Angeles
horizon_days: 7
stale_days: 14
skip_files:
  - skipme.md
triage_targets:
  - 规划/term.md
timeline:
  - ["08:30", "上午 · 课业"]
  - ["13:00", "下午 · 科研"]
""", encoding="utf-8")

    (tmp / "reports" / "usage.json").write_text("", encoding="utf-8")
    (tmp / "reports" / "triage-latest.json").write_text(
        '{"ts": "2026-01-01T00:00:00", "classified": 2, "held": 1}',
        encoding="utf-8")

    # git 历史（顺序敏感：git --since 按 commit-date 序遍历，HEAD 太旧会剪枝，
    # 所以最后的提交必须日期最新）：
    # A 30 天前初建（陈旧文件自此未动）→ B 10 天前勾选（上周完成）
    # → C 3 天前新增任务 → E 30 小时前 bot 提交（统计排除；避开 24h 边界）
    # → D 2 小时前勾选（24h 完成 + 本周完成，HEAD）
    _git(tmp, "init", "-q", "-b", "main")
    _git(tmp, "add", "-A", when=now - dt.timedelta(days=30))
    _git(tmp, "commit", "-q", "-m", "initial", when=now - dt.timedelta(days=30))
    (tmp / "规划" / "term.md").write_text(
        (tmp / "规划" / "term.md").read_text(encoding="utf-8")
        + "- [x] 上周完成的事\n", encoding="utf-8")
    _git(tmp, "add", "-A", when=now - dt.timedelta(days=10))
    _git(tmp, "commit", "-q", "-m", "last week done", when=now - dt.timedelta(days=10))
    (tmp / "main.md").write_text((tmp / "main.md").read_text(encoding="utf-8")
                                 + f"- [ ] 三天前新增的任务 {d(4)}\n", encoding="utf-8")
    _git(tmp, "add", "-A", when=now - dt.timedelta(days=3))
    _git(tmp, "commit", "-q", "-m", "add tasks", when=now - dt.timedelta(days=3))
    (tmp / "仪表盘.md").write_text(
        (tmp / "仪表盘.md").read_text(encoding="utf-8") + "- [x] bot 勾选行\n",
        encoding="utf-8")
    _git(tmp, "add", "-A", when=now - dt.timedelta(hours=30), who=BOT)
    _git(tmp, "commit", "-q", "-m", "bot commit", when=now - dt.timedelta(hours=30),
         who=BOT)
    (tmp / "main.md").write_text((tmp / "main.md").read_text(encoding="utf-8")
                                 + "- [x] 今日完成的事\n", encoding="utf-8")
    _git(tmp, "add", "-A", when=now - dt.timedelta(hours=2))
    _git(tmp, "commit", "-q", "-m", "done today", when=now - dt.timedelta(hours=2))
    return tmp, dt.datetime.now(dt.timezone.utc).astimezone()
