# -*- coding: utf-8 -*-
"""Anthropic 配置、Messages 协议、文档/仓库概括、标题/内容/语义搜索与 ask 前缀。"""

from __future__ import annotations

import http.server
import json
import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_TMP_APPDATA = tempfile.TemporaryDirectory(prefix="ryuumd-test-ai-appdata-")
os.environ["APPDATA"] = _TMP_APPDATA.name

from app.core.api import Api  # noqa: E402
from app.core.anthropic import (  # noqa: E402
    ANTHROPIC_VERSION,
    messages_url,
    parse_ask_prefix,
)
from app.core.search import list_note_excerpts  # noqa: E402
from app.core.config import Config  # noqa: E402


def make_api() -> Api:
    cfg = Config()
    cfg.set("edit_mode", "source")
    return Api(cfg)


def touch(path: Path, text: str = "x") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


class _MockHandler(http.server.BaseHTTPRequestHandler):
    def do_POST(self) -> None:
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n)
        rec = {
            "method": self.command,
            "path": self.path,
            "url": "http://%s:%s%s"
            % (self.server.server_address[0], self.server.server_address[1], self.path),
            "headers": {k.lower(): v for k, v in self.headers.items()},
            "body": raw.decode("utf-8", errors="replace"),
        }
        self.server.requests.append(rec)  # type: ignore[attr-defined]
        payload = self.server.response_payload  # type: ignore[attr-defined]
        data = json.dumps(payload).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, fmt: str, *args: Any) -> None:
        return


def start_mock(payload: dict[str, Any]) -> tuple[http.server.ThreadingHTTPServer, threading.Thread]:
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _MockHandler)
    server.response_payload = payload  # type: ignore[attr-defined]
    server.requests = []  # type: ignore[attr-defined]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


def stop_mock(server: http.server.ThreadingHTTPServer) -> None:
    server.shutdown()
    server.server_close()


def assistant_payload(text: str) -> dict[str, Any]:
    return {
        "id": "msg_test",
        "type": "message",
        "role": "assistant",
        "content": [{"type": "text", "text": text}],
        "model": "claude-test",
        "stop_reason": "end_turn",
    }


class TestAskPrefix(unittest.TestCase):
    def test_ask_prefix_rules(self):
        on, rest = parse_ask_prefix("ask 什么是 WebDAV")
        self.assertTrue(on)
        self.assertEqual(rest, "什么是 WebDAV")
        on, rest = parse_ask_prefix("ASK  测试")
        self.assertTrue(on)
        self.assertEqual(rest, "测试")
        self.assertFalse(parse_ask_prefix("asking foo")[0])
        self.assertFalse(parse_ask_prefix("ask")[0])
        self.assertFalse(parse_ask_prefix("ask ")[0])
        self.assertEqual(parse_ask_prefix("asking foo")[1], "asking foo")


class TestMessagesUrl(unittest.TestCase):
    def test_join_variants(self):
        self.assertTrue(messages_url("https://api.anthropic.com").endswith("/v1/messages"))
        self.assertTrue(messages_url("https://x.example/v1").endswith("/v1/messages"))
        self.assertEqual(messages_url("https://x.example/v1/messages"), "https://x.example/v1/messages")

    def test_rejects_non_http_schemes(self):
        self.assertEqual(messages_url("file:///C:/tmp"), "")
        self.assertEqual(messages_url("javascript:alert(1)"), "")
        self.assertEqual(messages_url("ftp://example.com"), "")


