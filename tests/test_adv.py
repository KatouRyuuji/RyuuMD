# -*- coding: utf-8 -*-
"""CLI / TUI / MCP / AISkill：同一核心 Api，不另开独立应用。"""

from __future__ import annotations

import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_TMP_APPDATA = tempfile.TemporaryDirectory(prefix="ryuumd-test-adv-appdata-")
os.environ["APPDATA"] = _TMP_APPDATA.name

from app.core.adv import (  # noqa: E402
    handle_mcp_message,
    invoke_tool,
    run_cli,
    run_tui,
)
from app.core.api import Api  # noqa: E402
from app.core.config import Config  # noqa: E402


def make_api() -> Api:
    cfg = Config()
    cfg.set("edit_mode", "source")
    cfg.set("ai", {"base_url": "https://example.test", "api_key": "sk-test", "model": "stub"})
    return Api(cfg)


class TestCliTuiMcp(unittest.TestCase):
    def setUp(self) -> None:
        self.dir = tempfile.TemporaryDirectory(prefix="ryuumd-adv-")
        self.root = Path(self.dir.name)
        self.api = make_api()
        self.md = self.root / "note.md"
        self.md.write_text("# 标题\n\nCLI_BODY_TOKEN\n", encoding="utf-8")

    def tearDown(self) -> None:
        self.dir.cleanup()

    def test_cli_read_prints_file_body(self):
        buf = io.StringIO()
        rc = run_cli(["read", str(self.md)], api=self.api, stdout=buf)
        self.assertEqual(rc, 0)
        self.assertIn("CLI_BODY_TOKEN", buf.getvalue())

    def test_tui_once_read_prints_file_body(self):
        buf = io.StringIO()
        rc = run_tui(["--once", "read", str(self.md)], api=self.api, stdout=buf)
        self.assertEqual(rc, 0)
        self.assertIn("CLI_BODY_TOKEN", buf.getvalue())

    def test_mcp_call_read_file(self):
        msg = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "read_file", "arguments": {"path": str(self.md)}},
        }
        reply = handle_mcp_message(msg, self.api)
        result = reply.get("result") or {}
        self.assertTrue(result.get("ok"), result)
        self.assertIn("CLI_BODY_TOKEN", result.get("content") or "")

    def test_invoke_tool_write_then_read(self):
        target = self.root / "out.md"
        w = invoke_tool(self.api, "write_file", {"path": str(target), "content": "hello-adv"})
        self.assertTrue(w.get("ok"), w)
        r = invoke_tool(self.api, "read_file", {"path": str(target)})
        self.assertEqual(r.get("content"), "hello-adv")

    def test_no_second_cli_mcp_tui_product(self):
        specs = [p.name.lower() for p in ROOT.glob("*.spec")]
        for name in specs:
            self.assertFalse(name.startswith("cli"), name)
            self.assertFalse("mcp" in name, name)
            self.assertFalse("tui" in name, name)
        extra = [
            p.name for p in (ROOT / "app").rglob("*.py")
            if p.name.lower() in ("cli_main.py", "tui_app.py", "mcp_server.py")
        ]
        self.assertEqual(extra, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
