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

    def test_save_file_atomic_leaves_no_tmp(self):
        # 原子写：临时文件写完即 os.replace 收走，目录不留 .tmp 残片
        target = self.root / "a.md"
        res = self.api.save_file(str(target), "内容")
        self.assertTrue(res["ok"])
        self.assertEqual(target.read_text(encoding="utf-8"), "内容")
        self.assertEqual([p.name for p in self.root.iterdir()], ["a.md"])

    def test_new_file_appends_ext_and_rejects_dup(self):
        res = self.api.new_file(str(self.root), "新建文档")
        self.assertTrue(res["ok"])
        self.assertTrue(res["name"].endswith(".md"))
        # 同名拒绝
        dup = self.api.new_file(str(self.root), "新建文档")
        self.assertFalse(dup["ok"])
        self.assertIn("已存在", dup["error"])

    def test_new_file_rejects_path_sep(self):
        res = self.api.new_file(str(self.root), "a/b")
        self.assertFalse(res["ok"])

    def test_new_folder_and_reject_dup(self):
        res = self.api.new_folder(str(self.root), "资料")
        self.assertTrue(res["ok"], res)
        self.assertTrue((self.root / "资料").is_dir())
        dup = self.api.new_folder(str(self.root), "资料")
        self.assertFalse(dup["ok"])
        self.assertFalse(self.api.new_folder(str(self.root), "../x")["ok"])


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


class TestFileOps(unittest.TestCase):
    """文件管理：重命名/移动/删除真实磁盘文件（删除走回收站）+ 配置路径同步。"""

    def setUp(self) -> None:
        self.dir = tempfile.TemporaryDirectory(prefix="ryuumd-ops-")
        self.root = Path(self.dir.name)
        self.api = make_api()
        self.api.config.set("recent_files", [])

    def tearDown(self) -> None:
        self.dir.cleanup()

    def test_rename_success_keeps_content(self):
        p = touch(self.root / "旧名.md", "# 内容不变")
        res = self.api.rename_file(str(p), "新名.md")
        self.assertTrue(res["ok"])
        self.assertEqual(res["name"], "新名.md")
        self.assertFalse(p.exists())
        self.assertEqual(Path(res["path"]).read_text(encoding="utf-8"), "# 内容不变")

    def test_rename_appends_ext_and_rejects_invalid(self):
        p = touch(self.root / "a.md")
        res = self.api.rename_file(str(p), "不带扩展")
        self.assertTrue(res["ok"])
        self.assertEqual(res["name"], "不带扩展.md")
        # 空名 / 含路径分隔符 / 源缺失 / 非 md 一律拒绝
        self.assertFalse(self.api.rename_file(res["path"], "  ")["ok"])
        self.assertFalse(self.api.rename_file(res["path"], "sub/x.md")["ok"])
        self.assertFalse(self.api.rename_file(str(self.root / "nope.md"), "y.md")["ok"])
        txt = touch(self.root / "note.txt")
        self.assertFalse(self.api.rename_file(str(txt), "z.md")["ok"])

    def test_rename_rejects_duplicate(self):
        touch(self.root / "a.md")
        b = touch(self.root / "b.md")
        res = self.api.rename_file(str(b), "a.md")
        self.assertFalse(res["ok"])
        self.assertIn("已存在", res["error"])

    def test_rename_case_only(self):
        # 仅大小写变化也是有效改名：大小写不敏感文件系统上 target.exists() 恒真，
        # 不得被「同名已存在」拦截、也不能静默不变（曾直接返回 ok 但未改名）
        p = touch(self.root / "note.md", "# 内容")
        res = self.api.rename_file(str(p), "NOTE.md")
        self.assertTrue(res["ok"], res)
        self.assertEqual(res["name"], "NOTE.md")
        self.assertEqual(Path(res["path"]).read_text(encoding="utf-8"), "# 内容")
        self.assertIn("NOTE.md", [x.name for x in self.root.iterdir()])

    def test_rename_syncs_recent_and_last_file(self):
        p = touch(self.root / "r.md")
        self.api.read_file(str(p))  # 记录 recent + last_file
        res = self.api.rename_file(str(p), "r2.md")
        self.assertTrue(res["ok"])
        items = self.api.get_recent()["items"]
        self.assertEqual([it["path"] for it in items], [res["path"]])
        self.assertEqual(self.api.config.get("last_file"), res["path"])

    def test_move_success(self):
        p = touch(self.root / "m.md", "正文")
        sub = self.root / "目标"
        sub.mkdir()
        res = self.api.move_file(str(p), str(sub))
        self.assertTrue(res["ok"])
        self.assertFalse(p.exists())
        self.assertEqual(Path(res["path"]).read_text(encoding="utf-8"), "正文")

    def test_move_rejects(self):
        p = touch(self.root / "m.md")
        sub = self.root / "sub"
        touch(sub / "m.md")  # 目标处同名
        self.assertFalse(self.api.move_file(str(p), str(sub))["ok"])
        self.assertFalse(self.api.move_file(str(p), str(self.root / "nope"))["ok"])
        # 移到当前所在文件夹 = 无操作拒绝，原文件不动
        res = self.api.move_file(str(p), str(self.root))
        self.assertFalse(res["ok"])
        self.assertTrue(p.exists())

    def test_delete_goes_to_recycle_bin(self):
        p = touch(self.root / "d.md")
        res = self.api.delete_file(str(p))
        self.assertTrue(res["ok"], res.get("error"))
        self.assertFalse(p.exists())

    def test_delete_cleans_recent_and_last_file(self):
        p = touch(self.root / "d.md")
        keep = touch(self.root / "keep.md")
        self.api.read_file(str(p))
        self.api.read_file(str(keep))
        self.api.read_file(str(p))  # last_file = p
        res = self.api.delete_file(str(p))
        self.assertTrue(res["ok"], res.get("error"))
        items = self.api.get_recent()["items"]
        self.assertEqual([it["path"] for it in items], [str(keep)])
        self.assertEqual(self.api.config.get("last_file"), "")

    def test_delete_rejects_non_md_and_missing(self):
        txt = touch(self.root / "n.txt")
        self.assertFalse(self.api.delete_file(str(txt))["ok"])
        self.assertTrue(txt.exists())
        self.assertFalse(self.api.delete_file(str(self.root / "nope.md"))["ok"])

    def test_duplicate_file_copies_beside(self):
        p = touch(self.root / "note.md", "# hi\n")
        res = self.api.duplicate_file(str(p))
        self.assertTrue(res["ok"], res)
        dest = Path(res["path"])
        self.assertTrue(dest.exists())
        self.assertEqual(dest.read_text(encoding="utf-8"), "# hi\n")
        self.assertIn("副本", dest.name)
        again = self.api.duplicate_file(str(p))
        self.assertTrue(again["ok"], again)
        self.assertNotEqual(again["path"], res["path"])
        txt = touch(self.root / "plain.txt", "x")
        self.assertFalse(self.api.duplicate_file(str(txt))["ok"])