class TestAiConfigRoundtrip(unittest.TestCase):
    def test_update_get_config_roundtrip(self):
        api = make_api()
        res = api.update_config({
            "ai": {
                "base_url": "http://127.0.0.1:9876",
                "api_key": "sk-test-roundtrip",
                "model": "claude-test-model",
            }
        })
        self.assertTrue(res["ok"])
        # get_config 脱敏：不下发真实 Key，只暴露 api_key_set
        data = api.get_config()
        self.assertEqual(data["ai"]["base_url"], "http://127.0.0.1:9876")
        self.assertEqual(data["ai"]["api_key"], "")
        self.assertTrue(data["ai"]["api_key_set"])
        self.assertEqual(data["ai"]["model"], "claude-test-model")
        again = make_api().get_config()
        self.assertEqual(again["ai"]["api_key"], "")
        self.assertTrue(again["ai"]["api_key_set"])
        self.assertEqual(again["ai"]["model"], "claude-test-model")
        # 磁盘上的真实 Key 不丢
        self.assertEqual(make_api().config.get("ai")["api_key"], "sk-test-roundtrip")

    def test_empty_key_keeps_saved(self):
        api = make_api()
        api.update_config({"ai": {"api_key": "sk-keep-me", "model": "m1"}})
        res = api.update_config({"ai": {"api_key": "", "model": "m2"}})
        self.assertTrue(res["ok"])
        cfg = make_api().config.get("ai")
        self.assertEqual(cfg["api_key"], "sk-keep-me")
        self.assertEqual(cfg["model"], "m2")
        data = api.get_config()
        self.assertTrue(data["ai"]["api_key_set"])


class TestAnthropicHttp(unittest.TestCase):
    def setUp(self) -> None:
        self.api = make_api()
        self.dir = tempfile.TemporaryDirectory(prefix="ryuumd-ai-http-")
        self.root = Path(self.dir.name)

    def tearDown(self) -> None:
        self.dir.cleanup()

    def _point_at(self, server: http.server.ThreadingHTTPServer, model: str = "claude-http-model") -> None:
        host, port = server.server_address
        self.api.update_config({
            "ai": {
                "base_url": "http://%s:%s" % (host, port),
                "api_key": "sk-mock-key",
                "model": model,
            }
        })

    def test_messages_protocol_fields(self):
        server, _t = start_mock(assistant_payload("MOCK_COMPLETION_TEXT"))
        try:
            self._point_at(server)
            doc = touch(self.root / "note.md", "# 协议\n\nHelloBody")
            res = self.api.summarize_document(str(doc), "", str(self.root))
            self.assertTrue(res["ok"], res)
            self.assertIn("MOCK_COMPLETION_TEXT", res["text"])
            self.assertEqual(len(server.requests), 1)
            rec = server.requests[0]
            self.assertEqual(rec["method"], "POST")
            self.assertTrue(rec["url"].endswith("/v1/messages"))
            self.assertEqual(rec["headers"].get("x-api-key"), "sk-mock-key")
            self.assertEqual(rec["headers"].get("anthropic-version"), ANTHROPIC_VERSION)
            body = json.loads(rec["body"])
            self.assertEqual(body["model"], "claude-http-model")
            self.assertIn("HelloBody", rec["body"])
        finally:
            stop_mock(server)

    def test_file_url_rejected(self):
        self.api.update_config({
            "ai": {"base_url": "file:///C:/tmp", "api_key": "sk-x", "model": "m"},
        })
        res = self.api.summarize_document("", "# t\n\nbody", "")
        self.assertFalse(res["ok"])
        self.assertIn("http", res.get("error") or "")

    def test_redirect_does_not_follow(self):
        class _RedirectHandler(http.server.BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                n = int(self.headers.get("Content-Length") or 0)
                self.rfile.read(n)
                self.server.hits.append(self.path)  # type: ignore[attr-defined]
                if self.path.rstrip("/").endswith("/v1/messages"):
                    host, port = self.server.server_address
                    self.send_response(302)
                    self.send_header("Location", "http://%s:%s/steal" % (host, port))
                    self.end_headers()
                    return
                self.server.stolen = True  # type: ignore[attr-defined]
                self.send_response(200)
                self.end_headers()

            def do_GET(self) -> None:
                self.server.stolen = True  # type: ignore[attr-defined]
                self.server.hits.append(self.path)  # type: ignore[attr-defined]
                self.send_response(200)
                self.end_headers()

            def log_message(self, fmt: str, *args: Any) -> None:
                return

        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _RedirectHandler)
        server.hits = []  # type: ignore[attr-defined]
        server.stolen = False  # type: ignore[attr-defined]
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            self._point_at(server)
            res = self.api.summarize_document("", "# t\n\nHelloRedirect", "")
            self.assertFalse(res["ok"], res)
            self.assertFalse(server.stolen)  # type: ignore[attr-defined]
            self.assertTrue(any("messages" in p for p in server.hits))  # type: ignore[attr-defined]
            self.assertFalse(any(p == "/steal" for p in server.hits))  # type: ignore[attr-defined]
        finally:
            stop_mock(server)

    def test_missing_key_fails(self):
        self.api.update_config({
            "ai": {
                "base_url": "http://127.0.0.1:9",
                "api_key": "",
                "model": "claude-x",
            }
        })
        res = self.api.summarize_document("", "# 空 key\n\n正文", "")
        self.assertFalse(res["ok"])
        self.assertTrue(res.get("error"))
        self.assertNotIn("MOCK", res.get("text") or "")


