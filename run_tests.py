# -*- coding: utf-8 -*-
"""RyuuMD 测试统一入口:先跑 Python API 单元测试,再跑真实窗口 E2E。

用法: python run_tests.py
退出码: 0 = 全部通过; 1 = 任一失败。
测试计划与手动验证清单见 docs/TEST_PLAN.md。
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def run(title: str, cmd: list[str]) -> int:
    print("=" * 60)
    print(title)
    print("=" * 60, flush=True)
    return subprocess.call(cmd, cwd=str(ROOT))


def main() -> int:
    rc1 = run("[1/2] Python API 单元测试", [sys.executable, "-m", "unittest", "tests.test_api", "tests.test_cloud", "tests.test_search", "tests.test_fonts", "-v"])
    rc2 = run("[2/2] 真实窗口 E2E 测试", [sys.executable, str(ROOT / "tests" / "test_e2e.py")])
    print("\n" + "=" * 60)
    print(f"结果: 单测={'通过' if rc1 == 0 else '失败'}  E2E={'通过' if rc2 == 0 else '失败'}")
    return 0 if rc1 == 0 and rc2 == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