class TestProjects(unittest.TestCase):
    """仓库（项目）管理：添加/去重/重命名/置顶/排序/移除/打开计时。"""

    def setUp(self) -> None:
        self.dir = tempfile.TemporaryDirectory(prefix="ryuumd-proj-")
        self.root = Path(self.dir.name)
        self.api = make_api()
        self.api.config.set("projects", [])

    def tearDown(self) -> None:
        self.dir.cleanup()

    def _mkrepo(self, name: str, files: int = 2) -> Path:
        d = self.root / name
        for i in range(files):
            touch(d / f"n{i}.md")
        return d

    def test_add_and_list_with_stats(self):
        d = self._mkrepo("noteA", files=3)
        res = self.api.add_project(str(d), "")
        self.assertTrue(res["ok"])
        self.assertFalse(res["existed"])
        self.assertEqual(res["project"]["name"], "noteA")
        items = self.api.list_projects()["items"]
        self.assertEqual(len(items), 1)
        self.assertTrue(items[0]["exists"])
        self.assertEqual(items[0]["md_count"], 3)
        self.assertFalse(items[0]["md_count_capped"])

    def test_add_dedupe_same_path(self):
        d = self._mkrepo("noteB")
        self.api.add_project(str(d), "")
        # 同路径不同大小写（Windows 同一目录）→ 判定已存在
        res = self.api.add_project(str(d).upper(), "")
        self.assertTrue(res["ok"])
        self.assertTrue(res["existed"])
        self.assertEqual(len(self.api.list_projects()["items"]), 1)

    def test_add_missing_folder(self):
        res = self.api.add_project(str(self.root / "ghost"), "")
        self.assertFalse(res["ok"])

    def test_custom_name_and_rename(self):
        d = self._mkrepo("noteC")
        pid = self.api.add_project(str(d), "工作笔记")["project"]["id"]
        self.assertEqual(self.api.list_projects()["items"][0]["name"], "工作笔记")
        res = self.api.rename_project(pid, "生活笔记")
        self.assertTrue(res["ok"])
        self.assertEqual(self.api.list_projects()["items"][0]["name"], "生活笔记")
        # 空名拒绝
        self.assertFalse(self.api.rename_project(pid, "  ")["ok"])
        # 不存在的 id
        self.assertFalse(self.api.rename_project("nope", "x")["ok"])

    def test_pin_and_sort_order(self):
        import time as _t

        a = self.api.add_project(str(self._mkrepo("a")), "")["project"]["id"]
        b = self.api.add_project(str(self._mkrepo("b")), "")["project"]["id"]
        c = self.api.add_project(str(self._mkrepo("c")), "")["project"]["id"]
        # b 最近打开过 → 非置顶组内应排最前
        self.api.projects.touch(b)
        # c 置顶 → 全局最前
        self.api.pin_project(c, True)
        names = [it["id"] for it in self.api.list_projects()["items"]]
        self.assertEqual(names[0], c)
        self.assertEqual(names[1], b)
        self.assertEqual(names[2], a)
        # 取消置顶后 c 无打开记录 → 落到最后（按名称）
        self.api.pin_project(c, False)
        _ = _t  # 保留导入占位（touch 已覆盖时间戳路径）
        ids = [it["id"] for it in self.api.list_projects()["items"]]
        self.assertEqual(ids[0], b)

    def test_open_project_touches_and_returns_tree(self):
        d = self._mkrepo("noteD")
        pid = self.api.add_project(str(d), "")["project"]["id"]
        res = self.api.open_project(pid)
        self.assertTrue(res["ok"])
        self.assertIn("tree", res)
        item = self.api.list_projects()["items"][0]
        self.assertGreater(item["last_opened_at"], 0)
        # 不存在的仓库
        self.assertFalse(self.api.open_project("nope")["ok"])

    def test_remove_project(self):
        d = self._mkrepo("noteE")
        pid = self.api.add_project(str(d), "")["project"]["id"]
        self.assertTrue(self.api.remove_project(pid)["ok"])
        self.assertEqual(self.api.list_projects()["items"], [])

    def test_missing_dir_flagged_not_dropped(self):
        d = self._mkrepo("noteF")
        self.api.add_project(str(d), "")
        import shutil

        shutil.rmtree(d)
        items = self.api.list_projects()["items"]
        self.assertEqual(len(items), 1)
        self.assertFalse(items[0]["exists"])
        self.assertIsNone(items[0]["md_count"])


