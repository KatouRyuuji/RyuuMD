"""Anthropic Messages 协议客户端与笔记 AI 能力。

请求为 `POST {base}/v1/messages`，请求头带 `x-api-key` 与 `anthropic-version`。
URL 拼接、ask 前缀判定、prompt 组装与响应文本提取为无 I/O 纯函数；
`complete` 用标准库 urllib 发真实 HTTP，便于本地 mock 服务器断言协议字段。
"""

from __future__ import annotations

import ipaddress
import json
import re
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable, Optional
from urllib.parse import urlparse

from .config import DEFAULT_AI
from .search import list_note_excerpts, SNIPPET_LEN

_ALLOWED_URL_SCHEMES = ("http", "https")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """禁止跟随 3xx，避免 x-api-key 被带到 Location。"""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        raise urllib.error.HTTPError(
            req.full_url, code, "redirect disabled", headers, fp
        )

ANTHROPIC_VERSION = "2023-06-01"
DEFAULT_MAX_TOKENS = 2048
MAX_DOC_CHARS = 80_000
ASK_RE = re.compile(r"(?is)^ask\s+(\S.*)$")

CompleteFn = Callable[..., dict[str, Any]]


def normalize_ai(raw: Any) -> dict[str, Any]:
    """把任意配置收成含 base_url / api_key / model 的字典。"""
    out = dict(DEFAULT_AI)
    if isinstance(raw, dict):
        if "base_url" in raw:
            out["base_url"] = str(raw.get("base_url") or "").strip()
        if "api_key" in raw:
            out["api_key"] = str(raw.get("api_key") or "").strip()
        if "model" in raw:
            out["model"] = str(raw.get("model") or "").strip()
    return out


def merge_ai(existing: Any, incoming: Any) -> dict[str, Any]:
    """合并 AI 配置；incoming 中出现的键覆盖已有值，api_key 为空串表示保持原值。"""
    cur = normalize_ai(existing)
    if not isinstance(incoming, dict):
        return cur
    if "base_url" in incoming:
        cur["base_url"] = str(incoming.get("base_url") or "").strip()
    if "api_key" in incoming:
        key = str(incoming.get("api_key") or "").strip()
        if key:
            cur["api_key"] = key
    if "model" in incoming:
        cur["model"] = str(incoming.get("model") or "").strip()
    return cur


def public_ai(raw: Any) -> dict[str, Any]:
    """给前端的 AI 配置：去掉 API Key，只暴露是否已保存。"""
    data = normalize_ai(raw)
    key = data.get("api_key") or ""
    data["api_key"] = ""
    data["api_key_set"] = bool(key)
    return data


def config_error(cfg: Any) -> str:
    """缺 Base URL / API Key / 模型时的明确错误文案；配齐返回空串。"""
    data = normalize_ai(cfg)
    if not data.get("api_key"):
        return "未配置 Anthropic API Key"
    if not data.get("base_url"):
        return "未配置 Anthropic Base URL"
    if not data.get("model"):
        return "未配置 Anthropic 模型"
    return ""


def messages_url(base_url: str) -> str:
    """把用户填写的 Base URL 收成 Messages 端点；仅允许 http/https。"""
    raw = (base_url or "").strip().rstrip("/")
    if not raw:
        return ""
    lower = raw.lower()
    if lower.endswith("/v1/messages"):
        url = raw
    elif lower.endswith("/v1"):
        url = raw + "/messages"
    else:
        url = raw + "/v1/messages"
    parsed = urlparse(url)
    if parsed.scheme not in _ALLOWED_URL_SCHEMES or not parsed.netloc:
        return ""
    if parsed.scheme == "http":
        host = (parsed.hostname or "").lower()
        try:
            local = host == "localhost" or ipaddress.ip_address(host).is_loopback
        except ValueError:
            local = host == "localhost"
        if not local:
            return ""
    return url


def parse_ask_prefix(query: str) -> tuple[bool, str]:
    """`ask` 加空白后的剩余文本走 AI 问答；`asking` / 无空白问题不触发。"""
    s = (query or "").strip()
    m = ASK_RE.match(s)
    if not m:
        return False, s
    return True, m.group(1).strip()