class TestSummarize(unittest.TestCase):
    def setUp(self) -> None:
        self.api = make_api()
        self.dir = tempfile.TemporaryDirectory(prefix="ryuumd-ai-sum-")
        self.root = Path(self.dir.name)
        touch(self.root / "AlphaNote.md", "# Alpha\n\nUNIQUE_DOC_BODY_ALPHA raft quorum")
        touch(self.root / "BetaNote.md", "# Beta\n\nUNIQUE_VAULT_BODY_BETA webdav sync")

    def tearDown(self) -> None:
        self.dir.cleanup()

    def test_document_sends_body_and_returns_completion(self):
        server, _t = start_mock(assistant_payload("DOC_SUMMARY_FROM_MOCK"))
        try:
            host, port = server.server_address
            self.api.update_config({
                "ai": {
                    "base_url": "http://%s:%s" % (host, port),
                    "api_key": "sk-doc",
                    "model": "claude-sum",
                }
            })
            p = self.root / "AlphaNote.md"
            res = self.api.summarize_document(str(p), p.read_text(encoding="utf-8"), str(self.root))
            self.assertTrue(res["ok"], res)
            self.assertIn("DOC_SUMMARY_FROM_MOCK", res["text"])
            rec = server.requests[0]
            self.assertIn("UNIQUE_DOC_BODY_ALPHA", rec["body"])
        finally:
            stop_mock(server)

    def test_excerpts_report_truncation(self):
        for i in range(3):
            touch(self.root / ("Extra%d.md" % i), "body %d" % i)
        pack = list_note_excerpts(self.root, max_files=2)
        self.assertEqual(len(pack["notes"]), 2)
        self.assertTrue(pack["truncated"])

    def test_vault_sends_multiple_notes(self):
        server, _t = start_mock(assistant_payload("VAULT_SUMMARY_FROM_MOCK"))
        try:
            host, port = server.server_address
            self.api.update_config({
                "ai": {
                    "base_url": "http://%s:%s" % (host, port),
                    "api_key": "sk-vault",
                    "model": "claude-sum",
                }
            })
            res = self.api.summarize_vault(str(self.root))
            self.assertTrue(res["ok"], res)
            self.assertIn("VAULT_SUMMARY_FROM_MOCK", res["text"])
            rec = server.requests[0]
            self.assertIn("AlphaNote.md", rec["body"])
            self.assertIn("BetaNote.md", rec["body"])
            self.assertIn("UNIQUE_DOC_BODY_ALPHA", rec["body"])
            self.assertIn("UNIQUE_VAULT_BODY_BETA", rec["body"])
        finally:
            stop_mock(server)

    def test_document_rejects_outside_vault(self):
        outside = tempfile.TemporaryDirectory(prefix="ryuumd-ai-out-")
        try:
            secret = Path(outside.name) / "secret.md"
            secret.write_text("SECRET_BODY_MUST_NOT_LEAVE", encoding="utf-8")
            self.api.update_config({
                "ai": {"base_url": "http://127.0.0.1:9", "api_key": "sk-x", "model": "m"},
            })
            res = self.api.summarize_document(str(secret), "", str(self.root))
            self.assertFalse(res["ok"])
            self.assertIn("仓库", res.get("error") or "")
            self.assertNotIn("SECRET", res.get("text") or "")
        finally:
            outside.cleanup()

    def test_summarize_missing_config(self):
        self.api.update_config({"ai": {"api_key": "", "base_url": "", "model": ""}})
        doc = self.api.summarize_document(str(self.root / "AlphaNote.md"), "", str(self.root))
        self.assertFalse(doc["ok"])
        self.assertTrue(doc.get("error"))
        vault = self.api.summarize_vault(str(self.root))
        self.assertFalse(vault["ok"])
        self.assertTrue(vault.get("error"))


