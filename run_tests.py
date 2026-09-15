# -*- coding: utf-8 -*-
"""RyuuMD 测试统一入口。

用法:
  python run_tests.py              # 单测 + E2E
  python run_tests.py --unit       # 仅单测（打包门禁）
  python run_tests.py --unit --quiet
退出码: 0 = 全部通过; 1 = 任一失败。
测试计划与手动验证清单见 docs/TEST_PLAN.md。
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent

UNIT_MODULES = [
    "tests.test_api",
    "tests.test_cloud",
    "tests.test_search",
    "tests.test_fonts",
    "tests.test_ai",
    "tests.test_workdir",
    "tests.test_design",
    "tests.test_adv",
    "tests.test_im",
    "tests.test_perf",
]


def run(title: str, cmd: list[str]) -> int:
    print("=" * 60)
    print(title)
    print("=" * 60, flush=True)
    return subprocess.call(cmd, cwd=str(ROOT))


def main() -> int:
    argv = sys.argv[1:]
    unit_only = "--unit" in argv
    quiet = "--quiet" in argv or "-q" in argv
    flag = "-q" if quiet else "-v"
    unit_cmd = [sys.executable, "-m", "unittest", *UNIT_MODULES, flag]
    rc1 = run("Python API 单元测试", unit_cmd)
    if unit_only:
        print(f"\n结果: 单测={'通过' if rc1 == 0 else '失败'}")
        return 0 if rc1 == 0 else 1
    rc2 = run("真实窗口 E2E 测试", [sys.executable, str(ROOT / "tests" / "test_e2e.py")])
    print("\n" + "=" * 60)
    print(f"结果: 单测={'通过' if rc1 == 0 else '失败'}  E2E={'通过' if rc2 == 0 else '失败'}")
    return 0 if rc1 == 0 and rc2 == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
