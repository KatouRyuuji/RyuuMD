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


class TestToolCommands(unittest.TestCase):
    """tool / tools 通用命令：整张工具表对脚本与 AI 可达。"""

    def setUp(self) -> None:
        self.dir = tempfile.TemporaryDirectory(prefix="ryuumd-tool-")
        self.root = Path(self.dir.name)
        self.api = make_api()
        self.md = self.root / "note.md"
        self.md.write_text("# 标题\n\nTOOL_BODY_TOKEN\n", encoding="utf-8")

    def tearDown(self) -> None:
        self.dir.cleanup()

    def test_tools_lists_json_schemas(self):
        buf = io.StringIO()
        rc = run_cli(["tools"], api=self.api, stdout=buf)
        self.assertEqual(rc, 0)
        tools = json.loads(buf.getvalue())
        names = [t["name"] for t in tools]
        self.assertIn("read_note", names)
        self.assertIn("smart_search", names)
        self.assertIn("vault_index", names)
        self.assertNotIn("echo", names)  # internal 不输出
        for t in tools:
            self.assertEqual(t["input_schema"]["type"], "object")

    def test_tool_invokes_with_args_json(self):
        buf = io.StringIO()
        rc = run_cli(["tool", "read_note", "--args", json.dumps({"path": str(self.md)})], api=self.api, stdout=buf, stdin=_Tty())
        self.assertEqual(rc, 0)
        payload = json.loads(buf.getvalue())
        self.assertTrue(payload.get("ok"), payload)
        self.assertIn("TOOL_BODY_TOKEN", payload.get("content") or "")

    def test_tool_reads_args_from_stdin(self):
        buf = io.StringIO()
        rc = run_cli(["tool", "read_note"], api=self.api, stdout=buf, stdin=io.StringIO(json.dumps({"path": str(self.md)})))
        self.assertEqual(rc, 0)
        self.assertIn("TOOL_BODY_TOKEN", buf.getvalue())

    def test_tool_no_args_defaults_empty_object(self):
        buf = io.StringIO()
        rc = run_cli(["tool", "list_vaults"], api=self.api, stdout=buf, stdin=_Tty())
        self.assertEqual(rc, 0)
        self.assertTrue(json.loads(buf.getvalue()).get("ok"))

    def test_tool_rejects_bad_json(self):
        buf = io.StringIO()
        rc = run_cli(["tool", "read_note", "--args", "{bad"], api=self.api, stdout=buf, stdin=_Tty())
        self.assertEqual(rc, 2)
        self.assertIn("JSON", buf.getvalue())

    def test_tool_rejects_non_object_args(self):
        buf = io.StringIO()
        rc = run_cli(["tool", "read_note", "--args", "[1,2]"], api=self.api, stdout=buf, stdin=_Tty())
        self.assertEqual(rc, 2)
        self.assertIn("JSON 对象", buf.getvalue())

    def test_tool_unknown_name_and_missing_name(self):
        buf = io.StringIO()
        rc = run_cli(["tool", "nope"], api=self.api, stdout=buf, stdin=_Tty())
        self.assertEqual(rc, 1)
        self.assertFalse(json.loads(buf.getvalue()).get("ok"))
        buf = io.StringIO()
        self.assertEqual(run_cli(["tool"], api=self.api, stdout=buf, stdin=_Tty()), 2)