class TestSearchModes(unittest.TestCase):
    def setUp(self) -> None:
        self.api = make_api()
        self.dir = tempfile.TemporaryDirectory(prefix="ryuumd-ai-search-")
        self.root = Path(self.dir.name)
        # 标题含查询、正文不含
        touch(self.root / "WebDAV指南.md", "# 其它\n\n正文里没有那个词")
        # 正文含查询、文件名不含
        touch(self.root / "plain.md", "# 笔记\n\n这里讲解 OmegaContentToken 的用法")
        # 语义命中目标：查询不会作为子串出现
        touch(self.root / "GammaNote.md", "# Gamma\n\nRaft and quorum keep replicas aligned.")

    def tearDown(self) -> None:
        self.dir.cleanup()

    def test_title_hits_filename_not_body(self):
        res = self.api.search_notes(str(self.root), "WebDAV", "title")
        self.assertTrue(res["ok"], res)
        names = {h["name"] for h in res["hits"]}
        self.assertIn("WebDAV指南.md", names)
        self.assertNotIn("plain.md", names)
        self.assertTrue(all(h["kind"] == "title" for h in res["hits"]))

    def test_content_hits_body_not_filename(self):
        res = self.api.search_notes(str(self.root), "OmegaContentToken", "content")
        self.assertTrue(res["ok"], res)
        names = {h["name"] for h in res["hits"]}
        self.assertIn("plain.md", names)
        self.assertNotIn("WebDAV指南.md", names)
        self.assertTrue(all(h["kind"] == "content" for h in res["hits"]))

    def test_semantic_uses_anthropic_not_substring(self):
        server, _t = start_mock(assistant_payload(
            json.dumps([{"rel": "GammaNote.md", "why": "consensus replicas"}], ensure_ascii=False)
        ))
        try:
            host, port = server.server_address
            self.api.update_config({
                "ai": {
                    "base_url": "http://%s:%s" % (host, port),
                    "api_key": "sk-sem",
                    "model": "claude-sem",
                }
            })
            query = "ideas about distributed consensus across replicas"
            res = self.api.search_notes(str(self.root), query, "semantic")
            self.assertTrue(res["ok"], res)
            self.assertTrue(server.requests, "semantic path must call Anthropic")
            self.assertTrue(any(h["kind"] == "semantic" for h in res["hits"]))
            names = {h["name"] for h in res["hits"]}
            self.assertIn("GammaNote.md", names)
            # 查询不是任何文件的子串，证明没有退化成纯扫描
            for p in self.root.rglob("*.md"):
                self.assertNotIn(query.lower(), p.read_text(encoding="utf-8").lower())
                self.assertNotIn(query.lower(), p.name.lower())
        finally:
            stop_mock(server)

    def test_ask_prefix_routes_to_answer(self):
        server, _t = start_mock(assistant_payload("ASK_ANSWER_FROM_MOCK"))
        try:
            host, port = server.server_address
            self.api.update_config({
                "ai": {
                    "base_url": "http://%s:%s" % (host, port),
                    "api_key": "sk-ask",
                    "model": "claude-ask",
                }
            })
            res = self.api.search_notes(str(self.root), "ask 什么是 WebDAV", "title")
            self.assertEqual(res.get("kind"), "ask")
            self.assertEqual(res.get("question"), "什么是 WebDAV")
            self.assertIn("ASK_ANSWER_FROM_MOCK", res.get("answer") or "")
            self.assertTrue(res["ok"], res)
            rec = server.requests[0]
            self.assertIn("什么是 WebDAV", rec["body"])
            upper = self.api.search_notes(str(self.root), "ASK  测试", "content")
            self.assertEqual(upper.get("kind"), "ask")
            self.assertEqual(upper.get("question"), "测试")
        finally:
            stop_mock(server)

    def test_non_ask_prefix_stays_keyword(self):
        asking = self.api.search_notes(str(self.root), "asking foo", "title")
        self.assertNotEqual(asking.get("kind"), "ask")
        self.assertNotIn("answer", asking)
        bare = self.api.search_notes(str(self.root), "ask", "title")
        self.assertNotEqual(bare.get("kind"), "ask")
        self.assertIn("hits", bare)