def build_messages_body(
    model: str,
    user_text: str,
    system: str = "",
    max_tokens: int = DEFAULT_MAX_TOKENS,
    tools: Optional[list[dict[str, Any]]] = None,
) -> dict[str, Any]:
    """组装 Anthropic Messages JSON body。"""
    body: dict[str, Any] = {
        "model": model,
        "max_tokens": int(max_tokens),
        "messages": [{"role": "user", "content": user_text}],
    }
    sys_text = (system or "").strip()
    if sys_text:
        body["system"] = sys_text
    if tools:
        body["tools"] = list(tools)
    return body


def extract_text(payload: Any) -> str:
    """从 Messages 响应 JSON 取出助手文本。"""
    if not isinstance(payload, dict):
        return ""
    content = payload.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict):
                if block.get("type") in (None, "text"):
                    parts.append(str(block.get("text") or ""))
        return "".join(parts)
    return str(payload.get("text") or "")


def extract_tool_use(payload: Any) -> Optional[dict[str, Any]]:
    """从 Messages 响应取出第一块 tool_use（name + input）。"""
    if not isinstance(payload, dict):
        return None
    content = payload.get("content")
    if not isinstance(content, list):
        return None
    for block in content:
        if not isinstance(block, dict):
            continue
        if block.get("type") != "tool_use":
            continue
        name = str(block.get("name") or "").strip()
        if not name:
            continue
        raw_in = block.get("input")
        inp = dict(raw_in) if isinstance(raw_in, dict) else {}
        out: dict[str, Any] = {"name": name, "input": inp}
        if block.get("id"):
            out["id"] = str(block.get("id"))
        return out
    return None


