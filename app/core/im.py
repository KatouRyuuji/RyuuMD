"""本软件内六家即时通讯结合：编码笔记载荷，经可注入传输发送/收回。

通道 id：feishu / popo / dingtalk / wecom / wechat / qq。
传输默认走配置里的 webhook；测试注入 MemoryTransport 做往返。
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Optional, Protocol

PROVIDERS = ("feishu", "popo", "dingtalk", "wecom", "wechat", "qq")

_LABELS = {
    "feishu": "飞书",
    "popo": "网易 POPO",
    "dingtalk": "钉钉",
    "wecom": "企业微信",
    "wechat": "微信",
    "qq": "QQ",
}


class Transport(Protocol):
    def send(self, provider: str, payload: dict[str, Any]) -> dict[str, Any]: ...
    def receive(self, provider: str) -> Optional[dict[str, Any]]: ...


class MemoryTransport:
    """进程内传输：发送的载荷可原样收回，供测试与未配置 webhook 时的本机往返。"""

    def __init__(self) -> None:
        self.boxes: dict[str, list[dict[str, Any]]] = {p: [] for p in PROVIDERS}

    def send(self, provider: str, payload: dict[str, Any]) -> dict[str, Any]:
        if provider not in self.boxes:
            return {"ok": False, "error": f"未知通道: {provider}"}
        self.boxes[provider].append(dict(payload))
        return {"ok": True, "provider": provider}

    def receive(self, provider: str) -> Optional[dict[str, Any]]:
        box = self.boxes.get(provider) or []
        if not box:
            return None
        return dict(box[-1])


class FileOutboxTransport:
    """本机发件箱：未配置 webhook 时笔记仍可发送并取回。"""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.boxes: dict[str, list[dict[str, Any]]] = {p: [] for p in PROVIDERS}
        self._load()

    def _load(self) -> None:
        if not self.path.is_file():
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, TypeError):
            return
        if not isinstance(raw, dict):
            return
        for pid in PROVIDERS:
            items = raw.get(pid)
            if isinstance(items, list):
                self.boxes[pid] = [dict(x) for x in items if isinstance(x, dict)]

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(self.boxes, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self.path)

    def send(self, provider: str, payload: dict[str, Any]) -> dict[str, Any]:
        if provider not in self.boxes:
            return {"ok": False, "error": f"未知通道: {provider}"}
        self.boxes[provider].append(dict(payload))
        if len(self.boxes[provider]) > 20:
            self.boxes[provider] = self.boxes[provider][-20:]
        try:
            self._save()
        except OSError as e:
            return {"ok": False, "error": str(e)}
        return {"ok": True, "provider": provider, "via": "outbox"}

    def receive(self, provider: str) -> Optional[dict[str, Any]]:
        box = self.boxes.get(provider) or []
        if not box:
            return None
        return dict(box[-1])


class ChannelTransport:
    """有 webhook 走 HTTP，否则走本机发件箱。"""

    def __init__(self, webhooks: dict[str, str], outbox: FileOutboxTransport) -> None:
        self.webhooks = dict(webhooks or {})
        self.http = HttpTransport(self.webhooks)
        self.outbox = outbox

    def send(self, provider: str, payload: dict[str, Any]) -> dict[str, Any]:
        if str(self.webhooks.get(provider) or "").strip():
            return self.http.send(provider, payload)
        return self.outbox.send(provider, payload)

    def receive(self, provider: str) -> Optional[dict[str, Any]]:
        if str(self.webhooks.get(provider) or "").strip():
            got = self.http.receive(provider)
            if got:
                return got
        return self.outbox.receive(provider)


class HttpTransport:
    """把编码后的载荷 POST 到该通道 webhook；同时记下最后一封供 receive。"""

    def __init__(self, webhooks: dict[str, str], timeout: int = 15) -> None:
        self.webhooks = dict(webhooks or {})
        self.timeout = timeout
        self._last: dict[str, dict[str, Any]] = {}

    def send(self, provider: str, payload: dict[str, Any]) -> dict[str, Any]:
        url = str(self.webhooks.get(provider) or "").strip()
        if not url:
            return {"ok": False, "error": f"{_LABELS.get(provider, provider)} 未配置 webhook"}
        raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(url, data=raw, method="POST")
        req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                resp.read(800)
        except urllib.error.URLError as e:
            return {"ok": False, "error": str(getattr(e, "reason", e))}
        except OSError as e:
            return {"ok": False, "error": str(e)}
        self._last[provider] = dict(payload)
        return {"ok": True, "provider": provider}

    def receive(self, provider: str) -> Optional[dict[str, Any]]:
        item = self._last.get(provider)
        return dict(item) if item else None


def encode_markdown(provider: str, markdown: str, title: str = "") -> dict[str, Any]:
    """按通道惯例包装 Markdown 正文。"""
    body = markdown if markdown is not None else ""
    head = (title or "").strip() or "RyuuMD"
    if provider == "feishu":
        return {"msg_type": "text", "content": {"text": body}}
    if provider == "popo":
        return {"msgType": "markdown", "content": body}
    if provider == "dingtalk":
        return {"msgtype": "markdown", "markdown": {"title": head, "text": body}}
    if provider == "wecom":
        return {"msgtype": "markdown", "markdown": {"content": body}}
    if provider == "wechat":
        return {"type": "markdown", "content": body}
    if provider == "qq":
        return {"post_type": "message", "message": body}
    raise ValueError(f"未知通道: {provider}")


def decode_markdown(provider: str, payload: dict[str, Any]) -> str:
    """从通道载荷取出 Markdown 正文。"""
    data = payload or {}
    if provider == "feishu":
        content = data.get("content")
        if isinstance(content, dict):
            return str(content.get("text") or "")
        return str(content or "")
    if provider == "popo":
        return str(data.get("content") or "")
    if provider == "dingtalk":
        block = data.get("markdown")
        if isinstance(block, dict):
            return str(block.get("text") or "")
        return ""
    if provider == "wecom":
        block = data.get("markdown")
        if isinstance(block, dict):
            return str(block.get("content") or "")
        return ""
    if provider == "wechat":
        return str(data.get("content") or "")
    if provider == "qq":
        return str(data.get("message") or "")
    raise ValueError(f"未知通道: {provider}")


def send_markdown(
    provider: str,
    markdown: str,
    transport: Transport,
    title: str = "",
) -> dict[str, Any]:
    p = (provider or "").strip()
    if p not in PROVIDERS:
        return {"ok": False, "error": f"未知通道: {provider}"}
    payload = encode_markdown(p, markdown, title=title)
    res = transport.send(p, payload)
    if not res.get("ok"):
        return {"ok": False, "error": res.get("error") or "发送失败", "provider": p}
    return {"ok": True, "provider": p}


def receive_markdown(provider: str, transport: Transport) -> dict[str, Any]:
    p = (provider or "").strip()
    if p not in PROVIDERS:
        return {"ok": False, "error": f"未知通道: {provider}", "content": ""}
    payload = transport.receive(p)
    if not payload:
        return {"ok": False, "error": "没有可收取的消息", "content": "", "provider": p}
    return {"ok": True, "provider": p, "content": decode_markdown(p, payload)}


def provider_list() -> list[dict[str, str]]:
    return [{"id": p, "name": _LABELS[p]} for p in PROVIDERS]