class TestFsutil(unittest.TestCase):
    """count_md_files：计数、忽略目录、预算截断。"""

    def setUp(self) -> None:
        self.dir = tempfile.TemporaryDirectory(prefix="ryuumd-fsu-")
        self.root = Path(self.dir.name)

    def tearDown(self) -> None:
        self.dir.cleanup()

    def test_count_and_ignore(self):
        from app.core.fsutil import count_md_files

        touch(self.root / "a.md")
        touch(self.root / "sub" / "b.md")
        touch(self.root / "c.txt")
        touch(self.root / ".git" / "d.md")
        touch(self.root / "node_modules" / "e.md")
        n, capped = count_md_files(str(self.root))
        self.assertEqual(n, 2)
        self.assertFalse(capped)

    def test_budget_cap(self):
        from app.core.fsutil import count_md_files

        for i in range(30):
            touch(self.root / f"f{i}.md")
        n, capped = count_md_files(str(self.root), budget=10)
        self.assertEqual(n, 10)
        self.assertTrue(capped)


class TestFileAssoc(unittest.TestCase):
    """文件关联：命令行构造与状态查询（不实际写注册表/弹系统对话框）。"""

    def test_launch_command_quotes_and_placeholder(self):
        from app.core import file_assoc

        cmd = file_assoc._launch_command()
        self.assertIn('"%1"', cmd)
        # 开发态：解释器与 main.py 都要带引号（路径可能含空格）
        self.assertTrue(cmd.startswith('"'))
        self.assertIn("main.py", cmd)

    def test_status_shape(self):
        from app.core import file_assoc

        st = file_assoc.status()
        self.assertTrue(st["ok"])
        for key in ("supported", "registered", "is_default"):
            self.assertIn(key, st)
        if sys.platform == "win32":
            self.assertTrue(st["supported"])


