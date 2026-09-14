# -*- coding: utf-8 -*-
"""列出/搜索与大文件读写：真实入口 + 三次墙钟上限。"""

from __future__ import annotations

import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_TMP_APPDATA = tempfile.TemporaryDirectory(prefix="ryuumd-test-perf-appdata-")
os.environ["APPDATA"] = _TMP_APPDATA.name

from app.core.api import Api  # noqa: E402
from app.core.config import Config  # noqa: E402

LIST_SEARCH_LIMIT = 2.0
IO_LIMIT = 1.0
TOKEN = "PERF_UNIQUE_NEEDLE_ZX9"
FILE_COUNT = 80
BIG_BYTES = 200 * 1024


def make_api() -> Api:
    cfg = Config()
    cfg.set("edit_mode", "source")
    return Api(cfg)


class TestVaultPerf(unittest.TestCase):
    def setUp(self) -> None:
        self.dir = tempfile.TemporaryDirectory(prefix="ryuumd-perf-")
        self.root = Path(self.dir.name)
        self.api = make_api()
        for i in range(FILE_COUNT):
            body = f"# 笔记 {i}\n\n普通段落 {i}。\n"
            if i == 17:
                body += f"\n含检索词 {TOKEN}\n"
            (self.root / f"note-{i:03d}.md").write_text(body, encoding="utf-8")
        self.big = self.root / "big.md"
        self.big_text = ("# 大文件\n\n" + ("段落。" * 80 + "\n") * 80)
        while len(self.big_text.encode("utf-8")) < BIG_BYTES:
            self.big_text += "填充填充填充填充\n"
        self.big.write_text(self.big_text, encoding="utf-8")

    def tearDown(self) -> None:
        self.dir.cleanup()

    def test_list_and_search_three_times_under_limit(self):
        times: list[float] = []
        for _ in range(3):
            t0 = time.perf_counter()
            listed = self.api.list_folder(str(self.root))
            searched = self.api.search_vault(str(self.root), TOKEN)
            elapsed = time.perf_counter() - t0
            times.append(elapsed)
            self.assertTrue(listed.get("ok"), listed)
            names = []

            def walk(nodes):
                for n in nodes or []:
                    names.append(n.get("name"))
                    walk(n.get("children"))

            walk(listed.get("tree"))
            self.assertIn("note-017.md", names)
            self.assertTrue(searched.get("ok"), searched)
            hits = searched.get("hits") or []
            self.assertTrue(any(TOKEN in (h.get("snippet") or "") for h in hits), hits)
            self.assertLess(elapsed, LIST_SEARCH_LIMIT, f"list+search {elapsed:.3f}s times={times}")
        self.assertEqual(len(times), 3)

    def test_big_file_read_write_three_times_under_limit(self):
        times: list[float] = []
        for i in range(3):
            t0 = time.perf_counter()
            saved = self.api.save_file(str(self.big), self.big_text + f"\n#{i}\n")
            read = self.api.read_file(str(self.big))
            elapsed = time.perf_counter() - t0
            times.append(elapsed)
            self.assertTrue(saved.get("ok"), saved)
            self.assertTrue(read.get("ok"), read)
            self.assertEqual(read.get("content"), self.big_text + f"\n#{i}\n")
            self.assertGreaterEqual(len((read.get("content") or "").encode("utf-8")), BIG_BYTES)
            self.assertLess(elapsed, IO_LIMIT, f"read+write {elapsed:.3f}s times={times}")
        self.assertEqual(len(times), 3)


if __name__ == "__main__":
    unittest.main(verbosity=2)
