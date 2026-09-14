"""本应用高级功能：CLI / TUI / MCP / AISkill，共用同一套核心读写。

入口仍是 main.py。``--cli`` / ``--tui`` / ``--mcp`` 在同一进程内调度，
不另开独立应用程序。工具表与 AI 路径共用 invoke_tool。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Callable, Optional, TextIO

from . import anthropic as ai_client
from .api import Api
from .config import Config

Dispatch = Callable[[str, dict[str, Any]], dict[str, Any]]

TOOL_SPECS: list[dict[str, Any]] = [
    {
        "name": "echo",
        "description": "原样返回 text，供 AI / MCP 联调",
        "input": {"text": "string"},
    },
    {
        "name": "read_file",
        "description": "读取 Markdown 文件正文",
        "input": {"path": "string"},
    },
    {
        "name": "write_file",
        "description": "写入 Markdown 文件正文",
        "input": {"path": "string", "content": "string"},
    },
    {
        "name": "list_folder",
        "description": "列出目录下的 Markdown 与子文件夹",
        "input": {"path": "string"},
    },
    {
        "name": "search_vault",
        "description": "在仓库内按标题或正文搜索",
        "input": {"folder": "string", "query": "string"},
    },
    {
        "name": "list_projects",
        "description": "列出已注册仓库",
        "input": {},
    },
    {
        "name": "read_scratch",
        "description": "读取随手记",
        "input": {},
    },
    {
        "name": "write_scratch",
        "description": "写入随手记",
        "input": {"content": "string"},
    },
    {
        "name": "im_send",
        "description": "把 Markdown 发到飞书/POPO/钉钉/企业微信/微信/QQ 通道",
        "input": {"provider": "string", "markdown": "string"},
    },
    {
        "name": "im_receive",
        "description": "从即时通讯通道取回最近一封 Markdown",
        "input": {"provider": "string"},
    },
]

SKILLS: list[dict[str, str]] = [
    {"id": "echo", "tool": "echo", "title": "回声"},
    {"id": "read_note", "tool": "read_file", "title": "读笔记"},
    {"id": "write_note", "tool": "write_file", "title": "写笔记"},
    {"id": "search_notes", "tool": "search_vault", "title": "搜笔记"},
    {"id": "list_vaults", "tool": "list_projects", "title": "列仓库"},
    {"id": "scratch", "tool": "read_scratch", "title": "随手记"},
]


def invoke_tool(api: Api, name: str, arguments: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    """按工具名调用与 GUI 相同的 Api 方法。"""
    args = dict(arguments or {})
    n = (name or "").strip()
    if n == "echo":
        return {"ok": True, "text": str(args.get("text") or "")}
    if n == "read_file":
        return api.read_file(str(args.get("path") or ""))
    if n == "write_file":
        return api.save_file(str(args.get("path") or ""), str(args.get("content") or ""))
    if n == "list_folder":
        return api.list_folder(str(args.get("path") or ""))
    if n == "search_vault":
        return api.search_vault(str(args.get("folder") or ""), str(args.get("query") or ""))
    if n == "list_projects":
        return api.list_projects()
    if n == "read_scratch":
        return api.read_scratch()
    if n == "write_scratch":
        return api.write_scratch(str(args.get("content") or ""))
    if n == "im_send":
        return api.im_send(str(args.get("provider") or ""), str(args.get("markdown") or ""), str(args.get("title") or ""))
    if n == "im_receive":
        return api.im_receive(str(args.get("provider") or ""))
    return {"ok": False, "error": f"未知工具: {n}"}


def list_tools() -> list[dict[str, Any]]:
    return list(TOOL_SPECS)


def list_skills() -> list[dict[str, str]]:
    return list(SKILLS)


def to_anthropic_tools(specs: Optional[list[dict[str, Any]]] = None) -> list[dict[str, Any]]:
    """把本应用工具表收成 Messages API 的 tools 数组。"""
    out: list[dict[str, Any]] = []
    for spec in specs if specs is not None else TOOL_SPECS:
        props: dict[str, Any] = {}
        required: list[str] = []
        for key, typ in (spec.get("input") or {}).items():
            json_type = typ if typ in ("string", "number", "integer", "boolean", "object", "array") else "string"
            props[str(key)] = {"type": json_type}
            required.append(str(key))
        schema: dict[str, Any] = {"type": "object", "properties": props}
        if required:
            schema["required"] = required
        out.append({
            "name": str(spec.get("name") or ""),
            "description": str(spec.get("description") or ""),
            "input_schema": schema,
        })
    return [t for t in out if t["name"]]


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


def _usage() -> str:
    return (
        "RyuuMD 高级功能（同一入口）\n"
        "  --cli read <path>\n"
        "  --cli write <path> [--content TEXT]\n"
        "  --cli list [path]\n"
        "  --cli search <query> [--folder path]\n"
        "  --cli vaults\n"
        "  --cli scratch-read\n"
        "  --cli scratch-write [--content TEXT]\n"
        "  --cli im-send <feishu|popo|dingtalk|wecom|wechat|qq> [--content TEXT]\n"
        "  --cli im-receive <channel>\n"
        "  --tui --once <同上子命令>\n"
        "  --mcp\n"
    )


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
    inn = stdin or sys.stdin
    if not argv or argv[0] in ("-h", "--help"):
        out.write(_usage())
        return 0 if argv and argv[0] in ("-h", "--help") else 2
    cmd = argv[0]
    rest = argv[1:]
    rest, content_flag = _take_flag(rest, "--content")
    rest, folder_flag = _take_flag(rest, "--folder")
    client = api or Api(Config())

    if cmd == "read":
        if not rest:
            out.write("缺少路径\n")
            return 2
        res = client.read_file(rest[0])
        if not res.get("ok"):
            out.write(str(res.get("error") or "读取失败") + "\n")
            return 1
        out.write(str(res.get("content") or ""))
        if not str(res.get("content") or "").endswith("\n"):
            out.write("\n")
        return 0

    if cmd == "write":
        if not rest:
            out.write("缺少路径\n")
            return 2
        body = content_flag if content_flag is not None else inn.read()
        res = client.save_file(rest[0], body)
        if not res.get("ok"):
            out.write(str(res.get("error") or "写入失败") + "\n")
            return 1
        out.write(str(res.get("path") or rest[0]) + "\n")
        return 0

    if cmd == "list":
        path = rest[0] if rest else ""
        if not path:
            out.write("缺少路径\n")
            return 2
        res = client.list_folder(path)
        if not res.get("ok"):
            out.write(str(res.get("error") or "列出失败") + "\n")
            return 1
        out.write(json.dumps(res, ensure_ascii=False, indent=2) + "\n")
        return 0

    if cmd == "search":
        query = " ".join(rest).strip()
        if not query:
            out.write("缺少查询\n")
            return 2
        res = client.search_vault(folder_flag or "", query)
        if not res.get("ok"):
            out.write(str(res.get("error") or "搜索失败") + "\n")
            return 1
        out.write(json.dumps(res, ensure_ascii=False, indent=2) + "\n")
        return 0

    if cmd == "vaults":
        res = client.list_projects()
        out.write(json.dumps(res, ensure_ascii=False, indent=2) + "\n")
        return 0 if res.get("ok") else 1

    if cmd == "scratch-read":
        res = client.read_scratch()
        if not res.get("ok"):
            out.write(str(res.get("error") or "读取失败") + "\n")
            return 1
        out.write(str(res.get("content") or ""))
        if not str(res.get("content") or "").endswith("\n"):
            out.write("\n")
        return 0

    if cmd == "scratch-write":
        body = content_flag if content_flag is not None else inn.read()
        res = client.write_scratch(body)
        if not res.get("ok"):
            out.write(str(res.get("error") or "写入失败") + "\n")
            return 1
        out.write(str(res.get("path") or "") + "\n")
        return 0

    if cmd == "im-send":
        if not rest:
            out.write("缺少通道名\n")
            return 2
        body = content_flag if content_flag is not None else inn.read()
        res = client.im_send(rest[0], body)
        if not res.get("ok"):
            out.write(str(res.get("error") or "发送失败") + "\n")
            return 1
        out.write(json.dumps(res, ensure_ascii=False) + "\n")
        return 0

    if cmd == "im-receive":
        if not rest:
            out.write("缺少通道名\n")
            return 2
        res = client.im_receive(rest[0])
        if not res.get("ok"):
            out.write(str(res.get("error") or "收取失败") + "\n")
            return 1
        out.write(str(res.get("content") or ""))
        if not str(res.get("content") or "").endswith("\n"):
            out.write("\n")
        return 0

    out.write("未知命令: " + cmd + "\n" + _usage())
    return 2


def run_tui(argv: list[str], api: Optional[Api] = None, stdout: Optional[TextIO] = None) -> int:
    """TUI：``--once`` 后接与 CLI 相同的子命令，走同一套核心。"""
    out = stdout or sys.stdout
    args = list(argv)
    if args and args[0] == "--once":
        return run_cli(args[1:], api=api, stdout=out)
    if args:
        return run_cli(args, api=api, stdout=out)
    out.write(_usage())
    return 2


def handle_mcp_message(msg: dict[str, Any], api: Api) -> dict[str, Any]:
    """处理一条 JSON-RPC 消息（tools/list、tools/call）。"""
    mid = msg.get("id")
    method = str(msg.get("method") or "")
    params = msg.get("params") if isinstance(msg.get("params"), dict) else {}
    if method in ("initialize", "notifications/initialized"):
        return {
            "jsonrpc": "2.0",
            "id": mid,
            "result": {"protocolVersion": "2024-11-05", "serverInfo": {"name": "RyuuMD", "version": "1"}},
        }
    if method in ("tools/list", "list_tools"):
        return {"jsonrpc": "2.0", "id": mid, "result": {"tools": list_tools()}}
    if method in ("tools/call", "call_tool"):
        name = str(params.get("name") or "")
        arguments = params.get("arguments") if isinstance(params.get("arguments"), dict) else {}
        result = invoke_tool(api, name, arguments)
        return {"jsonrpc": "2.0", "id": mid, "result": result}
    return {
        "jsonrpc": "2.0",
        "id": mid,
        "error": {"code": -32601, "message": f"Unknown method: {method}"},
    }


def run_mcp(api: Optional[Api] = None, stdin: Optional[TextIO] = None, stdout: Optional[TextIO] = None) -> int:
    """stdio JSON-RPC 循环；空输入结束。"""
    client = api or Api(Config())
    inn = stdin or sys.stdin
    out = stdout or sys.stdout
    for line in inn:
        raw = line.strip()
        if not raw:
            continue
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            out.write(json.dumps({"jsonrpc": "2.0", "error": {"code": -32700, "message": "parse error"}}) + "\n")
            out.flush()
            continue
        if not isinstance(msg, dict):
            continue
        if msg.get("method") in ("notifications/initialized",) and msg.get("id") is None:
            continue
        reply = handle_mcp_message(msg, client)
        out.write(json.dumps(reply, ensure_ascii=False) + "\n")
        out.flush()
    return 0


def is_adv_argv(argv: list[str]) -> bool:
    if not argv:
        return False
    return argv[0] in ("--cli", "cli", "--tui", "tui", "--mcp", "mcp")


def dispatch_argv(argv: list[str]) -> int:
    """main 入口：识别 --cli / --tui / --mcp。"""
    if not argv:
        return 2
    head, rest = argv[0], argv[1:]
    if head in ("--cli", "cli"):
        return run_cli(rest)
    if head in ("--tui", "tui"):
        return run_tui(rest)
    if head in ("--mcp", "mcp"):
        return run_mcp()
    return 2
