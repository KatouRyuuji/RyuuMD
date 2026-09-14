# -*- coding: utf-8 -*-
"""外壳字体门闩：sys-tokens 由本仓库维护，不得引入 vendor webfont。"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SYS_TOKENS = ROOT / "app" / "web" / "css" / "sys-tokens.css"
INDEX = ROOT / "app" / "web" / "index.html"
BRIDGE = ROOT / "app" / "web" / "css" / "sys-bridge.css"


class FontOverlayTests(unittest.TestCase):
    def test_sys_tokens_is_app_owned_noto(self):
        text = SYS_TOKENS.read_text(encoding="utf-8")
        self.assertNotIn("@import", text)
        self.assertNotIn("misans", text.lower())
        self.assertNotIn("vendor/ryuuji", text)
        self.assertIsNone(re.search(r"--sys-font-ui:\s*\"MiSans\"", text))
        self.assertIn('--sys-font-ui: "Noto Sans SC", sans-serif', text)
        self.assertIn('--sys-font-display: "LXGW WenKai"', text)
        self.assertNotIn("Microsoft YaHei", text)
        self.assertNotIn("PingFang SC", text)
        self.assertIn('--sys-font-mono: "Cascadia Code"', text)

    def test_index_loads_sys_tokens_and_vendor_tokens_without_webfont_import(self):
        html = INDEX.read_text(encoding="utf-8")
        self.assertIn("css/sys-tokens.css", html)
        self.assertIn("css/noto-sans-sc.css", html)
        self.assertIn("vendor/ryuuji/styles/tokens.css", html)
        self.assertNotIn('href="vendor/ryuuji/styles/patterns.css"', html)
        vendor = (ROOT / "app" / "web" / "vendor" / "ryuuji" / "styles" / "tokens.css").read_text(encoding="utf-8")
        self.assertNotIn("@import", vendor)

    def test_home_shell_uses_duration_tokens_no_backdrop(self):
        home = (ROOT / "app" / "web" / "css" / "home.css").read_text(encoding="utf-8")
        self.assertNotIn("backdrop-filter", home)
        self.assertIn("var(--sys-dur-2)", home)
        self.assertIn("var(--sys-shadow-lift)", home)
        self.assertIn("var(--sys-paper-lip)", home)
        self.assertNotIn("translateY(-2px)", home)

    def test_boot_defers_patterns_stylesheet(self):
        js = (ROOT / "app" / "web" / "js" / "app.js").read_text(encoding="utf-8")
        self.assertIn("scheduleDecorLoad", js)
        self.assertIn("vendor/ryuuji/styles/patterns.css", js)

    def test_sys_bridge_does_not_redeclare_sys_fonts(self):
        text = BRIDGE.read_text(encoding="utf-8")
        self.assertNotIn("--sys-font-ui:", text)
        self.assertNotIn("--sys-font-display:", text)
        self.assertNotIn("--sys-font-mono:", text)
        self.assertIn("--font-mono:", text)

    def test_noto_webfont_shipped(self):
        css = (ROOT / "app" / "web" / "css" / "noto-sans-sc.css").read_text(encoding="utf-8")
        font_dir = ROOT / "app" / "web" / "assets" / "fonts" / "noto-sans-sc"
        self.assertTrue((font_dir / "OFL.txt").is_file())
        woffs = list(font_dir.glob("*.woff2"))
        self.assertGreaterEqual(len(woffs), 20, "Noto Sans SC 分片 woff2 数量过少")
        self.assertIn("font-display: block", css)
        self.assertNotIn("font-display: swap", css)
        self.assertIn("unicode-range:", css)


if __name__ == "__main__":
    unittest.main()