class TestNewToolHandlers(unittest.TestCase):
    """扩展工具表的参数传递与闭环行为。"""

    def setUp(self) -> None:
        self.dir = tempfile.TemporaryDirectory(prefix="ryuumd-tools-")
        self.root = Path(self.dir.name)
        self.vault = self.root / "vault"
        self.vault.mkdir()
        (self.vault / "甲.md").write_text("# 甲\n\nALPHA_TOKEN\n\n- [ ] 待办甲\n\n[[乙]]\n", encoding="utf-8")
        (self.vault / "乙.md").write_text("# 乙\n\nBETA_TOKEN #标签一\n", encoding="utf-8")
        self.api = make_api()

    def tearDown(self) -> None:
        self.dir.cleanup()

    def call(self, tool: str, **args):
        return invoke_tool(self.api, tool, args)

    def test_specs_match_handlers(self):
        from app.core.adv import TOOL_SPECS, _TOOL_HANDLERS

        names = {t["name"] for t in TOOL_SPECS}
        self.assertEqual(names, set(_TOOL_HANDLERS))
        for t in TOOL_SPECS:
            schema = t["input_schema"]
            self.assertEqual(schema.get("type"), "object", t["name"])
            for req in schema.get("required", []):
                self.assertIn(req, schema.get("properties", {}), (t["name"], req))

    def test_mcp_lists_new_tools(self):
        reply = handle_mcp_message({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}, self.api)
        names = [t["name"] for t in reply["result"]["tools"]]
        for expect in ("smart_search", "vault_index", "daily_note", "capture", "workdir_status"):
            self.assertIn(expect, names)
        self.assertNotIn("echo", names)

    def test_file_management_cycle(self):
        r = self.call("new_note", folder=str(self.vault), name="新建")
        self.assertTrue(r.get("ok"), r)
        created = r["path"]
        self.assertTrue(created.endswith("新建.md"))
        r = self.call("rename_note", path=created, new_name="改名")
        self.assertTrue(r.get("ok"), r)
        renamed = r["path"]
        self.assertTrue(renamed.endswith("改名.md"))
        sub = self.vault / "子目录"
        sub.mkdir()
        r = self.call("move_note", path=renamed, target_folder=str(sub))
        self.assertTrue(r.get("ok"), r)
        moved = r["path"]
        self.assertEqual(Path(moved).parent, sub)
        r = self.call("duplicate_note", path=moved)
        self.assertTrue(r.get("ok"), r)
        self.assertIn("副本", r["name"])
        r = self.call("preview_note", path=str(self.vault / "甲.md"), max_chars=50)
        self.assertTrue(r.get("ok"), r)
        self.assertTrue(r.get("preview"))  # max_chars 下限 200，小文件不截断
        r = self.call("note_stat", path=moved)
        self.assertTrue(r.get("exists"), r)
        r = self.call("delete_note", path=moved)
        self.assertTrue(r.get("ok"), r)
        self.assertFalse(self.call("note_stat", path=moved).get("exists"))
        # 新建子文件夹
        r = self.call("new_folder", parent=str(self.vault), name="另一个")
        self.assertTrue(r.get("ok"), r)
        self.assertTrue((self.vault / "另一个").is_dir())

    def test_list_all_notes_and_smart_search(self):
        r = self.call("list_all_notes", folder=str(self.vault))
        self.assertTrue(r.get("ok"), r)
        self.assertEqual(len(r.get("items") or r.get("files") or []), 2)
        r = self.call("list_all_notes", folder=str(self.vault), query="甲")
        items = r.get("items") or r.get("files") or []
        self.assertEqual(len(items), 1)
        r = self.call("smart_search", folder=str(self.vault), query="甲", mode="title")
        self.assertTrue(r.get("ok"), r)
        self.assertTrue(r.get("hits"))
        r = self.call("smart_search", folder=str(self.vault), query="BETA_TOKEN", mode="content")
        self.assertTrue(r.get("hits"))

    def test_vault_management_cycle(self):
        parent = self.root / "parent"
        parent.mkdir()
        r = self.call("create_vault", parent=str(parent), name="新仓库")
        self.assertTrue(r.get("ok"), r)
        self.assertTrue((parent / "新仓库").is_dir())
        r = self.call("add_vault", path=str(self.vault), name="我的库")
        self.assertTrue(r.get("ok"), r)
        pid = (r.get("project") or {}).get("id") or r.get("id")
        self.assertTrue(pid)
        r = self.call("rename_vault", project_id=pid, name="改名库")
        self.assertTrue(r.get("ok"), r)
        r = self.call("pin_vault", project_id=pid, pinned=True)
        self.assertTrue(r.get("ok"), r)
        vaults = self.call("list_vaults")
        mine = [v for v in vaults["items"] if v.get("id") == pid]
        self.assertEqual(mine[0].get("name"), "改名库")
        self.assertTrue(mine[0].get("pinned"))
        r = self.call("remove_vault", project_id=pid)
        self.assertTrue(r.get("ok"), r)
        self.assertTrue(self.vault.is_dir())  # 移除不动磁盘
        # make_dir 任意父目录建文件夹
        r = self.call("make_dir", parent=str(parent), name="散目录")
        self.assertTrue(r.get("ok"), r)
        self.assertTrue((parent / "散目录").is_dir())

    def test_config_roundtrip(self):
        r = self.call("update_config", values={"theme": "dark", "daily_note_folder": "日志"})
        self.assertTrue(r.get("ok"), r)
        cfg = self.call("get_config")
        self.assertEqual(cfg.get("theme"), "dark")
        self.assertEqual(cfg.get("daily_note_folder"), "日志")
        # 脱敏：AI 密钥不回传明文
        self.assertNotEqual(str((cfg.get("ai") or {}).get("api_key") or ""), "sk-test")

    def test_recent_list(self):
        self.call("read_note", path=str(self.vault / "甲.md"))
        r = self.call("list_recent")
        paths = [it.get("path") for it in r.get("items") or []]
        self.assertIn(str(self.vault / "甲.md"), paths)
        r = self.call("remove_recent", path=str(self.vault / "甲.md"))
        self.assertTrue(r.get("ok"), r)
        self.assertNotIn(str(self.vault / "甲.md"), [it.get("path") for it in self.call("list_recent").get("items") or []])
        self.call("read_note", path=str(self.vault / "乙.md"))
        self.call("clear_recent")
        self.assertEqual(self.call("list_recent").get("items"), [])

    def test_capture_and_daily_note(self):
        r = self.call("capture", folder=str(self.vault), text="CAPTURE_TOKEN")
        self.assertTrue(r.get("ok"), r)
        box = self.vault / "收集箱.md"
        self.assertIn("CAPTURE_TOKEN", box.read_text(encoding="utf-8"))
        r = self.call("daily_note", folder=str(self.vault))
        self.assertTrue(r.get("ok"), r)
        from datetime import datetime

        self.assertIn(datetime.now().strftime("%Y-%m-%d"), r.get("path") or "")

    def test_templates(self):
        r = self.call("list_templates", folder=str(self.vault))
        self.assertEqual(r.get("items"), [])
        tpl_dir = self.vault / "模板"
        tpl_dir.mkdir()
        (tpl_dir / "会议.md").write_text("# {{title}}\n\n日期：{{date}}\n", encoding="utf-8")
        r = self.call("list_templates", folder=str(self.vault))
        self.assertEqual(len(r.get("items") or []), 1)
        r = self.call("new_from_template", folder=str(self.vault), template_path=str(tpl_dir / "会议.md"), name="周会")
        self.assertTrue(r.get("ok"), r)
        self.assertIn("# 周会", r.get("content") or "")
        self.assertNotIn("{{title}}", r.get("content") or "")

    def test_save_image(self):
        import base64

        png = base64.b64encode(bytes.fromhex("89504e470d0a1a0a0000000d494844520000000100000001080600000 01f15c4890000000d49444154789c626001000000ffff03000006000557bfabd40000000049454e44ae426082".replace(" ", ""))).decode()
        r = self.call("save_image", md_path=str(self.vault / "甲.md"), filename="p.png", data_b64=png)
        self.assertTrue(r.get("ok"), r)
        rel = r.get("rel") or ""
        self.assertTrue(rel.startswith("甲.assets/"), rel)
        self.assertTrue((self.vault / rel.replace("/", os.sep)).is_file())

    def test_vault_index_and_stats(self):
        r = self.call("vault_index", folder=str(self.vault), kind="tasks")
        self.assertTrue(r.get("ok"), r)
        self.assertTrue(r.get("items"))
        r = self.call("vault_index", folder=str(self.vault), kind="tags")
        self.assertTrue(any("标签一" in str(it) for it in r.get("items") or []), r)
        r = self.call("vault_index", folder=str(self.vault), kind="broken")
        self.assertTrue(r.get("ok"), r)
        r = self.call("vault_index", folder=str(self.vault), kind="orphans")
        self.assertTrue(r.get("ok"), r)
        r = self.call("vault_index", folder=str(self.vault), kind="mentions", path=str(self.vault / "乙.md"))
        self.assertTrue(r.get("ok"), r)
        r = self.call("vault_index", folder=str(self.vault), kind="nope")
        self.assertFalse(r.get("ok"))
        r = self.call("vault_stats", folder=str(self.vault))
        self.assertTrue(r.get("ok"), r)

    def test_wikilinks(self):
        r = self.call("resolve_wikilink", folder=str(self.vault), name="乙", current_file=str(self.vault / "甲.md"))
        self.assertTrue(r.get("ok"), r)
        self.assertIn("乙.md", str(r))
        r = self.call("find_backlinks", folder=str(self.vault), path=str(self.vault / "乙.md"))
        self.assertTrue(r.get("ok"), r)
        self.assertTrue(any("甲.md" in str(h) for h in r.get("hits") or []), r)

    def test_workdir_tools_in_source_mode(self):
        # 直改模式下工作副本工具明确报错，不静默
        for name in ("workdir_status", "workdir_summary", "workdir_push", "workdir_merge", "workdir_push_all", "workdir_merge_all"):
            r = self.call(name, path=str(self.vault / "甲.md"), folder=str(self.vault))
            self.assertFalse(r.get("ok"), (name, r))
            self.assertIn("直改", str(r.get("error") or ""), name)

    def test_list_im_providers(self):
        r = self.call("list_im_providers")
        self.assertTrue(r.get("ok"), r)
        ids = [p.get("id") for p in r.get("items") or []]
        for pid in ("feishu", "popo", "dingtalk", "wecom", "wechat", "qq"):
            self.assertIn(pid, ids)

    def test_ai_tools_without_config(self):
        api = Api(Config())  # 未配置 AI
        from app.core.adv import invoke_tool as _invoke

        r = _invoke(api, "ask_ai", {"folder": str(self.vault), "question": "你好"})
        self.assertFalse(r.get("ok"))
        self.assertTrue(r.get("error"))
        r = _invoke(api, "summarize_note", {"path": str(self.vault / "甲.md")})
        self.assertFalse(r.get("ok"))
        r = _invoke(api, "knowledge_tree", {"folder": str(self.vault)})
        self.assertFalse(r.get("ok"))

    def test_open_tutorial_idempotent(self):
        r = self.call("open_tutorial")
        self.assertTrue(r.get("ok"), r)
        self.assertTrue(r.get("entry", "").endswith("欢迎使用 RyuuMD.md"))
        r2 = self.call("open_tutorial")
        self.assertTrue(r2.get("ok"), r2)
        self.assertEqual(r2.get("copied"), 0)  # 幂等：不覆盖已有文件


if __name__ == "__main__":
    unittest.main(verbosity=2)