class TestMultiWindowApi(unittest.TestCase):
    """多窗口 API：经 window_manager 转发；无 manager 环境下明确报错。"""

    def setUp(self) -> None:
        self.dir = tempfile.TemporaryDirectory(prefix="ryuumd-mw-")
        self.root = Path(self.dir.name)

    def tearDown(self) -> None:
        self.dir.cleanup()

    def test_initial_path_roundtrip(self):
        from app.core.config import Config as _C

        api = Api(_C(), initial_path=str(self.root / "x.md"))
        self.assertEqual(api.get_initial_path(), str(self.root / "x.md"))
        self.assertEqual(make_api().get_initial_path(), "")

    def test_open_new_window_without_manager(self):
        api = make_api()
        self.assertFalse(api.open_new_window("")["ok"])

    def test_open_new_window_with_manager(self):
        calls: list[str] = []

        class FakeManager:
            def create(self, path):  # noqa: ANN001
                calls.append(path)

        from app.core.config import Config as _C

        api = Api(_C(), window_manager=FakeManager())
        self.assertTrue(api.open_new_window(str(self.root))["ok"])
        self.assertEqual(calls, [str(self.root)])

    def test_open_project_new_window(self):
        calls: list[str] = []

        class FakeManager:
            def create(self, path):  # noqa: ANN001
                calls.append(path)

        from app.core.config import Config as _C

        api = Api(_C(), window_manager=FakeManager())
        api.config.set("projects", [])
        d = self.root / "repo"
        touch(d / "a.md")
        pid = api.add_project(str(d), "")["project"]["id"]
        self.assertTrue(api.open_project_new_window(pid)["ok"])
        self.assertEqual(calls, [str(d)])
        # 打开即视为使用过：last_opened_at 更新
        self.assertGreater(api.list_projects()["items"][0]["last_opened_at"], 0)
        # 目录被删除后拒绝并报错
        import shutil

        shutil.rmtree(d)
        self.assertFalse(api.open_project_new_window(pid)["ok"])
        # 不存在的 id
        self.assertFalse(api.open_project_new_window("nope")["ok"])


class TestSingleton(unittest.TestCase):
    """单实例：server 启停、转发成功/失败、token 校验。"""

    def test_forward_roundtrip(self):
        from app.core.config import Config as _C
        from app.core.singleton import InstanceServer, try_forward

        cfg = _C()
        received: list[str] = []
        server = InstanceServer(cfg, on_open=received.append)
        self.assertTrue(server.start())
        try:
            import json as _json
            info = _json.loads((cfg.data_dir / "instance.json").read_text(encoding="utf-8"))
            self.assertIn("pid", info)
            self.assertEqual(int(info["pid"]), os.getpid())
            self.assertTrue(try_forward(cfg, "C:/some/path.md"))
            self.assertTrue(try_forward(cfg, ""))  # 空路径 = 唤起新窗口
            import time as _t

            deadline = _t.time() + 3
            while len(received) < 2 and _t.time() < deadline:
                _t.sleep(0.05)
            self.assertEqual(received, ["C:/some/path.md", ""])
        finally:
            server.stop()
        # server 停止（lock 移除）后转发失败
        self.assertFalse(try_forward(cfg, "x.md"))

    def test_forward_no_instance(self):
        from app.core.config import Config as _C
        from app.core.singleton import try_forward

        cfg = _C()
        (cfg.data_dir / "instance.json").unlink(missing_ok=True)
        self.assertFalse(try_forward(cfg, "a.md"))

    def test_stale_lock_dead_pid_cleared(self):
        import json as _json

        from app.core.config import Config as _C
        from app.core.singleton import try_forward

        cfg = _C()
        lock = cfg.data_dir / "instance.json"
        lock.write_text(
            _json.dumps({"port": 1, "token": "x", "pid": 99999999}),
            encoding="utf-8",
        )
        self.assertFalse(try_forward(cfg, "a.md"))
        self.assertFalse(lock.is_file())

    def test_stale_lock_no_pid_connect_fail_cleared(self):
        import json as _json

        from app.core.config import Config as _C
        from app.core.singleton import try_forward

        cfg = _C()
        lock = cfg.data_dir / "instance.json"
        lock.write_text(_json.dumps({"port": 1, "token": "x"}), encoding="utf-8")
        self.assertFalse(try_forward(cfg, "a.md"))
        self.assertFalse(lock.is_file())

    def test_bad_token_denied(self):
        import json as _json
        import socket as _socket

        from app.core.config import Config as _C
        from app.core.singleton import InstanceServer

        cfg = _C()
        received: list[str] = []
        server = InstanceServer(cfg, on_open=received.append)
        self.assertTrue(server.start())
        try:
            info = _json.loads((cfg.data_dir / "instance.json").read_text(encoding="utf-8"))
            with _socket.create_connection(("127.0.0.1", info["port"]), timeout=2) as s:
                s.sendall(_json.dumps({"token": "wrong", "action": "open", "path": "x"}).encode() + b"\n")
                s.settimeout(2.0)
                self.assertTrue(s.recv(16).startswith(b"denied"))
            self.assertEqual(received, [])
        finally:
            server.stop()


