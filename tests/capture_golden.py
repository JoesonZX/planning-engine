#!/usr/bin/env python3
"""捕获 golden 快照：用**当前**代码跑一遍合成 vault，把输出写进 tests/golden/。

用途：重构前捕获、每步重构后跑 test_golden.py 比对——行为不变则全绿。
注意：快照含相对日期的绝对形态（如 today+1 的具体日期），只在捕获后
~48 小时内有效（test_golden 超龄自动 skip，不挡 CI）。

用法：python tests/capture_golden.py
"""

from __future__ import annotations

import datetime as dt
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from golden_common import collect_outputs, normalize  # noqa: E402
from golden_vault import build_fixture  # noqa: E402

GOLDEN_DIR = Path(__file__).resolve().parent / "golden"


def main() -> int:
    root, _ = build_fixture()
    outputs = {name: normalize(text) for name, text in collect_outputs(root).items()}
    GOLDEN_DIR.mkdir(exist_ok=True)
    for name, text in outputs.items():
        (GOLDEN_DIR / name).write_text(text, encoding="utf-8", newline="\n")
        print(f"[ok] golden {name} ({len(text)} chars)")
    (GOLDEN_DIR / "manifest.json").write_text(
        json.dumps({"captured_at": dt.datetime.now(dt.timezone.utc).isoformat()},
                   indent=1), encoding="utf-8")
    import shutil
    shutil.rmtree(root, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
