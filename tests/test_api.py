# -*- coding: utf-8 -*-
"""RyuuMD Python API 层单元测试（unittest，零三方依赖）。

覆盖:文件读写、文件夹树（过滤/截断/排序）、open_path、会话恢复、配置容错，
以及资源存在性（hljs 主题、二维码）。
运行: python -m unittest discover -s tests -v   或根目录 run_tests.py
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# 测试全程使用临时 APPDATA,隔离用户真实配置(%APPDATA%/RyuuMD/config.json)
_TMP_APPDATA = tempfile.TemporaryDirectory(prefix="ryuumd-test-appdata-")
os.environ["APPDATA"] = _TMP_APPDATA.name

from app.core.api import IGNORE_DIRS, MAX_TREE_DEPTH, MAX_TREE_ENTRIES, Api  # noqa: E402
from app.core.config import Config  # noqa: E402


def make_api() -> Api:
    return Api(Config())


def touch(path: Path, text: str = "x") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


class TestFileIO(unittest.TestCase):
    def setUp(self) -> None:
        self.dir = tempfile.TemporaryDirectory(prefix="ryuumd-io-")
        self.root = Path(self.dir.name)
        self.api = make_api()

    def tearDown(self) -> None:
        self.dir.cleanup()

    def test_read_file_roundtrip_utf8(self):
        p = touch(self.root / "笔记.md", "# 标题\n\n中文内容 ABC")
        res = self.api.read_file(str(p))
        self.assertTrue(res["ok"])
        self.assertEqual(res["name"], "笔记.md")
        self.assertIn("中文内容", res["content"])
        self.assertEqual(res["size"], p.stat().st_size)
        self.assertEqual(res["path"], str(p))

    def test_read_file_missing(self):
        self.assertFalse(self.api.read_file(str(self.root / "nope.md"))["ok"])
        # 目录不是文件
        (self.root / "adir").mkdir()
        self.assertFalse(self.api.read_file(str(self.root / "adir"))["ok"])

    def test_save_file_creates_parent_dirs(self):
        target = self.root / "deep" / "nested" / "a.md"
        res = self.api.save_file(str(target), "内容")
        self.assertTrue(res["ok"])
        self.assertEqual(target.read_text(encoding="utf-8"), "内容")

    def test_new_file_appends_ext_and_rejects_dup(self):
        res = self.api.new_file(str(self.root), "新建文档")
        self.assertTrue(res["ok"])
        self.assertTrue(res["name"].endswith(".md"))
        # 同名拒绝
        dup = self.api.new_file(str(self.root), "新建文档")
        self.assertFalse(dup["ok"])
        self.assertIn("已存在", dup["error"])


class TestFolderTree(unittest.TestCase):
    def setUp(self) -> None:
        self.dir = tempfile.TemporaryDirectory(prefix="ryuumd-tree-")
        self.root = Path(self.dir.name)
        self.api = make_api()

    def tearDown(self) -> None:
        self.dir.cleanup()

    def test_only_md_exts_and_dirs_first_sorted(self):
        touch(self.root / "b.md")
        touch(self.root / "a.md")
        touch(self.root / "note.txt")
        touch(self.root / "zdir" / "inner.md")
        touch(self.root / "adir" / "inner.md")
        res = self.api.list_folder(str(self.root))
        self.assertTrue(res["ok"])
        names = [n["name"] for n in res["tree"]]
        # 目录在前(按名排序),文件在后(按名排序);txt 不出现
        self.assertEqual(names, ["adir", "zdir", "a.md", "b.md"])
        self.assertEqual(res["tree"][0]["type"], "dir")
        self.assertEqual(res["tree"][0]["children"][0]["name"], "inner.md")
        self.assertFalse(res["truncated"])

    def test_md_exts_variants(self):
        for name in ["a.md", "b.markdown", "c.mdown", "d.mkd", "e.mdx"]:
            touch(self.root / name)
        touch(self.root / "f.md.txt")
        res = self.api.list_folder(str(self.root))
        names = [n["name"] for n in res["tree"]]
        self.assertEqual(len(names), 5)
        self.assertNotIn("f.md.txt", names)

    def test_hidden_and_vendor_dirs_filtered(self):
        touch(self.root / "keep.md")
        for d in [".git", ".hidden", ".venv", "node_modules", "__pycache__", "venv", "dist", "build", "target"]:
            touch(self.root / d / "deep.md")
        res = self.api.list_folder(str(self.root))
        names = [n["name"] for n in res["tree"]]
        self.assertEqual(names, ["keep.md"])
        # 过滤的不计入截断
        self.assertFalse(res["truncated"])
        # IGNORE_DIRS 常量与实现一致(防误删)
        for d in ["node_modules", "__pycache__", "venv", "dist", "build"]:
            self.assertIn(d, IGNORE_DIRS)

    def test_depth_truncation(self):
        # 建 MAX_TREE_DEPTH+3 层嵌套,每层放 md
        deep = self.root
        for i in range(MAX_TREE_DEPTH + 3):
            deep = deep / f"d{i}"
            touch(deep / "leaf.md")
        res = self.api.list_folder(str(self.root))
        self.assertTrue(res["ok"])
        self.assertTrue(res["truncated"])
        # 树实际深度不超过上限
        def depth(node, cur=0):
            m = cur
            for n in node:
                if n["type"] == "dir":
                    m = max(m, depth(n["children"], cur + 1))
            return m
        self.assertLessEqual(depth(res["tree"]), MAX_TREE_DEPTH + 1)

    def test_entries_truncation(self):
        for i in range(MAX_TREE_ENTRIES + 50):
            touch(self.root / f"f{i:05d}.md")
        res = self.api.list_folder(str(self.root))
        self.assertTrue(res["ok"])
        self.assertTrue(res["truncated"])

        def count(nodes):
            return sum(1 + (count(n["children"]) if n["type"] == "dir" else 0) for n in nodes)
        self.assertLessEqual(count(res["tree"]), MAX_TREE_ENTRIES)

    def test_missing_folder(self):
        self.assertFalse(self.api.list_folder(str(self.root / "nope"))["ok"])


class TestOpenDroppedFiles(unittest.TestCase):
    """拖入打开（前端捕获转发通道）:open_dropped_files 从 _dnd_state['paths']
    按文件名匹配真实路径,取首个可打开项(md 文件或文件夹)注入前端。"""

    def setUp(self) -> None:
        self.dir = tempfile.TemporaryDirectory(prefix="ryuumd-drop-")
        self.root = Path(self.dir.name)
        self.api = make_api()
        from webview.dom import _dnd_state

        self.dnd = _dnd_state
        self.dnd["paths"].clear()
        self.js_calls: list[str] = []

        js_calls = self.js_calls

        class FakeWindow:
            def evaluate_js(self, js):  # noqa: ANN001
                js_calls.append(js)

        self.api.bind_window(FakeWindow())

    def tearDown(self) -> None:
        self.dnd["paths"].clear()
        self.dir.cleanup()

    def _seed(self, full_path: str) -> None:
        """模拟 FilesDropped 通道写入:(basename, fullpath)。"""
        self.dnd["paths"].append((os.path.basename(full_path), full_path))

    def test_open_md_file(self):
        f = touch(self.root / "笔记.md", "# hi")
        self._seed(str(f))
        res = self.api.open_dropped_files(["笔记.md"])
        self.assertTrue(res["ok"])
        self.assertEqual(res["path"], str(f))
        # 匹配项已消费,且向前端注入了打开调用
        self.assertEqual(len(self.dnd["paths"]), 0)
        self.assertEqual(len(self.js_calls), 1)
        self.assertIn("__openDroppedPath", self.js_calls[0])

    def test_open_folder(self):
        (self.root / "ws").mkdir()
        self._seed(str(self.root / "ws"))
        res = self.api.open_dropped_files(["ws"])
        self.assertTrue(res["ok"])
        self.assertEqual(len(self.js_calls), 1)

    def test_skip_unsupported_and_take_next(self):
        touch(self.root / "a.txt")
        f = touch(self.root / "b.md")
        self._seed(str(self.root / "a.txt"))
        self._seed(str(f))
        res = self.api.open_dropped_files(["a.txt", "b.md"])
        self.assertTrue(res["ok"])
        self.assertEqual(res["path"], str(f))

    def test_no_match(self):
        res = self.api.open_dropped_files(["ghost.md"])
        self.assertFalse(res["ok"])
        self.assertEqual(len(self.js_calls), 0)

    def test_empty_names(self):
        res = self.api.open_dropped_files([])
        self.assertFalse(res["ok"])


class TestOpenPathAndSession(unittest.TestCase):
    def setUp(self) -> None:
        self.dir = tempfile.TemporaryDirectory(prefix="ryuumd-path-")
        self.root = Path(self.dir.name)
        self.api = make_api()

    def tearDown(self) -> None:
        self.dir.cleanup()

    def test_open_path_dispatch(self):
        touch(self.root / "doc.md", "hi")
        touch(self.root / "sub" / "inner.md")
        touch(self.root / "skip.txt")
        # 目录 -> 树
        r1 = self.api.open_path(str(self.root / "sub"))
        self.assertTrue(r1["ok"] and "tree" in r1)
        # md -> 文件
        r2 = self.api.open_path(str(self.root / "doc.md"))
        self.assertTrue(r2["ok"] and "content" in r2)
        # 其他类型 -> 拒绝
        r3 = self.api.open_path(str(self.root / "skip.txt"))
        self.assertFalse(r3["ok"])

    def test_restore_session(self):
        f = touch(self.root / "last.md", "上次内容")
        touch(self.root / "ws" / "w.md")
        self.api.config.set("last_file", str(f))
        self.api.config.set("last_folder", str(self.root / "ws"))
        res = self.api.restore_session()
        self.assertTrue(res["ok"])
        self.assertTrue(res["folder"]["ok"])
        self.assertEqual(res["file"]["content"], "上次内容")
        # 失效路径被忽略
        self.api.config.set("last_file", str(self.root / "gone.md"))
        self.api.config.set("last_folder", str(self.root / "gone_dir"))
        res2 = self.api.restore_session()
        self.assertIsNone(res2["file"])
        self.assertIsNone(res2["folder"])


class TestRecentFiles(unittest.TestCase):
    """最近打开列表：记录（去重/置顶/限长）、查询（exists）、移除、清空。"""

    def setUp(self) -> None:
        self.dir = tempfile.TemporaryDirectory(prefix="ryuumd-recent-")
        self.root = Path(self.dir.name)
        self.api = make_api()
        self.api.config.set("recent_files", [])

    def tearDown(self) -> None:
        self.dir.cleanup()

    def test_record_on_open_and_dedupe_top(self):
        a = touch(self.root / "a.md")
        b = touch(self.root / "b.md")
        self.api.read_file(str(a))
        self.api.read_file(str(b))
        self.api.read_file(str(a))  # 再次打开 a：应去重置顶
        items = self.api.get_recent()["items"]
        self.assertEqual([it["path"] for it in items], [str(a), str(b)])
        self.assertTrue(all(it["kind"] == "file" for it in items))

    def test_record_folder_and_save(self):
        d = self.root / "ws"
        touch(d / "x.md")
        self.api.list_folder(str(d))
        f = touch(self.root / "s.md")
        self.api.save_file(str(f), "内容")
        items = self.api.get_recent()["items"]
        self.assertEqual(items[0]["path"], str(f))
        self.assertEqual(items[1]["kind"], "folder")

    def test_max_length(self):
        from app.core.api import RECENT_MAX

        for i in range(RECENT_MAX + 5):
            self.api.read_file(str(touch(self.root / f"f{i:02d}.md")))
        items = self.api.get_recent()["items"]
        self.assertEqual(len(items), RECENT_MAX)
        # 最新打开的排在最前
        self.assertTrue(items[0]["path"].endswith(f"f{RECENT_MAX + 4:02d}.md"))

    def test_exists_flag(self):
        f = touch(self.root / "alive.md")
        self.api.read_file(str(f))
        f.unlink()  # 删除文件 -> 失效
        touch(self.root / "ok.md")
        self.api.read_file(str(self.root / "ok.md"))
        items = self.api.get_recent()["items"]
        by_name = {it["name"]: it for it in items}
        self.assertFalse(by_name["alive.md"]["exists"])
        self.assertTrue(by_name["ok.md"]["exists"])

    def test_remove_and_clear(self):
        a = touch(self.root / "a.md")
        b = touch(self.root / "b.md")
        self.api.read_file(str(a))
        self.api.read_file(str(b))
        self.api.remove_recent(str(a))
        items = self.api.get_recent()["items"]
        self.assertEqual([it["path"] for it in items], [str(b)])
        self.api.clear_recent()
        self.assertEqual(self.api.get_recent()["items"], [])


class TestConfig(unittest.TestCase):
    def test_defaults_and_update(self):
        cfg = Config()
        self.assertEqual(cfg.get("theme"), "light")
        cfg.update({"theme": "dark", "custom": 1})
        # 新实例读到持久化值(同一 APPDATA 目录)
        cfg2 = Config()
        self.assertEqual(cfg2.get("theme"), "dark")
        self.assertEqual(cfg2.get("custom"), 1)

    def test_corrupt_json_falls_back(self):
        cfg = Config()
        path = cfg.data_dir / "config.json"
        path.write_text("{ 这不是合法 JSON", encoding="utf-8")
        cfg2 = Config()
        self.assertEqual(cfg2.get("theme"), "light")
        self.assertEqual(cfg2.get("display_mode"), "ir")


class TestAssets(unittest.TestCase):
    def test_hljs_themes_exist(self):
        styles = ROOT / "app" / "web" / "vendor" / "vditor" / "dist" / "js" / "highlight.js" / "styles"
        for name in ["github.min.css", "github-dark.min.css"]:
            p = styles / name
            self.assertTrue(p.is_file(), f"hljs 主题缺失: {name}")
            self.assertGreater(p.stat().st_size, 100)

    def test_qrcode_exists(self):
        qr = ROOT / "app" / "web" / "assets" / "QRCode.png"
        self.assertTrue(qr.is_file(), "欢迎页二维码缺失: app/web/assets/QRCode.png")
        self.assertGreater(qr.stat().st_size, 10 * 1024)


if __name__ == "__main__":
    unittest.main(verbosity=2)
