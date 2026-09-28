# -*- coding: utf-8 -*-
"""CLI / TUI / MCP / AISkill：同一核心 Api，不另开独立应用。"""

from __future__ import annotations

import contextlib
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
    MCP_PROTOCOL_LATEST,
    _mcp_config,
    _self_command,
    dispatch_argv,
    ensure_stdio,
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


class _Tty(io.StringIO):
    """假装是交互终端：isatty 为 True 时 scratch 不进写模式。"""

    def isatty(self) -> bool:
        return True


class TestCliTuiMcp(unittest.TestCase):
    def setUp(self) -> None:
        self.dir = tempfile.TemporaryDirectory(prefix="ryuumd-adv-")
        self.root = Path(self.dir.name)
        self.api = make_api()
        self.md = self.root / "note.md"
        self.md.write_text("# 标题\n\nCLI_BODY_TOKEN\n", encoding="utf-8")

    def tearDown(self) -> None:
        self.dir.cleanup()

    # ---- CLI ----

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

    def test_help_lists_all_commands(self):
        buf = io.StringIO()
        rc = run_cli(["help"], api=self.api, stdout=buf)
        self.assertEqual(rc, 0)
        text = buf.getvalue()
        for name in ("read", "write", "ls", "search", "vaults", "scratch", "send", "receive", "mcp"):
            self.assertIn(name, text)
        self.assertNotIn("  scratch-read ", text)  # 隐藏旧命令不作为条目出现

    def test_dispatch_bare_command_and_legacy_prefix(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = dispatch_argv(["read", str(self.md)])
        self.assertEqual(rc, 0)
        self.assertIn("CLI_BODY_TOKEN", buf.getvalue())

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = dispatch_argv(["--cli", "read", str(self.md)])
        self.assertEqual(rc, 0)
        self.assertIn("CLI_BODY_TOKEN", buf.getvalue())

    def test_scratch_write_and_read(self):
        buf = io.StringIO()
        rc = run_cli(["scratch", "SCRATCH_TOKEN_1"], api=self.api, stdout=buf, stdin=_Tty())
        self.assertEqual(rc, 0)
        self.assertIn("SCRATCH_TOKEN_1", str(self.api.read_scratch().get("content")))

        buf = io.StringIO()
        rc = run_cli(["scratch"], api=self.api, stdout=buf, stdin=_Tty())
        self.assertEqual(rc, 0)
        self.assertIn("SCRATCH_TOKEN_1", buf.getvalue())

    def test_scratch_empty_pipe_reads_instead_of_clearing(self):
        self.api.write_scratch("SCRATCH_KEEP_TOKEN")
        buf = io.StringIO()
        rc = run_cli(["scratch"], api=self.api, stdout=buf, stdin=io.StringIO(""))
        self.assertEqual(rc, 0)
        self.assertIn("SCRATCH_KEEP_TOKEN", buf.getvalue())  # 空管道按读取处理
        self.assertIn("SCRATCH_KEEP_TOKEN", str(self.api.read_scratch().get("content")))

    def test_dangling_flag_reports_error(self):
        self.api.write_scratch("SCRATCH_GUARD_TOKEN")
        buf = io.StringIO()
        rc = run_cli(["scratch", "--content"], api=self.api, stdout=buf, stdin=_Tty())
        self.assertEqual(rc, 2)
        self.assertIn("缺少值", buf.getvalue())
        self.assertIn("SCRATCH_GUARD_TOKEN", str(self.api.read_scratch().get("content")))

    def test_search_keeps_literal_title_token(self):
        buf = io.StringIO()
        rc = run_cli(["search", "--title", "foo"], api=self.api, stdout=buf)
        self.assertNotEqual(rc, 2)  # search 未声明 --title，字面量留在查询词里

    def test_help_head_reaches_cli(self):
        from app.core.adv import is_adv_argv

        self.assertTrue(is_adv_argv(["help"]))
        self.assertTrue(is_adv_argv(["mcp"]))
        self.assertFalse(is_adv_argv(["D:/notes/a.md"]))

    def test_legacy_mcp_prefix_forwards_flags(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = dispatch_argv(["--mcp", "--print-config"])
        self.assertEqual(rc, 0)
        self.assertIn("mcpServers", buf.getvalue())

    def test_legacy_command_names(self):
        buf = io.StringIO()
        rc = run_cli(["scratch-write", "--content", "LEGACY_TOKEN"], api=self.api, stdout=buf)
        self.assertEqual(rc, 0)
        self.assertIn("LEGACY_TOKEN", str(self.api.read_scratch().get("content")))
        buf = io.StringIO()
        rc = run_cli(["scratch-read"], api=self.api, stdout=buf)
        self.assertEqual(rc, 0)
        self.assertIn("LEGACY_TOKEN", buf.getvalue())

    def test_write_without_stdin_reports_instead_of_crash(self):
        old = sys.stdin
        sys.stdin = None
        try:
            buf = io.StringIO()
            rc = run_cli(["write", str(self.root / "x.md")], api=self.api, stdout=buf)
            self.assertEqual(rc, 2)
            self.assertIn("--content", buf.getvalue())
        finally:
            sys.stdin = old

    # ---- 工具表 ----

    def test_invoke_tool_write_then_read(self):
        target = self.root / "out.md"
        w = invoke_tool(self.api, "write_note", {"path": str(target), "content": "hello-adv"})
        self.assertTrue(w.get("ok"), w)
        r = invoke_tool(self.api, "read_note", {"path": str(target)})
        self.assertEqual(r.get("content"), "hello-adv")

    def test_invoke_tool_legacy_aliases(self):
        target = self.root / "legacy.md"
        w = invoke_tool(self.api, "write_file", {"path": str(target), "content": "legacy-alias"})
        self.assertTrue(w.get("ok"), w)
        r = invoke_tool(self.api, "read_file", {"path": str(target)})
        self.assertEqual(r.get("content"), "legacy-alias")
        self.assertEqual(invoke_tool(self.api, "nope", {}).get("ok"), False)

    # ---- MCP 协议 ----

    def test_mcp_initialize_negotiates_version_and_capabilities(self):
        msg = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {"protocolVersion": "2025-03-26", "capabilities": {}, "clientInfo": {"name": "t", "version": "1"}},
        }
        result = handle_mcp_message(msg, self.api)["result"]
        self.assertEqual(result["protocolVersion"], "2025-03-26")
        self.assertIn("tools", result["capabilities"])
        self.assertEqual(result["serverInfo"]["name"], "RyuuMD")

        msg["params"]["protocolVersion"] = "1999-01-01"
        result = handle_mcp_message(msg, self.api)["result"]
        self.assertEqual(result["protocolVersion"], MCP_PROTOCOL_LATEST)

    def test_mcp_ping_and_notifications(self):
        reply = handle_mcp_message({"jsonrpc": "2.0", "id": 9, "method": "ping"}, self.api)
        self.assertEqual(reply.get("result"), {})
        # notification 不应答（含带 id 的畸形形态）
        self.assertIsNone(handle_mcp_message({"jsonrpc": "2.0", "method": "notifications/initialized"}, self.api))
        self.assertIsNone(handle_mcp_message({"jsonrpc": "2.0", "id": 1, "method": "notifications/initialized"}, self.api))
        # 无 id 的普通请求按 notification 处理，不应答
        self.assertIsNone(handle_mcp_message({"jsonrpc": "2.0", "method": "ping"}, self.api))
        # 带 id 但缺 method 的畸形请求回 -32600，让等待方尽快失败
        reply = handle_mcp_message({"jsonrpc": "2.0", "id": 1}, self.api)
        self.assertEqual((reply or {}).get("error", {}).get("code"), -32600)

    def test_mcp_tools_list_has_schema_and_hides_internal(self):
        reply = handle_mcp_message({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}, self.api)
        tools = reply["result"]["tools"]
        names = [t["name"] for t in tools]
        self.assertIn("read_note", names)
        self.assertNotIn("echo", names)  # internal 工具不出现在 MCP 边界
        for t in tools:
            self.assertIn("inputSchema", t)
            self.assertEqual(t["inputSchema"]["type"], "object")

    def test_mcp_call_read_note(self):
        msg = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "read_note", "arguments": {"path": str(self.md)}},
        }
        reply = handle_mcp_message(msg, self.api)
        result = reply.get("result") or {}
        self.assertFalse(result.get("isError"), result)
        payload = json.loads(result["content"][0]["text"])
        self.assertTrue(payload.get("ok"), payload)
        self.assertIn("CLI_BODY_TOKEN", payload.get("content") or "")

    def test_mcp_call_legacy_tool_name(self):
        msg = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "read_file", "arguments": {"path": str(self.md)}},
        }
        reply = handle_mcp_message(msg, self.api)
        self.assertFalse((reply.get("result") or {}).get("isError"), reply)

    def test_mcp_call_unknown_tool_is_jsonrpc_error(self):
        msg = {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "nope", "arguments": {}}}
        reply = handle_mcp_message(msg, self.api)
        self.assertEqual((reply.get("error") or {}).get("code"), -32602)

    # ---- stdio 重建 / 客户端接入 ----

    def test_ensure_stdio_noop_when_not_frozen(self):
        out_before, in_before = sys.stdout, sys.stdin
        ensure_stdio()
        self.assertIs(sys.stdout, out_before)
        self.assertIs(sys.stdin, in_before)

    def test_self_command_source_mode(self):
        cmd = _self_command()
        self.assertEqual(cmd[0], sys.executable)
        self.assertTrue(cmd[1].endswith("main.py"), cmd)
        self.assertEqual(cmd[2], "mcp")

    def test_self_command_frozen_mode(self):
        had = hasattr(sys, "frozen")
        old = getattr(sys, "frozen", None)
        sys.frozen = True
        try:
            self.assertEqual(_self_command(), [sys.executable, "mcp"])
        finally:
            if had:
                sys.frozen = old
            else:
                del sys.frozen

    def test_mcp_config_shape(self):
        cfg = _mcp_config()
        entry = cfg["mcpServers"]["ryuumd"]
        self.assertEqual(entry["type"], "stdio")
        self.assertTrue(entry["command"])
        self.assertEqual(entry["args"][-1], "mcp")

    # ---- 单产物约束 ----

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
