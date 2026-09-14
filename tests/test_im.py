# -*- coding: utf-8 -*-
"""六家即时通讯结合：经已交付入口编码发送再收取，正文一致。"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_TMP_APPDATA = tempfile.TemporaryDirectory(prefix="ryuumd-test-im-appdata-")
os.environ["APPDATA"] = _TMP_APPDATA.name

from app.core.api import Api  # noqa: E402
from app.core.config import Config  # noqa: E402
from app.core.im import MemoryTransport, PROVIDERS, decode_markdown, encode_markdown  # noqa: E402


def make_api() -> Api:
    cfg = Config()
    cfg.set("edit_mode", "source")
    api = Api(cfg)
    api._im_transport = MemoryTransport()
    return api


NOTE = "# 结合测试\n\n这是一篇代表性 Markdown，含 **加粗** 与 `code`。\nUNIQUE_IM_TOKEN_42\n"


class TestImRoundtrip(unittest.TestCase):
    def test_each_provider_send_receive_markdown(self):
        api = make_api()
        listed = api.im_providers()
        self.assertTrue(listed["ok"])
        ids = [it["id"] for it in listed["items"]]
        self.assertEqual(tuple(ids), PROVIDERS)
        for pid in PROVIDERS:
            sent = api.im_send(pid, NOTE, title="测试")
            self.assertTrue(sent.get("ok"), (pid, sent))
            got = api.im_receive(pid)
            self.assertTrue(got.get("ok"), (pid, got))
            self.assertEqual(got.get("content"), NOTE, pid)

    def test_encode_decode_not_identity_skip(self):
        """载荷不是原文本身，必须经 encode/decode 才能还原。"""
        for pid in PROVIDERS:
            payload = encode_markdown(pid, NOTE, title="题")
            self.assertIsInstance(payload, dict)
            self.assertNotEqual(payload, NOTE)
            self.assertEqual(decode_markdown(pid, payload), NOTE)

    def test_unknown_provider(self):
        api = make_api()
        self.assertFalse(api.im_send("slack", NOTE).get("ok"))

    def test_production_outbox_without_webhook(self):
        cfg = Config()
        cfg.set("edit_mode", "source")
        api = Api(cfg)
        sent = api.im_send("feishu", NOTE)
        self.assertTrue(sent.get("ok"), sent)
        got = api.im_receive("feishu")
        self.assertTrue(got.get("ok"), got)
        self.assertEqual(got.get("content"), NOTE)
        self.assertTrue((cfg.data_dir / "im-outbox.json").is_file())


if __name__ == "__main__":
    unittest.main(verbosity=2)