class TestConfig(unittest.TestCase):
    def test_defaults_and_update(self):
        cfg = Config()
        self.assertEqual(cfg.get("theme"), "light")
        self.assertEqual(cfg.get("startup_page"), "home")
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

    def test_atomic_save_leaves_no_tmp(self):
        # 原子写：临时文件写完后 os.replace 收走，目录不留 .tmp 残片，内容完整可读
        cfg = Config()
        cfg.set("theme", "dark")
        self.assertFalse((cfg.data_dir / "config.json.tmp").exists())
        self.assertEqual(Config().get("theme"), "dark")

    def test_palette_and_font_defaults(self):
        from app.core.config import DEFAULTS
        self.assertEqual(DEFAULTS["palette_light"], "sky")
        self.assertEqual(DEFAULTS["palette_dark"], "vampire")
        self.assertEqual(DEFAULTS["font_ui"], "")
        self.assertEqual(DEFAULTS["font_mono"], "")
        self.assertEqual(DEFAULTS["sidebar_width"], 256)
        self.assertEqual(DEFAULTS["focus_mode"], False)
        self.assertEqual(DEFAULTS["typewriter_mode"], False)
        cfg = Config()
        self.assertEqual(cfg.get("palette_light"), "sky")
        self.assertEqual(cfg.get("palette_dark"), "vampire")
        self.assertEqual(cfg.get("font_ui"), "")
        self.assertEqual(cfg.get("font_mono"), "")
        self.assertEqual(cfg.get("sidebar_width"), 256)
        self.assertEqual(cfg.get("focus_mode"), False)
        self.assertEqual(cfg.get("typewriter_mode"), False)

    def test_update_config_writes_palette_and_font(self):
        api = make_api()
        res = api.update_config({
            "palette_light": "sakura",
            "palette_dark": "abyss",
            "font_ui": "KaiTi",
            "font_mono": "Consolas",
            "sidebar_width": 320,
            "focus_mode": True,
            "typewriter_mode": True,
        })
        self.assertTrue(res["ok"])
        data = api.get_config()
        self.assertEqual(data["palette_light"], "sakura")
        self.assertEqual(data["palette_dark"], "abyss")
        self.assertEqual(data["font_ui"], "KaiTi")
        self.assertEqual(data["font_mono"], "Consolas")
        self.assertEqual(data["sidebar_width"], 320)
        self.assertTrue(data["focus_mode"])
        self.assertTrue(data["typewriter_mode"])
        data2 = make_api().get_config()
        self.assertEqual(data2["palette_light"], "sakura")
        self.assertEqual(data2["font_mono"], "Consolas")
        self.assertEqual(data2["sidebar_width"], 320)

    def test_webview_gui_windows_is_edgechromium(self):
        from main import webview_gui

        if sys.platform == "win32":
            self.assertEqual(webview_gui(), "edgechromium")
        else:
            self.assertIsNone(webview_gui())

    def test_data_dir_respects_appdata(self):
        from app.core.config import _data_dir_base

        self.assertEqual(_data_dir_base(), Path(os.environ["APPDATA"]))

    def test_data_dir_darwin_without_appdata(self):
        from unittest.mock import patch

        import app.core.config as cfg

        fake_home = Path("/Users/tester")
        env = {k: v for k, v in os.environ.items() if k != "APPDATA"}
        with patch.dict(os.environ, env, clear=True):
            with patch.object(cfg.sys, "platform", "darwin"):
                with patch.object(cfg.Path, "home", return_value=fake_home):
                    self.assertEqual(
                        cfg._data_dir_base(),
                        fake_home / "Library" / "Application Support",
                    )


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
