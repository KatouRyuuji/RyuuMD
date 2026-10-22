"""发版版本号一致性检查（CI 门禁）。

packaging/version_info.txt 与 packaging/RyuuMD-mac.spec 的版本号必须一致；
--tag vX.Y.Z 额外要求与 tag 一致；--print 只输出版本号。"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def collect() -> dict[str, str]:
    found: dict[str, str] = {}
    vi = _read("packaging/version_info.txt")
    for key in ("filevers", "prodvers"):
        m = re.search(rf"{key}=\((\d+),\s*(\d+),\s*(\d+),\s*(\d+)\)", vi)
        if m:
            found[f"version_info.txt {key}"] = ".".join(m.groups()[:3])
    for key in ("FileVersion", "ProductVersion"):
        m = re.search(rf"StringStruct\('{key}',\s*'(\d+)\.(\d+)\.(\d+)(?:\.\d+)?'\)", vi)
        if m:
            found[f"version_info.txt {key}"] = ".".join(m.groups())
    spec = _read("packaging/RyuuMD-mac.spec")
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
