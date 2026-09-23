# -*- coding: utf-8 -*-
"""在 5 个配色 × 2 种明暗主题下生成真实窗口界面截图。"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SHOT_SCRIPT = ROOT / "tests" / "ui_shots.py"
PALETTES = ("a1", "a3", "a4", "a5", "a6")
THEMES = ("light", "dark")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True, type=Path, help="截图输出目录")
    parser.add_argument("--preset", action="append", default=[], help="仅运行指定预设，例如 a1-light")
    args = parser.parse_args()
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    logs = out / "logs"
    logs.mkdir(exist_ok=True)

    requested = set(args.preset)
    presets = [f"{palette}-{theme}" for palette in PALETTES for theme in THEMES]
    if requested:
        unknown = requested - set(presets)
        if unknown:
            parser.error("未知预设: " + ", ".join(sorted(unknown)))
        presets = [preset for preset in presets if preset in requested]

    manifest = {"palettes": list(PALETTES), "themes": list(THEMES), "presets": []}
    failures: list[str] = []
    for preset in presets:
        palette, theme = preset.split("-", 1)
        env = os.environ.copy()
        env["RYUUMD_UI_SHOT_OUT"] = str(out)
        env["RYUUMD_UI_SHOT_PALETTE"] = palette
        env["RYUUMD_UI_SHOT_THEME"] = theme
        result = subprocess.run(
            [sys.executable, str(SHOT_SCRIPT)],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            timeout=300,
        )
        log = result.stdout + ("\n--- stderr ---\n" + result.stderr if result.stderr else "")
        (logs / f"{preset}.log").write_text(log, encoding="utf-8")
        names = sorted(p.name for p in out.glob(f"{preset}-*.png"))
        case_failures = [line for line in result.stdout.splitlines() if line.startswith("FAIL ")]
        ok = result.returncode == 0 and not case_failures and bool(names)
        manifest["presets"].append({
            "palette": palette,
            "theme": theme,
            "passed": ok,
            "screenshots": names,
            "count": len(names),
        })
        print(("PASS " if ok else "FAIL ") + f"{preset}: {len(names)} screenshots", flush=True)
        failures.extend(f"{preset}: {line}" for line in case_failures)
        if result.returncode != 0:
            failures.append(f"{preset}: process exit {result.returncode}")

    (out / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    total = sum(item["count"] for item in manifest["presets"])
    print(f"Total: {total} screenshots across {len(manifest['presets'])} theme presets")
    if failures:
        print("\n".join(failures), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
