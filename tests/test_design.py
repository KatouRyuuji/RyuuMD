# -*- coding: utf-8 -*-
"""运行时样式与设计源 tokens/palettes 对齐（色值从源文件解析，不另写色表）。"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_TMP_APPDATA = tempfile.TemporaryDirectory(prefix="ryuumd-test-design-appdata-")
os.environ["APPDATA"] = _TMP_APPDATA.name

from app.core.palette_css import duration_map, load_export_palettes  # noqa: E402

SOURCE = Path(r"..\..\RyuujiDesign\styles")
VENDOR = ROOT / "app" / "web" / "vendor" / "ryuuji" / "styles"
SYS_TOKENS = ROOT / "app" / "web" / "css" / "sys-tokens.css"

EXPECTED_DURS = {
    "--sys-dur-1": "100ms",
    "--sys-dur-2": "180ms",
    "--sys-dur-3": "240ms",
    "--sys-dur-4": "320ms",
    "--sys-dur-5": "400ms",
}


class TestDesignAlign(unittest.TestCase):
    def test_source_readable(self):
        self.assertTrue((SOURCE / "palettes.css").is_file())
        self.assertTrue((SOURCE / "tokens.css").is_file())

    def test_palettes_runtime_matches_source_primary(self):
        src = load_export_palettes(str(SOURCE / "palettes.css"))
        app = load_export_palettes(str(VENDOR / "palettes.css"))
        self.assertIn("a1", src)
        self.assertIn("a1", app)
        for pal in ("a1", "a6"):
            for theme in ("light", "dark"):
                self.assertEqual(
                    src[pal][theme]["primary"],
                    app[pal][theme]["primary"],
                    f"{pal}/{theme} primary",
                )
                self.assertEqual(src[pal][theme]["bg"], app[pal][theme]["bg"])

    def test_duration_tokens_match_source(self):
        src = duration_map((SOURCE / "tokens.css").read_text(encoding="utf-8"))
        vendor = duration_map((VENDOR / "tokens.css").read_text(encoding="utf-8"))
        sys_tok = duration_map(SYS_TOKENS.read_text(encoding="utf-8"))
        self.assertEqual(src, EXPECTED_DURS)
        self.assertEqual(vendor, src)
        self.assertEqual(sys_tok, src)

    def test_hover_lift_is_one_px(self):
        motion = (VENDOR / "motion.css").read_text(encoding="utf-8")
        self.assertIn("translateY(-1px)", motion)
        self.assertIn("data-reduced-motion", motion)


if __name__ == "__main__":
    unittest.main(verbosity=2)
