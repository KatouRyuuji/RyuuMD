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

# 设计源 RyuujiDesign 的 styles 目录：RYUUJI_DESIGN_STYLES 指定，缺省取工作区中的相邻仓库；
# 不存在（如 CI）时源对齐用例跳过，vendor 自身用例照常
SOURCE = Path(os.environ.get("RYUUJI_DESIGN_STYLES") or ROOT.parent.parent / "RyuujiDesign" / "styles")
_needs_source = unittest.skipUnless(SOURCE.is_dir(), "设计源仓库不存在")
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
    @_needs_source
    def test_source_readable(self):
        self.assertTrue((SOURCE / "palettes.css").is_file())
        self.assertTrue((SOURCE / "tokens.css").is_file())

    @_needs_source
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

    @_needs_source
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

    def test_chrome_background_follows_palette(self):
        from app.core.palette_css import chrome_background, load_export_palettes

        pals = load_export_palettes()
        self.assertEqual(chrome_background("a1", "light"), pals["a1"]["light"]["bg"])
        self.assertEqual(chrome_background("a6", "dark"), pals["a6"]["dark"]["bg"])
        self.assertNotEqual(pals["a1"]["light"]["bg"], pals["a6"]["dark"]["bg"])
        self.assertEqual(chrome_background("nope", "light"), pals["a1"]["light"]["bg"])

    def test_sys_bridge_maps_warning_danger(self):
        bridge = (ROOT / "app" / "web" / "css" / "sys-bridge.css").read_text(encoding="utf-8")
        self.assertIn("--warning: var(--sys-warning);", bridge)
        self.assertIn("--danger: var(--sys-danger);", bridge)
        self.assertIn("--warning-ink: var(--sys-warning-ink);", bridge)
        self.assertIn("--danger-ink: var(--sys-danger-ink);", bridge)

    def test_app_css_semantic_colors_use_sys_tokens(self):
        app = (ROOT / "app" / "web" / "css" / "app.css").read_text(encoding="utf-8")
        self.assertNotIn("#c48a2a", app)
        self.assertNotIn("#c23b3b", app)
        self.assertNotIn("#c62828", app)
        self.assertIn("var(--sys-warning-ink)", app)
        self.assertIn("var(--sys-danger-ink)", app)

    def test_index_applies_chrome_before_css(self):
        html = (ROOT / "app" / "web" / "index.html").read_text(encoding="utf-8")
        boot = html.find("ryuumd-chrome")
        css = html.find("vendor/ryuuji/styles/palettes.css")
        self.assertGreater(boot, 0)
        self.assertGreater(css, boot)
        self.assertIn("data-chrome-ready", html)

    def test_index_hides_app_until_fonts_and_chrome(self):
        html = (ROOT / "app" / "web" / "index.html").read_text(encoding="utf-8")
        self.assertIn("html:not([data-ui-ready]) #app { opacity: 0; }", html)
        self.assertNotIn("html:not([data-chrome-ready]) #app { opacity: 0; }", html)
        self.assertIn("data-fonts-ready", html)
        self.assertIn('document.fonts.load', html)

    def test_main_persists_webview_profile(self):
        text = (ROOT / "main.py").read_text(encoding="utf-8")
        self.assertIn("private_mode=False", text)
        self.assertIn('storage_path=str(config.data_dir / "webview")', text)

    def test_boot_does_not_fallback_to_empty_defaults(self):
        js = (ROOT / "app" / "web" / "js" / "app.js").read_text(encoding="utf-8")
        self.assertNotIn("已以默认配置启动", js)
        self.assertIn("async function loadConfig()", js)
        self.assertIn('typeof a.get_config === "function"', js)

    def test_editor_content_defaults_to_adaptive_width(self):
        css = (ROOT / "app" / "web" / "css" / "ryuuji-a.css").read_text(encoding="utf-8")
        self.assertNotIn("width: min(100%, 48rem)", css)
        self.assertNotIn("width: min(100%, 980px)", css)
        self.assertNotIn("max-width: min(100%, 72rem)", css)
        self.assertRegex(
            css,
            r"\.vditor-ir \.vditor-reset,\s*\n\.vditor-wysiwyg \.vditor-reset,\s*\n\.vditor-sv \{\s*\n  width: 100%;",
        )

    def test_readable_width_option_is_preserved(self):
        palette = (ROOT / "app" / "web" / "css" / "palette.css").read_text(encoding="utf-8")
        self.assertIn("#editor-wrap.readable-width", palette)
        self.assertIn("width: min(100%, 42rem)", palette)
        self.assertIn("max-width: 42rem !important", palette)
        cfg = (ROOT / "app" / "core" / "config.py").read_text(encoding="utf-8")
        self.assertIn('"readable_width": False', cfg)

    def test_window_background_follows_config(self):
        from app.core.config import Config
        from app.core.palette_css import load_export_palettes
        from main import chrome_attrs, window_background

        cfg = Config()
        cfg.update({"theme": "dark", "palette": "a6"})
        self.assertEqual(chrome_attrs(cfg), ("dark", "a6"))
        pals = load_export_palettes()
        self.assertEqual(window_background(cfg), pals["a6"]["dark"]["bg"])
        self.assertNotEqual(window_background(cfg), "#f4faff")
        cfg.update({"palette": "cherry"})
        self.assertEqual(chrome_attrs(cfg)[1], "a1")


if __name__ == "__main__":
    unittest.main(verbosity=2)
