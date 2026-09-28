"""本应用高级功能：CLI / TUI / MCP / AISkill，共用同一套核心读写。

入口仍是 main.py。无参数或首参数为路径时启动 GUI；首参数命中命令表
（read/write/ls/search/vaults/scratch/send/receive/mcp）时走 CLI；
``mcp`` 启动 stdio JSON-RPC 服务。旧前缀 ``--cli`` / ``--tui`` / ``--mcp``
与旧命令名（list/im-send/im-receive/scratch-read/scratch-write）仍兼容。
工具表与 AI 路径共用 invoke_tool。
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Optional, TextIO

from . import anthropic as ai_client
from .api import Api
from .config import Config
from .fsutil import atomic_write_text

Dispatch = Callable[[str, dict[str, Any]], dict[str, Any]]

# ---------------------------------------------------------------------------
# 工具表（标准 JSON Schema；MCP 与 Anthropic Messages 直接消费）

TOOL_SPECS: list[dict[str, Any]] = [
    {
        "name": "read_note",
        "description": "读取 Markdown 笔记正文",
        "input_schema": {
            "type": "object",
            "properties": {"path": {"type": "string", "description": "笔记文件路径"}},
            "required": ["path"],
        },
    },
    {
        "name": "write_note",
        "description": "写入 Markdown 笔记正文（整文覆盖）",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "笔记文件路径"},
                "content": {"type": "string", "description": "Markdown 正文"},
            },
            "required": ["path", "content"],
        },
    },
    {
        "name": "list_notes",
        "description": "列出目录下的 Markdown 笔记与子文件夹",
        "input_schema": {
            "type": "object",
            "properties": {"path": {"type": "string", "description": "目录路径"}},
            "required": ["path"],
        },
    },
    {
        "name": "search_notes",
        "description": "在仓库内按标题或正文搜索",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "搜索关键词"},
                "folder": {"type": "string", "description": "限定仓库目录，缺省搜索全部"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "list_vaults",
        "description": "列出已注册仓库",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "read_scratch",
        "description": "读取随手记",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "write_scratch",
        "description": "写入随手记",
        "input_schema": {
            "type": "object",
            "properties": {"content": {"type": "string", "description": "随手记正文"}},
            "required": ["content"],
        },
    },
    {
        "name": "send_to_im",
        "description": "把 Markdown 发到飞书/POPO/钉钉/企业微信/微信/QQ 通道",
        "input_schema": {
            "type": "object",
            "properties": {
                "provider": {"type": "string", "description": "feishu/popo/dingtalk/wecom/wechat/qq"},
                "markdown": {"type": "string", "description": "Markdown 正文"},
                "title": {"type": "string", "description": "标题（部分通道使用）"},
            },
            "required": ["provider", "markdown"],
        },
    },
    {
        "name": "receive_from_im",
        "description": "从即时通讯通道取回最近一封发出的 Markdown",
        "input_schema": {
            "type": "object",
            "properties": {"provider": {"type": "string", "description": "通道名"}},
            "required": ["provider"],
        },
    },
    {
        "name": "echo",
        "description": "原样返回 text，供 AI 联调",
        "internal": True,
        "input_schema": {
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
        },
    },
]

# 旧工具名 → 新工具名（兼容既有 MCP 客户端与 AI 会话）
TOOL_ALIASES: dict[str, str] = {
    "read_file": "read_note",
    "write_file": "write_note",
    "list_folder": "list_notes",
    "search_vault": "search_notes",
    "list_projects": "list_vaults",
    "im_send": "send_to_im",
    "im_receive": "receive_from_im",
}

SKILLS: list[dict[str, str]] = [
    {"id": "echo", "tool": "echo", "title": "回声"},
    {"id": "read_note", "tool": "read_note", "title": "读笔记"},
    {"id": "write_note", "tool": "write_note", "title": "写笔记"},
    {"id": "search_notes", "tool": "search_notes", "title": "搜笔记"},
    {"id": "list_vaults", "tool": "list_vaults", "title": "列仓库"},
    {"id": "scratch", "tool": "read_scratch", "title": "随手记"},
]


def _canonical(name: str) -> str:
    n = (name or "").strip()
    return TOOL_ALIASES.get(n, n)


_TOOL_HANDLERS: dict[str, Callable[[Api, dict[str, Any]], dict[str, Any]]] = {
    "echo": lambda api, a: {"ok": True, "text": str(a.get("text") or "")},
    "read_note": lambda api, a: api.read_file(str(a.get("path") or "")),
    "write_note": lambda api, a: api.save_file(str(a.get("path") or ""), str(a.get("content") or "")),
    "list_notes": lambda api, a: api.list_folder(str(a.get("path") or "")),
    "search_notes": lambda api, a: api.search_vault(str(a.get("folder") or ""), str(a.get("query") or "")),
    "list_vaults": lambda api, a: api.list_projects(),
    "read_scratch": lambda api, a: api.read_scratch(),
    "write_scratch": lambda api, a: api.write_scratch(str(a.get("content") or "")),
    "send_to_im": lambda api, a: api.im_send(
        str(a.get("provider") or ""), str(a.get("markdown") or ""), str(a.get("title") or "")
    ),
    "receive_from_im": lambda api, a: api.im_receive(str(a.get("provider") or "")),
}


def invoke_tool(api: Api, name: str, arguments: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    """按工具名调用与 GUI 相同的 Api 方法；旧名经 TOOL_ALIASES 归一。"""
    handler = _TOOL_HANDLERS.get(_canonical(name))
    if handler is None:
        return {"ok": False, "error": f"未知工具: {name}"}
    return handler(api, dict(arguments or {}))


def list_tools(include_internal: bool = True) -> list[dict[str, Any]]:
    if include_internal:
        return list(TOOL_SPECS)
    return [t for t in TOOL_SPECS if not t.get("internal")]


def list_skills() -> list[dict[str, str]]:
    return list(SKILLS)


def to_anthropic_tools(specs: Optional[list[dict[str, Any]]] = None) -> list[dict[str, Any]]:
    """把本应用工具表透传成 Messages API 的 tools 数组（含 internal 工具，供 AI 联调）。"""
    out: list[dict[str, Any]] = []
    for spec in specs if specs is not None else TOOL_SPECS:
        name = str(spec.get("name") or "")
        if not name:
            continue
        out.append({
            "name": name,
            "description": str(spec.get("description") or ""),
            "input_schema": dict(spec.get("input_schema") or {"type": "object", "properties": {}}),
        })
    return out


# ---------------------------------------------------------------------------
# AI 问答

def ask_with_tools(
    question: str,
    root: str,
    cfg: Any,
    dispatch: Dispatch,
) -> dict[str, Any]:
    """AI 问答：complete 带上工具表；响应若含 tool_use 则调度本应用工具。"""
    q = (question or "").strip()
    if not q:
        return {"ok": False, "kind": "ask", "question": "", "answer": "", "error": "问题为空", "hits": []}
    err = ai_client.config_error(cfg)
    if err:
        return {"ok": False, "kind": "ask", "question": q, "answer": "", "error": err, "hits": []}
    excerpts = ""
    truncated = False
    if root:
        notes, truncated = ai_client._excerpts(root)
        excerpts = "\n\n".join(f"## {n['rel']}\n{n['excerpt']}" for n in notes)
    user = (
        "你是 RyuuMD 笔记助手。根据仓库摘录回答用户问题；摘录不足时请明确说明。用中文回答。"
        "需要读写笔记、搜索、列仓库或随手记时，调用提供的工具。\n\n"
        f"仓库摘录：\n{excerpts or '（当前没有打开仓库）'}\n\n"
        f"用户问题：\n{q}"
    )
    res = ai_client.complete(cfg, user, tools=to_anthropic_tools())
    if not res.get("ok"):
        return {
            "ok": False,
            "kind": "ask",
            "question": q,
            "answer": "",
            "error": res.get("error") or "",
            "hits": [],
            "truncated": truncated,
        }
    use = res.get("tool_use") if isinstance(res.get("tool_use"), dict) else None
    if use and use.get("name"):
        tool_res = dispatch(str(use.get("name")), dict(use.get("input") or {}))
        return {
            "ok": bool(tool_res.get("ok", True)),
            "kind": "ask",
            "question": q,
            "answer": str(res.get("text") or ""),
            "error": str(tool_res.get("error") or ""),
            "hits": [],
            "truncated": truncated,
            "tool": str(use.get("name")),
            "tool_result": tool_res,
        }
    return {
        "ok": True,
        "kind": "ask",
        "question": q,
        "answer": str(res.get("text") or ""),
        "error": "",
        "hits": [],
        "truncated": truncated,
    }


# ---------------------------------------------------------------------------
# CLI（命令表驱动）

CliHandler = Callable[[Api, list[str], dict[str, Optional[str]], TextIO, Optional[TextIO]], int]


def _write_text(out: TextIO, text: str) -> None:
    out.write(text)
    if not text.endswith("\n"):
        out.write("\n")


def _is_tty(stream: Any) -> bool:
    try:
        return bool(stream.isatty())
    except Exception:
        return False


def _body_from(flags: dict[str, Optional[str]], inn: Optional[TextIO], out: TextIO) -> Optional[str]:
    """正文来源：--content 优先，否则读 stdin；stdin 不可用时报错返回 None。"""
    if flags.get("content") is not None:
        return str(flags["content"])
    if inn is None:
        out.write("stdin 不可用，请用 --content 传入正文\n")
        return None
    return inn.read()


def _cmd_read(api: Api, args: list[str], flags: dict[str, Optional[str]], out: TextIO, inn: Optional[TextIO]) -> int:
    if not args:
        out.write("缺少路径\n")
        return 2
    res = api.read_file(args[0])
    if not res.get("ok"):
        out.write(str(res.get("error") or "读取失败") + "\n")
        return 1
    _write_text(out, str(res.get("content") or ""))
    return 0


def _cmd_write(api: Api, args: list[str], flags: dict[str, Optional[str]], out: TextIO, inn: Optional[TextIO]) -> int:
    if not args:
        out.write("缺少路径\n")
        return 2
    body = _body_from(flags, inn, out)
    if body is None:
        return 2
    res = api.save_file(args[0], body)
    if not res.get("ok"):
        out.write(str(res.get("error") or "写入失败") + "\n")
        return 1
    out.write(str(res.get("path") or args[0]) + "\n")
    return 0


def _cmd_ls(api: Api, args: list[str], flags: dict[str, Optional[str]], out: TextIO, inn: Optional[TextIO]) -> int:
    if not args:
        out.write("缺少路径\n")
        return 2
    res = api.list_folder(args[0])
    if not res.get("ok"):
        out.write(str(res.get("error") or "列出失败") + "\n")
        return 1
    out.write(json.dumps(res, ensure_ascii=False, indent=2) + "\n")
    return 0


def _cmd_search(api: Api, args: list[str], flags: dict[str, Optional[str]], out: TextIO, inn: Optional[TextIO]) -> int:
    query = " ".join(args).strip()
    if not query:
        out.write("缺少查询\n")
        return 2
    res = api.search_vault(str(flags.get("folder") or ""), query)
    if not res.get("ok"):
        out.write(str(res.get("error") or "搜索失败") + "\n")
        return 1
    out.write(json.dumps(res, ensure_ascii=False, indent=2) + "\n")
    return 0


def _cmd_vaults(api: Api, args: list[str], flags: dict[str, Optional[str]], out: TextIO, inn: Optional[TextIO]) -> int:
    res = api.list_projects()
    out.write(json.dumps(res, ensure_ascii=False, indent=2) + "\n")
    return 0 if res.get("ok") else 1


def _scratch_write(api: Api, body: str, out: TextIO) -> int:
    res = api.write_scratch(body)
    if not res.get("ok"):
        out.write(str(res.get("error") or "写入失败") + "\n")
        return 1
    out.write(str(res.get("path") or "") + "\n")
    return 0


def _scratch_read(api: Api, out: TextIO) -> int:
    res = api.read_scratch()
    if not res.get("ok"):
        out.write(str(res.get("error") or "读取失败") + "\n")
        return 1
    _write_text(out, str(res.get("content") or ""))
    return 0


def _cmd_scratch(api: Api, args: list[str], flags: dict[str, Optional[str]], out: TextIO, inn: Optional[TextIO]) -> int:
    body = flags.get("content")
    if body is None and args:
        body = " ".join(args)
    if body is None and inn is not None and not _is_tty(inn):
        body = inn.read()
    # 空正文（空管道、--content ""）视为「无内容」走读取，防止误清空随手记
    if body:
        return _scratch_write(api, body, out)
    return _scratch_read(api, out)


def _cmd_scratch_read(api: Api, args: list[str], flags: dict[str, Optional[str]], out: TextIO, inn: Optional[TextIO]) -> int:
    return _scratch_read(api, out)


def _cmd_scratch_write(api: Api, args: list[str], flags: dict[str, Optional[str]], out: TextIO, inn: Optional[TextIO]) -> int:
    body = _body_from(flags, inn, out)
    if body is None:
        return 2
    return _scratch_write(api, body, out)


def _cmd_send(api: Api, args: list[str], flags: dict[str, Optional[str]], out: TextIO, inn: Optional[TextIO]) -> int:
    if not args:
        out.write("缺少通道名\n")
        return 2
    body = _body_from(flags, inn, out)
    if body is None:
        return 2
    res = api.im_send(args[0], body, str(flags.get("title") or ""))
    if not res.get("ok"):
        out.write(str(res.get("error") or "发送失败") + "\n")
        return 1
    out.write(json.dumps(res, ensure_ascii=False) + "\n")
    return 0


def _cmd_receive(api: Api, args: list[str], flags: dict[str, Optional[str]], out: TextIO, inn: Optional[TextIO]) -> int:
    if not args:
        out.write("缺少通道名\n")
        return 2
    res = api.im_receive(args[0])
    if not res.get("ok"):
        out.write(str(res.get("error") or "收取失败") + "\n")
        return 1
    _write_text(out, str(res.get("content") or ""))
    return 0


COMMANDS: list[dict[str, Any]] = [
    {"name": "read", "usage": "read <path>", "help": "读取笔记正文", "handler": _cmd_read},
    {"name": "write", "usage": "write <path> [--content TEXT]", "help": "写入笔记（无 --content 时读 stdin）", "handler": _cmd_write, "flags": ("content",)},
    {"name": "ls", "usage": "ls <path>", "help": "列出目录下的笔记与子文件夹", "handler": _cmd_ls, "aliases": ["list"]},
    {"name": "search", "usage": "search <query> [--folder PATH]", "help": "在仓库内按标题或正文搜索", "handler": _cmd_search, "flags": ("folder",)},
    {"name": "vaults", "usage": "vaults", "help": "列出已注册仓库", "handler": _cmd_vaults},
    {"name": "scratch", "usage": "scratch [TEXT | --content TEXT]", "help": "随手记：无内容时读取，有内容时写入", "handler": _cmd_scratch, "flags": ("content",)},
    {"name": "send", "usage": "send <feishu|popo|dingtalk|wecom|wechat|qq> [--content TEXT] [--title T]", "help": "把 Markdown 发到即时通讯通道", "handler": _cmd_send, "aliases": ["im-send"], "flags": ("content", "title")},
    {"name": "receive", "usage": "receive <channel>", "help": "取回最近一封发出的 Markdown", "handler": _cmd_receive, "aliases": ["im-receive"]},
    # 旧命令名：语义与合并后的 scratch 不同，作为隐藏命令保留旧行为
    {"name": "scratch-read", "usage": "scratch-read", "help": "", "handler": _cmd_scratch_read, "hidden": True},
    {"name": "scratch-write", "usage": "scratch-write [--content TEXT]", "help": "", "handler": _cmd_scratch_write, "hidden": True, "flags": ("content",)},
]

_COMMAND_MAP: dict[str, dict[str, Any]] = {}
for _c in COMMANDS:
    _COMMAND_MAP[_c["name"]] = _c
    for _a in _c.get("aliases", ()):
        _COMMAND_MAP[_a] = _c


def _usage() -> str:
    lines = [
        "RyuuMD 命令行（无参数时启动 GUI）",
        "",
        "用法: RyuuMD <命令> [参数]     源码: python main.py <命令>",
        "",
        "命令:",
    ]
    for c in COMMANDS:
        if c.get("hidden"):
            continue
        lines.append(f"  {c['usage']:<58} {c['help']}")
    lines += [
        "",
        "MCP:",
        "  mcp                                                        启动 MCP stdio 服务",
        "  mcp --install                                              注册到 Claude Code（user 级）",
        "  mcp --print-config                                         打印可粘贴的客户端配置",
        "",
        "兼容: --cli/--tui/--mcp 前缀与旧命令名（list/im-send/im-receive/scratch-read/scratch-write）仍可用。",
    ]
    return "\n".join(lines) + "\n"


def _take_flag(argv: list[str], flag: str) -> tuple[list[str], Optional[str]]:
    out: list[str] = []
    val: Optional[str] = None
    i = 0
    while i < len(argv):
        if argv[i] == flag and i + 1 < len(argv):
            val = argv[i + 1]
            i += 2
            continue
        out.append(argv[i])
        i += 1
    return out, val


def run_cli(
    argv: list[str],
    api: Optional[Api] = None,
    stdout: Optional[TextIO] = None,
    stdin: Optional[TextIO] = None,
) -> int:
    """执行一条 CLI 命令；成功把正文打到 stdout。"""
    out = stdout or sys.stdout
    if out is None:
        return 1  # stdio 重建失败（无控制台的窗口程序）：静默降级，退出码仍有效
    inn = stdin if stdin is not None else sys.stdin
    args = list(argv)
    if not args:
        out.write(_usage())
        return 2
    if args[0] in ("-h", "--help", "help"):
        out.write(_usage())
        return 0
    entry = _COMMAND_MAP.get(args[0])
    if entry is None:
        out.write("未知命令: " + args[0] + "\n" + _usage())
        return 2
    # 只抽取该命令声明过的 flag，其余原样留在位置参数（如 search 的字面 "--title"）
    rest = args[1:]
    flags: dict[str, Optional[str]] = {}
    declared = entry.get("flags", ())
    for f in declared:
        rest, flags[f] = _take_flag(rest, "--" + f)
    dangling = next((t for t in rest if t in ("--" + f for f in declared)), None)
    if dangling is not None:
        out.write(f"{dangling} 缺少值\n")
        return 2
    client = api or Api(Config())
    return entry["handler"](client, rest, flags, out, inn)


def run_tui(argv: list[str], api: Optional[Api] = None, stdout: Optional[TextIO] = None) -> int:
    """TUI：``--once`` 后接与 CLI 相同的子命令，走同一套核心。"""
    out = stdout or sys.stdout
    if out is None:
        return 1
    args = list(argv)
    if args and args[0] == "--once":
        return run_cli(args[1:], api=api, stdout=out)
    if args:
        return run_cli(args, api=api, stdout=out)
    out.write(_usage())
    return 2


# ---------------------------------------------------------------------------
# MCP（stdio JSON-RPC，协议版本 2024-11-05 至 2025-06-18）

MCP_PROTOCOL_VERSIONS = ("2024-11-05", "2025-03-26", "2025-06-18")
MCP_PROTOCOL_LATEST = MCP_PROTOCOL_VERSIONS[-1]


def _mcp_tools() -> list[dict[str, Any]]:
    """MCP 边界的 tools/list 载荷：隐藏 internal 工具，键名转 inputSchema。"""
    out: list[dict[str, Any]] = []
    for spec in list_tools(include_internal=False):
        out.append({
            "name": spec["name"],
            "description": spec["description"],
            "inputSchema": spec["input_schema"],
        })
    return out


def handle_mcp_message(msg: dict[str, Any], api: Api) -> Optional[dict[str, Any]]:
    """处理一条 JSON-RPC 消息；notification 不应答（返回 None）。

    JSON-RPC 语义：notifications/* 与无 id 的请求都不应答；带 id 但缺 method
    的畸形请求回 -32600，让等待方尽快失败而不是挂起。
    """
    mid = msg.get("id")
    method = str(msg.get("method") or "")
    params = msg.get("params") if isinstance(msg.get("params"), dict) else {}
    if not method:
        if mid is not None:
            return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32600, "message": "Invalid Request: missing method"}}
        return None
    if method.startswith("notifications/") or mid is None:
        return None
    if method == "initialize":
        version = str(params.get("protocolVersion") or "")
        if version not in MCP_PROTOCOL_VERSIONS:
            version = MCP_PROTOCOL_LATEST
        return {
            "jsonrpc": "2.0",
            "id": mid,
            "result": {
                "protocolVersion": version,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": "RyuuMD", "version": "1"},
            },
        }
    if method == "ping":
        return {"jsonrpc": "2.0", "id": mid, "result": {}}
    if method in ("tools/list", "list_tools"):
        return {"jsonrpc": "2.0", "id": mid, "result": {"tools": _mcp_tools()}}
    if method in ("tools/call", "call_tool"):
        raw_name = str(params.get("name") or "")
        name = _canonical(raw_name)
        if name not in _TOOL_HANDLERS:
            return {
                "jsonrpc": "2.0",
                "id": mid,
                "error": {"code": -32602, "message": f"Unknown tool: {raw_name}"},
            }
        arguments = params.get("arguments") if isinstance(params.get("arguments"), dict) else {}
        res = invoke_tool(api, name, arguments)
        return {
            "jsonrpc": "2.0",
            "id": mid,
            "result": {
                "content": [{"type": "text", "text": json.dumps(res, ensure_ascii=False)}],
                "isError": not bool(res.get("ok", True)),
            },
        }
    return {
        "jsonrpc": "2.0",
        "id": mid,
        "error": {"code": -32601, "message": f"Unknown method: {method}"},
    }


def run_mcp(api: Optional[Api] = None, stdin: Optional[TextIO] = None, stdout: Optional[TextIO] = None) -> int:
    """stdio JSON-RPC 循环；notification 不应答；空输入结束。"""
    client = api or Api(Config())
    inn = stdin or sys.stdin
    out = stdout or sys.stdout
    if inn is None or out is None:
        return 1
    for line in inn:
        raw = line.strip()
        if not raw:
            continue
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            out.write(json.dumps({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "parse error"}}) + "\n")
            out.flush()
            continue
        if not isinstance(msg, dict):
            continue
        reply = handle_mcp_message(msg, client)
        if reply is None:
            continue
        out.write(json.dumps(reply, ensure_ascii=False) + "\n")
        out.flush()
    return 0


# ---------------------------------------------------------------------------
# MCP 客户端接入（--print-config / --install）

def _self_command() -> list[str]:
    """当前进程启动 MCP 模式的命令行（exe 用自身，源码用解释器 + main.py）。"""
    if getattr(sys, "frozen", False):
        return [sys.executable, "mcp"]
    main_py = Path(__file__).resolve().parent.parent.parent / "main.py"
    return [sys.executable, str(main_py), "mcp"]


def _mcp_config() -> dict[str, Any]:
    cmd = _self_command()
    return {"mcpServers": {"ryuumd": {"type": "stdio", "command": cmd[0], "args": cmd[1:]}}}


def _mcp_print_config(out: TextIO) -> int:
    out.write(json.dumps(_mcp_config(), ensure_ascii=False, indent=2) + "\n")
    return 0


def _backup_path(path: Path) -> Path:
    return path.parent / (path.name + ".bak-" + datetime.now().strftime("%Y%m%d-%H%M%S"))


def _mcp_install(out: TextIO) -> int:
    """注册到 Claude Code：优先调 claude CLI，否则原子写 ~/.claude.json。"""
    cmd = _self_command()
    claude = shutil.which("claude")
    if claude:
        full = ["claude", "mcp", "add", "--scope", "user", "ryuumd", "--"] + cmd
        if claude.lower().endswith((".cmd", ".bat")):
            full = ["cmd", "/c"] + full
        try:
            # 捕获字节自行解码：claude CLI 输出 UTF-8，text=True 在中文 Windows 按 GBK 解码会崩
            proc = subprocess.run(full, capture_output=True, timeout=30)
        except (OSError, subprocess.TimeoutExpired) as e:
            out.write(f"claude CLI 执行失败: {e}\n")
            return 1
        if proc.returncode == 0:
            out.write("已注册到 Claude Code（scope: user），重开会话后生效\n")
            return 0
        detail = ((proc.stderr or b"") or (proc.stdout or b"")).decode("utf-8", errors="replace").strip()
        out.write(f"claude mcp add 失败: {detail}\n")
        return 1
    path = Path.home() / ".claude.json"
    data: dict[str, Any] = {}
    if path.exists():
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(loaded, dict):
                raise ValueError("根节点不是 JSON 对象")
            data = loaded
        except (ValueError, OSError) as e:
            backup = _backup_path(path)
            try:
                shutil.copy2(path, backup)
                out.write(f"{path} 解析失败（{e}），已备份为 {backup.name}，未做修改\n")
            except OSError as be:
                out.write(f"{path} 解析失败（{e}），备份也失败（{be}），未做修改\n")
            return 1
    servers = data.setdefault("mcpServers", {})
    if not isinstance(servers, dict):
        out.write(f"{path} 的 mcpServers 不是对象，未做修改\n")
        return 1
    servers["ryuumd"] = _mcp_config()["mcpServers"]["ryuumd"]
    backed = path.exists()
    try:
        if backed:
            shutil.copy2(path, _backup_path(path))
        atomic_write_text(path, json.dumps(data, ensure_ascii=False, indent=2))
    except OSError as e:
        out.write(f"写入 {path} 失败（{e}），未做修改\n")
        return 1
    note = "（原文件已备份）" if backed else ""
    out.write(f"已写入 {path} 的 mcpServers.ryuumd{note}，重开 Claude Code 会话后生效\n")
    return 0


def _mcp_usage() -> str:
    return (
        "用法: RyuuMD mcp [--install | --print-config]\n"
        "  （无参数）         启动 MCP stdio 服务\n"
        "  --install         注册到 Claude Code（user 级）\n"
        "  --print-config    打印可粘贴的客户端配置\n"
    )


def _mcp_dispatch(rest: list[str]) -> int:
    out = sys.stdout
    if out is None:
        return 1  # stdio 重建失败：静默降级
    if not rest:
        return run_mcp()
    head = rest[0]
    if head == "--print-config":
        return _mcp_print_config(out)
    if head == "--install":
        return _mcp_install(out)
    if head in ("-h", "--help", "help"):
        out.write(_mcp_usage())
        return 0
    out.write(f"未知 mcp 参数: {head}\n" + _mcp_usage())
    return 2


# ---------------------------------------------------------------------------
# windowed exe 的 stdio 重建

def _stream_ok(stream: Any) -> bool:
    if stream is None or type(stream).__name__ == "NullWriter":
        return False
    try:
        stream.fileno()
    except Exception:
        return False
    return True


def ensure_stdio() -> None:
    """CLI/MCP 模式下修正或重建标准流（仅 Windows）。

    - 源码模式：非 tty 且非 UTF-8 的流 reconfigure 为 UTF-8——MCP 管道场景的协议
      硬要求（中文 Windows 的管道流默认 GBK）；tty 控制台不动。
    - frozen（console=False 打包）：逐流处理。GetStdHandle 有效且非控制台（管道/
      重定向，如 MCP 被 spawn）→ 一律 UTF-8：流可用则 reconfigure，不可用则
      open_osfhandle 重建；仍为坏的流 → AttachConsole 挂父控制台后开 CONOUT$/CONIN$
      （AttachConsole 不更新进程标准句柄，必须显式开 CON 设备；stdin 用 GetConsoleCP），
      编码随控制台代码页；都失败则保持静默降级（Explorer 带参启动场景），退出码仍有效。
    """
    if sys.platform != "win32":
        return
    if not getattr(sys, "frozen", False):
        for name in ("stdin", "stdout", "stderr"):
            current = getattr(sys, name)
            if _stream_ok(current) and not _is_tty(current) and not _is_utf8(current):
                _reconfigure_utf8(current, name)
        return
    import ctypes
    import msvcrt

    kernel32 = ctypes.windll.kernel32
    handles = {
        "stdin": kernel32.GetStdHandle(-10),   # STD_INPUT_HANDLE
        "stdout": kernel32.GetStdHandle(-11),  # STD_OUTPUT_HANDLE
        "stderr": kernel32.GetStdHandle(-12),  # STD_ERROR_HANDLE
    }
    wrappers: dict[tuple[int, bool], TextIO] = {}
    for name, handle in handles.items():
        if not handle or handle == -1:
            continue
        current = getattr(sys, name)
        if _is_console_handle(kernel32, handle):
            if not _stream_ok(current):
                _wrap_handle(msvcrt, name, handle, _console_encoding(kernel32, name), "replace", wrappers)
            continue
        if _stream_ok(current):
            _reconfigure_utf8(current, name)
        else:
            _wrap_handle(msvcrt, name, handle, "utf-8", "replace", wrappers)
    # 部分句柄有效的中间态：对仍为坏的流走控制台兜底
    if all(_stream_ok(getattr(sys, n)) for n in ("stdin", "stdout", "stderr")):
        return
    if not kernel32.AttachConsole(-1):  # ATTACH_PARENT_PROCESS
        return
    generic_read_write = 0x80000000 | 0x40000000
    share_read_write = 0x1 | 0x2
    out_handle = kernel32.CreateFileW("CONOUT$", generic_read_write, share_read_write, None, 3, 0, None)
    in_handle = kernel32.CreateFileW("CONIN$", generic_read_write, share_read_write, None, 3, 0, None)
    for name, handle in (("stdout", out_handle), ("stderr", out_handle), ("stdin", in_handle)):
        if not _stream_ok(getattr(sys, name)):
            _wrap_handle(msvcrt, name, handle, _console_encoding(kernel32, name), "replace", wrappers)


def _is_console_handle(kernel32: Any, handle: int) -> bool:
    import ctypes

    mode = ctypes.c_uint32()
    return bool(kernel32.GetConsoleMode(handle, ctypes.byref(mode)))


def _console_encoding(kernel32: Any, name: str) -> str:
    """控制台流编码：stdin 读输入代码页 GetConsoleCP，输出流读 GetConsoleOutputCP。"""
    cp = kernel32.GetConsoleCP() if name == "stdin" else kernel32.GetConsoleOutputCP()
    return "utf-8" if cp == 65001 else "mbcs"


def _is_utf8(stream: Any) -> bool:
    enc = str(getattr(stream, "encoding", "") or "").lower().replace("-", "").replace("_", "")
    return enc == "utf8"


def _reconfigure_utf8(stream: Any, name: str) -> None:
    """已接管道的流只换编码：MCP 协议与笔记正文都要求 UTF-8。"""
    reconfigure = getattr(stream, "reconfigure", None)
    if reconfigure is None:
        return
    try:
        if name == "stdin":
            reconfigure(encoding="utf-8", errors="replace")
        else:
            reconfigure(encoding="utf-8", errors="replace", newline="\n")
    except Exception:
        pass


def _wrap_handle(msvcrt: Any, name: str, handle: int, encoding: str, errors: str, wrappers: dict[tuple[int, bool], TextIO]) -> None:
    """把 HANDLE 包成文本流替换坏的 sys.*；同向同句柄（2>&1）共用一个 wrapper，防双重关闭。"""
    if not handle or handle == -1:
        return
    key = (handle, name == "stdin")
    if key in wrappers:
        setattr(sys, name, wrappers[key])
        return
    if name == "stdin":
        fd = msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY)
        stream = open(fd, "r", encoding=encoding, errors="replace")
    else:
        fd = msvcrt.open_osfhandle(handle, os.O_WRONLY | os.O_BINARY)
        stream = open(fd, "w", encoding=encoding, errors=errors, newline="\n", buffering=1)
    wrappers[key] = stream
    setattr(sys, name, stream)


# ---------------------------------------------------------------------------
# 入口调度

_LEGACY_PREFIXES = ("--cli", "cli", "--tui", "tui", "--mcp")
_ADV_HEADS = frozenset(_COMMAND_MAP) | {"mcp", "help", "-h", "--help"} | frozenset(_LEGACY_PREFIXES)


def is_adv_argv(argv: list[str]) -> bool:
    """首参数命中命令表 / mcp / 旧前缀时走 CLI-MCP 通道，否则走 GUI。

    命令表优先于路径检测：双击、拖拽、文件关联传入的永远是完整路径，
    裸词命令名不会与真实文件冲突。
    """
    if not argv:
        return False
    return argv[0] in _ADV_HEADS


def dispatch_argv(argv: list[str]) -> int:
    """main 入口：识别命令表 / --cli / --tui / --mcp。"""
    ensure_stdio()
    if not argv:
        return 2
    head, rest = argv[0], argv[1:]
    if head in ("--cli", "cli"):
        return run_cli(rest)
    if head in ("--tui", "tui"):
        return run_tui(rest)
    if head in ("--mcp", "mcp"):
        return _mcp_dispatch(rest)
    return run_cli(argv)