class TestKnowledgeAndAsk(unittest.TestCase):
    def setUp(self) -> None:
        self.api = make_api()
        self.dir = tempfile.TemporaryDirectory(prefix="ryuumd-ai-tree-")
        self.root = Path(self.dir.name)
        touch(self.root / "分布式.md", "# 分布式\n\n共识算法与复制。")
        touch(self.root / "存储.md", "# 存储\n\nLSM 与索引。")

    def tearDown(self) -> None:
        self.dir.cleanup()

    def _point_at(self, server: http.server.ThreadingHTTPServer) -> None:
        host, port = server.server_address
        self.api.update_config({
            "ai": {"base_url": "http://%s:%s" % (host, port), "api_key": "sk-mock", "model": "m"},
        })

    def test_ask_ai_returns_answer(self):
        server, _t = start_mock(assistant_payload("MOCK_ANSWER"))
        try:
            self._point_at(server)
            res = self.api.ask_ai(str(self.root), "什么是共识")
            self.assertTrue(res["ok"], res)
            self.assertEqual(res["answer"], "MOCK_ANSWER")
            self.assertIn("什么是共识", server.requests[0]["body"])  # type: ignore[attr-defined]
        finally:
            stop_mock(server)

    def test_knowledge_tree_cache_and_refresh(self):
        server, _t = start_mock(assistant_payload("MOCK_TREE"))
        try:
            self._point_at(server)
            first = self.api.knowledge_tree(str(self.root))
            self.assertTrue(first["ok"], first)
            self.assertEqual(first["text"], "MOCK_TREE")
            self.assertEqual(len(server.requests), 1)  # type: ignore[attr-defined]
            # 第二次走缓存，不再发请求
            second = self.api.knowledge_tree(str(self.root))
            self.assertEqual(second["text"], "MOCK_TREE")
            self.assertTrue(second.get("cached"))
            self.assertEqual(len(server.requests), 1)  # type: ignore[attr-defined]
            # 路径写法不同仍命中同一缓存键
            slashed = self.api.knowledge_tree(str(self.root) + os.sep)
            self.assertTrue(slashed.get("cached"))
            self.assertEqual(len(server.requests), 1)  # type: ignore[attr-defined]
            # 写盘后缓存失效
            self.api.save_file(str(self.root / "分布式.md"), "# 分布式\n\n已改。")
            after_save = self.api.knowledge_tree(str(self.root))
            self.assertTrue(after_save["ok"], after_save)
            self.assertFalse(after_save.get("cached"))
            self.assertEqual(len(server.requests), 2)  # type: ignore[attr-defined]
            # 复制笔记同样失效
            self.api.knowledge_tree(str(self.root))  # 回填缓存
            self.assertEqual(len(server.requests), 2)  # type: ignore[attr-defined]
            dup = self.api.duplicate_file(str(self.root / "分布式.md"))
            self.assertTrue(dup["ok"], dup)
            after_dup = self.api.knowledge_tree(str(self.root))
            self.assertFalse(after_dup.get("cached"))
            self.assertEqual(len(server.requests), 3)  # type: ignore[attr-defined]
            # refresh 绕过缓存重新生成
            third = self.api.knowledge_tree(str(self.root), refresh=True)
            self.assertTrue(third["ok"], third)
            self.assertEqual(len(server.requests), 4)  # type: ignore[attr-defined]
            self.assertIn("分布式.md", server.requests[-1]["body"])  # type: ignore[attr-defined]
        finally:
            stop_mock(server)

    def test_knowledge_tree_requires_folder_and_key(self):
        res = self.api.knowledge_tree("")
        self.assertFalse(res["ok"])
        self.assertIn("仓库", res["error"])
        # config.set 直写绕过 merge（merge 语义是空 key 保持原值），构造真实缺配置
        self.api.config.set("ai", {"base_url": "", "api_key": "", "model": ""})
        res2 = self.api.knowledge_tree(str(self.root))
        self.assertFalse(res2["ok"])
        self.assertIn("API Key", res2["error"])