def complete(
    cfg: Any,
    user_text: str,
    system: str = "",
    max_tokens: int = DEFAULT_MAX_TOKENS,
    timeout: int = 90,
    tools: Optional[list[dict[str, Any]]] = None,
) -> dict[str, Any]:
    """对已配置端点发一次非流式 Messages 请求。"""
    data = normalize_ai(cfg)
    err = config_error(data)
    if err:
        return {"ok": False, "error": err, "text": ""}
    url = messages_url(data["base_url"])
    if not url:
        return {"ok": False, "error": "远程 Base URL 必须使用 HTTPS；http 仅允许本机地址", "text": ""}
    body = build_messages_body(data["model"], user_text, system, max_tokens, tools=tools)
    raw = json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=raw, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("x-api-key", data["api_key"])
    req.add_header("anthropic-version", ANTHROPIC_VERSION)
    opener = urllib.request.build_opener(_NoRedirect)
    try:
        with opener.open(req, timeout=timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8", errors="replace") or "{}")
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = e.read()[:800].decode("utf-8", errors="replace")
        except Exception:
            detail = str(e)
        return {"ok": False, "error": f"Anthropic HTTP {e.code}: {detail}".strip(), "text": ""}
    except urllib.error.URLError as e:
        return {"ok": False, "error": f"无法连接 Anthropic: {e.reason}", "text": ""}
    except (OSError, json.JSONDecodeError, TimeoutError, ValueError) as e:
        return {"ok": False, "error": f"Anthropic 请求失败: {e}", "text": ""}
    text = extract_text(payload)
    use = extract_tool_use(payload)
    out: dict[str, Any] = {
        "ok": True,
        "error": "",
        "text": text,
        "stop_reason": str(payload.get("stop_reason") or "") if isinstance(payload, dict) else "",
    }
    if use:
        out["tool_use"] = use
    return out


def _run_complete(cfg: Any, user_text: str, complete_fn: Optional[CompleteFn]) -> dict[str, Any]:
    fn = complete_fn or complete
    return fn(cfg, user_text)


def _excerpts(root: str | Path) -> tuple[list[dict[str, Any]], bool]:
    """仓库摘录 + 是否因篇数上限被截断。"""
    pack = list_note_excerpts(root)
    notes = pack.get("notes") or []
    return notes, bool(pack.get("truncated"))


def _with_trunc(res: dict[str, Any], truncated: bool) -> dict[str, Any]:
    res["truncated"] = bool(truncated)
    return res


def summarize_document(
    title: str,
    content: str,
    cfg: Any,
    complete_fn: Optional[CompleteFn] = None,
) -> dict[str, Any]:
    """用当前文档正文请求概括。"""
    body = content or ""
    if not body.strip():
        return {"ok": False, "error": "当前文档没有可概括的内容", "text": ""}
    err = config_error(cfg)
    if err:
        return {"ok": False, "error": err, "text": ""}
    clipped = body[:MAX_DOC_CHARS]
    user = (
        "请用中文概括下面这篇 Markdown 文档的主题与要点，分条陈述，不要编造文中没有的内容。\n\n"
        f"# {title or '未命名'}\n\n{clipped}"
    )
    res = _run_complete(cfg, user, complete_fn)
    return {"ok": bool(res.get("ok")), "error": res.get("error") or "", "text": res.get("text") or ""}


def summarize_vault(
    root: str,
    cfg: Any,
    complete_fn: Optional[CompleteFn] = None,
) -> dict[str, Any]:
    """用仓库内多篇笔记摘录请求概括。"""
    err = config_error(cfg)
    if err:
        return {"ok": False, "error": err, "text": ""}
    notes, truncated = _excerpts(root)
    if not notes:
        return {"ok": False, "error": "仓库里没有可概括的笔记", "text": "", "truncated": False}
    parts = [f"## {n['rel']}\n{n['excerpt']}" for n in notes]
    user = (
        "请用中文概括这个笔记仓库的主题、覆盖范围与各篇笔记的要点。"
        "下面是仓库内的笔记摘录。\n\n" + "\n\n".join(parts)
    )
    res = _run_complete(cfg, user, complete_fn)
    return _with_trunc(
        {"ok": bool(res.get("ok")), "error": res.get("error") or "", "text": res.get("text") or ""},
        truncated,
    )


def ask_question(
    question: str,
    root: str,
    cfg: Any,
    complete_fn: Optional[CompleteFn] = None,
) -> dict[str, Any]:
    """按用户问题走 AI 回答，可选附带仓库摘录。"""
    q = (question or "").strip()
    if not q:
        return {"ok": False, "kind": "ask", "question": "", "answer": "", "error": "问题为空", "hits": []}
    err = config_error(cfg)
    if err:
        return {"ok": False, "kind": "ask", "question": q, "answer": "", "error": err, "hits": []}
    excerpts = ""
    truncated = False
    if root:
        notes, truncated = _excerpts(root)
        excerpts = "\n\n".join(f"## {n['rel']}\n{n['excerpt']}" for n in notes)
    user = (
        "你是 RyuuMD 笔记助手。根据仓库摘录回答用户问题；摘录不足时请明确说明。用中文回答。\n\n"
        f"仓库摘录：\n{excerpts or '（当前没有打开仓库）'}\n\n"
        f"用户问题：\n{q}"
    )
    res = _run_complete(cfg, user, complete_fn)
    return _with_trunc(
        {
            "ok": bool(res.get("ok")),
            "kind": "ask",
            "question": q,
            "answer": res.get("text") or "",
            "error": res.get("error") or "",
            "hits": [],
        },
        truncated,
    )


def parse_semantic_rels(text: str) -> list[tuple[str, str]]:
    """从模型输出中解析语义命中的相对路径。"""
    s = (text or "").strip()
    if not s:
        return []
    if s.startswith("```"):
        s = re.sub(r"^```(?:json)?\s*", "", s)
        s = re.sub(r"\s*```$", "", s)
    start = s.find("[")
    end = s.rfind("]")
    if start < 0 or end <= start:
        return []
    try:
        arr = json.loads(s[start : end + 1])
    except json.JSONDecodeError:
        return []
    if not isinstance(arr, list):
        return []
    out: list[tuple[str, str]] = []
    for it in arr:
        if isinstance(it, str):
            rel = it.strip()
            if rel:
                out.append((rel, ""))
        elif isinstance(it, dict):
            rel = str(it.get("rel") or it.get("path") or it.get("name") or "").strip()
            why = str(it.get("why") or it.get("snippet") or "").strip()
            if rel:
                out.append((rel, why))
    return out


def search_semantic(
    root: str,
    query: str,
    cfg: Any,
    complete_fn: Optional[CompleteFn] = None,
) -> dict[str, Any]:
    """自然语言查询走 Anthropic，返回 kind=semantic 的命中。"""
    q = (query or "").strip()
    if not q:
        return {"ok": True, "kind": "semantic", "hits": [], "truncated": False}
    err = config_error(cfg)
    if err:
        return {"ok": False, "kind": "semantic", "error": err, "hits": []}
    root_p = Path(root)
    if not root_p.is_dir():
        return {"ok": False, "kind": "semantic", "error": "文件夹不存在", "hits": []}
    notes, truncated = _excerpts(root)
    catalog = "\n".join(f"- {n['rel']}\n  {n['excerpt'][:400]}" for n in notes)
    user = (
        "你是笔记检索助手。根据用户的自然语言查询，从下列笔记中选出语义相关的条目。\n"
        "只输出 JSON 数组，不要 Markdown 围栏，不要其它文字。"
        '每项为 {"rel": "相对路径", "why": "一句话理由"}，最多 8 条。没有相关项时输出 []。\n\n'
        f"查询：\n{q}\n\n笔记目录：\n{catalog}"
    )
    res = _run_complete(cfg, user, complete_fn)
    if not res.get("ok"):
        return {
            "ok": False,
            "kind": "semantic",
            "error": res.get("error") or "语义搜索失败",
            "hits": [],
            "truncated": truncated,
        }
    wanted = parse_semantic_rels(res.get("text") or "")
    by_rel = {str(n["rel"]).replace("\\", "/").lower(): n for n in notes}
    by_name = {str(n["name"]).lower(): n for n in notes}
    hits: list[dict[str, Any]] = []
    seen: set[str] = set()
    for rel, why in wanted:
        key = rel.replace("\\", "/").lower().lstrip("./")
        note = by_rel.get(key) or by_name.get(Path(rel).name.lower())
        if not note:
            continue
        path = str(note["path"])
        if path in seen:
            continue
        seen.add(path)
        snippet = why or str(note.get("excerpt") or "")[:SNIPPET_LEN]
        hits.append(
            {
                "kind": "semantic",
                "path": path,
                "name": note["name"],
                "rel": note["rel"],
                "line": 0,
                "snippet": snippet[:SNIPPET_LEN],
            }
        )
    return {"ok": True, "kind": "semantic", "hits": hits, "truncated": truncated}


def knowledge_tree(
    root: str,
    cfg: Any,
    complete_fn: Optional[CompleteFn] = None,
) -> dict[str, Any]:
    """按仓库笔记目录生成知识谱系（主题分层的 Markdown 大纲）。"""
    err = config_error(cfg)
    if err:
        return {"ok": False, "error": err, "text": ""}
    root_p = Path(root)
    if not root_p.is_dir():
        return {"ok": False, "error": "文件夹不存在", "text": ""}
    notes, truncated = _excerpts(root_p)
    if not notes:
        return {"ok": False, "error": "仓库里没有笔记", "text": "", "truncated": False}
    catalog = "\n".join(f"- {n['rel']}\n  {n['excerpt'][:400]}" for n in notes)
    user = (
        "你是知识管理助手。根据笔记仓库的文件路径与内容摘录，梳理出这份仓库的知识谱系："
        "按主题分层归组，标出每层包含的笔记，笔记名保留原相对路径。"
        "用中文、Markdown 大纲（标题 + 缩进列表）输出，不要编造仓库里没有的笔记。\n\n"
        f"笔记目录：\n{catalog}"
    )
    res = _run_complete(cfg, user, complete_fn)
    return _with_trunc(
        {"ok": bool(res.get("ok")), "error": res.get("error") or "", "text": res.get("text") or ""},
        truncated,
    )


def run_search(
    root: str,
    query: str,
    mode: str = "title",
    cfg: Any = None,
    complete_fn: Optional[CompleteFn] = None,
) -> dict[str, Any]:
    """同一入口：先判定 ask 前缀，再按标题 / 内容 / 语义检索。"""
    from . import search as vault_search

    is_ask, rest = parse_ask_prefix(query)
    if is_ask:
        return ask_question(rest, root, cfg, complete_fn)
    kind = (mode or "title").strip().lower()
    if kind in ("content", "body"):
        return vault_search.search_by_content(root, rest)
    if kind in ("semantic", "ai"):
        return search_semantic(root, rest, cfg, complete_fn)
    return vault_search.search_by_title(root, rest)
