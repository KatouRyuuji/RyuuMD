# -*- coding: utf-8 -*-
"""发版版本号一致性检查（CI 门禁）。

版本号散落在三处，必须一致：
  - version_info.txt   Windows exe 版本资源（filevers / prodvers / FileVersion / ProductVersion）
  - RyuuMD-mac.spec     CFBundleShortVersionString / CFBundleVersion
  - packaging/RyuuMD.iss 从 exe 版本资源读取，无需单独维护

用法:
  python scripts/check_version.py              # 仅检查三处一致，打印版本号
  python scripts/check_version.py --tag v1.8.1 # 额外要求与 tag 一致（发版用）
  python scripts/check_version.py --print      # 只输出版本号（供 CI 取值）
退出码: 0 = 一致; 1 = 不一致或解析失败。
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def collect() -> dict[str, str]:
    found: dict[str, str] = {}
    vi = _read("version_info.txt")
    for key in ("filevers", "prodvers"):
        m = re.search(rf"{key}=\((\d+),\s*(\d+),\s*(\d+),\s*(\d+)\)", vi)
        if m:
            found[f"version_info.txt {key}"] = ".".join(m.groups()[:3])
    for key in ("FileVersion", "ProductVersion"):
        m = re.search(rf"StringStruct\('{key}',\s*'(\d+)\.(\d+)\.(\d+)(?:\.\d+)?'\)", vi)
        if m:
            found[f"version_info.txt {key}"] = ".".join(m.groups())
    spec = _read("RyuuMD-mac.spec")
    for key in ("CFBundleShortVersionString", "CFBundleVersion"):
        m = re.search(rf'"{key}":\s*"(\d+\.\d+\.\d+)"', spec)
        if m:
            found[f"RyuuMD-mac.spec {key}"] = m.group(1)
    return found


def main() -> int:
    argv = sys.argv[1:]
    found = collect()
    expected = 6
    if len(found) != expected:
        print(f"[错误] 只解析到 {len(found)}/{expected} 处版本号，格式可能被改动：", file=sys.stderr)
        for k, v in found.items():
            print(f"  {k} = {v}", file=sys.stderr)
        return 1
    versions = set(found.values())
    if len(versions) != 1:
        print("[错误] 版本号不一致：", file=sys.stderr)
        for k, v in found.items():
            print(f"  {k} = {v}", file=sys.stderr)
        return 1
    version = versions.pop()
    if "--tag" in argv:
        i = argv.index("--tag")
        tag = argv[i + 1] if i + 1 < len(argv) else ""
        tag = tag.rsplit("/", 1)[-1]
        if tag.lstrip("v") != version:
            print(f"[错误] tag {tag!r} 与代码版本 {version} 不一致，请先同步版本号再打 tag", file=sys.stderr)
            return 1
    if "--print" in argv:
        print(version)
    else:
        print(f"版本号一致: {version}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