class TestUiSurface(unittest.TestCase):
    def test_settings_and_search_markup(self):
        web = ROOT / "app" / "web"
        html = (web / "index.html").read_text(encoding="utf-8")
        settings = (web / "js" / "settings.js").read_text(encoding="utf-8")
        palette = (web / "js" / "palette.js").read_text(encoding="utf-8")
        appjs = (web / "js" / "app.js").read_text(encoding="utf-8")
        self.assertIn('id="ai-base-url"', settings)
        self.assertIn('id="ai-api-key"', settings)
        self.assertIn('id="ai-model"', settings)
        self.assertIn('data-tab="ai"', settings)
        self.assertIn('id="ai-sidebar"', html)
        self.assertIn('id="ai-sum-doc"', html)
        self.assertIn('id="ai-sum-vault"', html)
        self.assertIn('id="ai-knowledge"', html)
        self.assertIn('id="ai-ask-input"', html)
        self.assertIn("ai_panel.js", html)
        self.assertIn('id="pal-modes"', html)
        self.assertIn('data-mode="title"', html)
        self.assertIn('data-mode="content"', html)
        self.assertIn('data-mode="semantic"', html)
        self.assertIn("pal-mode-kbd", html)
        self.assertIn("回车", html)
        self.assertIn('id="pal-answer"', html)
        self.assertIn("isAskQuery", palette)
        self.assertIn("function searchFiresOnInput", palette)
        self.assertIn("按 Enter 提问", palette)
        self.assertIn("Enter 搜索", palette)
        self.assertIn("FOOT_ENTER_SEARCH", palette)
        self.assertIn("search_notes", appjs)
        self.assertIn("typeof a.search_notes", appjs)
        self.assertIn("state.config.last_folder", appjs)
        self.assertIn("renderSearchError", palette)
        self.assertIn("btn-push-source", html)
        self.assertIn("btn-merge-source", html)
        self.assertIn("set-edit-mode", (web / "js" / "settings.js").read_text(encoding="utf-8"))
        self.assertIn("welcome-edit-mode", (web / "js" / "welcome.js").read_text(encoding="utf-8"))
        self.assertIn("workdir_push", appjs)
        self.assertIn("pushCurrentToSource", appjs)
        self.assertIn("AiPanel", appjs)
        self.assertIn("window.AiPanel.knowledge()", appjs)
        self.assertNotIn("knowledge(false)", appjs)
        ai_panel = (web / "js" / "ai_panel.js").read_text(encoding="utf-8")
        self.assertIn("registerEscape", ai_panel)
        self.assertIn("function markdown", ai_panel)
        self.assertIn("setBusy", ai_panel)
        css = (web / "css" / "palette.css").read_text(encoding="utf-8")
        self.assertIn("--primary", css)
        self.assertIn(".pal-mode", css)
        self.assertIn(".pal-mode-kbd", css)


if __name__ == "__main__":
    unittest.main()
