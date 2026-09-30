# -*- coding: utf-8 -*-
"""工作副本：进入仓库时复制、只改副本、保存至源 / 合并源。"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_TMP_APPDATA = tempfile.TemporaryDirectory(prefix="ryuumd-workdir-appdata-")
os.environ["APPDATA"] = _TMP_APPDATA.name

from app.core.api import Api  # noqa: E402
from app.core.config import Config  # noqa: E402


def make_api(mode: str = "source") -> Api:
    cfg = Config()
    cfg.set("edit_mode", mode)
    return Api(cfg)


class TestWorkdirCopyAndPush(unittest.TestCase):
    def setUp(self) -> None:
        self.dir = tempfile.TemporaryDirectory(prefix="ryuumd-workdir-")
        self.root = Path(self.dir.name)
        (self.root / "docs").mkdir()
        (self.root / "hello.md").write_text("# 源\n\n原文\n", encoding="utf-8")
        (self.root / "docs" / "note.md").write_text("# 笔记\n\n正文\n", encoding="utf-8")
        self.api = make_api("workdir")

    def tearDown(self) -> None:
        self.dir.cleanup()

    def test_source_mode_does_not_clone(self):
        api = make_api("source")
        res = api.list_folder(str(self.root))
        self.assertTrue(res["ok"])
        self.assertEqual(res["root"], str(self.root))
        self.assertEqual(res.get("edit_mode"), "source")
        self.assertNotIn("workcopies", res["root"].replace("\\", "/"))

    def test_list_folder_creates_copy_and_keeps_source_in_recent(self):
        res = self.api.list_folder(str(self.root))
        self.assertTrue(res["ok"], res)
        self.assertEqual(res["edit_mode"], "workdir")
        self.assertNotEqual(res["root"], str(self.root))
        self.assertEqual(res["source_root"], str(self.root.resolve()))
        self.assertGreater(res.get("workdir_new_files", 0), 0)
        self.assertEqual(self.api.config.get("last_folder"), str(self.root.resolve()))
        recent = self.api.get_recent()["items"]
        self.assertEqual(recent[0]["path"], str(self.root.resolve()))
        work_hello = Path(res["root"]) / "hello.md"
        self.assertTrue(work_hello.is_file())

    def test_save_writes_copy_not_source(self):
        listed = self.api.list_folder(str(self.root))
        work = Path(listed["root"]) / "hello.md"
        res = self.api.save_file(str(work), "# 源\n\n副本已改\n")
        self.assertTrue(res["ok"], res)
        self.assertIn("副本已改", work.read_text(encoding="utf-8"))
        self.assertEqual((self.root / "hello.md").read_text(encoding="utf-8"), "# 源\n\n原文\n")

    def test_read_source_path_opens_copy(self):
        self.api.list_folder(str(self.root))
        res = self.api.read_file(str(self.root / "hello.md"))
        self.assertTrue(res["ok"], res)
        self.assertNotEqual(res["path"], str(self.root / "hello.md"))
        self.assertEqual(Path(res["source_path"]).name, "hello.md")
        self.assertEqual(self.api.config.get("last_file"), res["source_path"])

    def test_move_between_workdirs_returns_target_source_path(self):
        source = self.api.list_folder(str(self.root))
        target_root = self.root.with_name(self.root.name + "-target")
        (target_root / "docs").mkdir(parents=True)
        (target_root / "docs" / "existing.md").write_text("target", encoding="utf-8")
        target = self.api.list_folder(str(target_root))
        source_file = Path(source["root"]) / "hello.md"
        target_folder = Path(target["root"]) / "docs"

        res = self.api.move_file(str(source_file), str(target_folder))

        self.assertTrue(res["ok"], res)
        self.assertEqual(Path(res["path"]), target_folder / "hello.md")
        self.assertEqual(Path(res["source_path"]), target_root.resolve() / "docs" / "hello.md")

    def test_push_writes_source(self):
        listed = self.api.list_folder(str(self.root))
        work = str(Path(listed["root"]) / "hello.md")
        self.api.save_file(work, "# 源\n\n副本已改\n")
        st = self.api.workdir_status(work)
        self.assertEqual(st["status"], "copy_ahead")
        pushed = self.api.workdir_push(work)
        self.assertTrue(pushed["ok"], pushed)
        self.assertTrue(pushed["pushed"])
        self.assertEqual((self.root / "hello.md").read_text(encoding="utf-8"), "# 源\n\n副本已改\n")
        self.assertEqual(self.api.workdir_status(work)["status"], "in_sync")

    def test_merge_pulls_source(self):
        listed = self.api.list_folder(str(self.root))
        work = Path(listed["root"]) / "hello.md"
        (self.root / "hello.md").write_text("# 源\n\n源已更新\n", encoding="utf-8")
        st = self.api.workdir_status(str(work))
        self.assertEqual(st["status"], "source_ahead")
        merged = self.api.workdir_merge(str(work))
        self.assertTrue(merged["ok"], merged)
        self.assertIn("源已更新", work.read_text(encoding="utf-8"))

    def test_diverged_needs_confirm(self):
        listed = self.api.list_folder(str(self.root))
        work = Path(listed["root"]) / "hello.md"
        work.write_text("# 源\n\n副本改\n", encoding="utf-8")
        (self.root / "hello.md").write_text("# 源\n\n源改\n", encoding="utf-8")
        st = self.api.workdir_status(str(work))
        self.assertEqual(st["status"], "diverged")
        blocked = self.api.workdir_push(str(work))
        self.assertFalse(blocked["ok"])
        self.assertTrue(blocked.get("needs_confirm"))
        forced = self.api.workdir_push(str(work), True)
        self.assertTrue(forced["ok"], forced)
        self.assertIn("副本改", (self.root / "hello.md").read_text(encoding="utf-8"))

    def test_merge_markers(self):
        listed = self.api.list_folder(str(self.root))
        work = Path(listed["root"]) / "hello.md"
        work.write_text("副本侧\n", encoding="utf-8")
        (self.root / "hello.md").write_text("源侧\n", encoding="utf-8")
        res = self.api.workdir_merge(str(work), "markers")
        self.assertTrue(res["ok"], res)
        text = work.read_text(encoding="utf-8")
        self.assertIn("<<<<<<< 工作副本", text)
        self.assertIn("副本侧", text)
        self.assertIn("源侧", text)

    def test_summary_counts_copy_ahead(self):
        listed = self.api.list_folder(str(self.root))
        work = Path(listed["root"]) / "hello.md"
        self.api.save_file(str(work), "只改副本\n")
        sumr = self.api.workdir_summary(listed["root"])
        self.assertTrue(sumr["ok"], sumr)
        self.assertGreaterEqual(sumr["pending"], 1)
        self.assertGreaterEqual(sumr["counts"]["copy_ahead"], 1)

    def test_direct_mode_status_rejected(self):
        api = make_api("source")
        res = api.workdir_push(str(self.root / "hello.md"))
        self.assertFalse(res["ok"])
        self.assertIn("直改", res["error"])

    def test_file_session_does_not_steal_folder(self):
        lone = self.api.read_file(str(self.root / "hello.md"))
        self.assertTrue(lone["ok"], lone)
        listed = self.api.list_folder(str(self.root))
        self.assertTrue(listed["ok"], listed)
        names = []

        def walk(nodes):
            for n in nodes or []:
                names.append(n.get("name"))
                walk(n.get("children"))

        walk(listed.get("tree"))
        self.assertIn("hello.md", names)
        self.assertIn("note.md", names)

    def test_daily_note_writes_copy_not_source(self):
        listed = self.api.list_folder(str(self.root))
        res = self.api.open_daily_note(listed["root"], "日记")
        self.assertTrue(res["ok"], res)
        src_daily = self.root / "日记"
        self.assertFalse(src_daily.exists())
        self.assertTrue(Path(res["path"]).is_file())
        self.assertIn("workcopies", res["path"].replace("\\", "/"))

    def test_empty_folder_vault_root_stays_on_copy(self):
        self.api.list_folder(str(self.root))
        res = self.api.open_daily_note("", "日记")
        self.assertTrue(res["ok"], res)
        self.assertFalse((self.root / "日记").exists())
        self.assertIn("workcopies", res["path"].replace("\\", "/"))

    def test_new_file_writes_copy_not_source(self):
        self.api.list_folder(str(self.root))
        created = self.api.new_file(str(self.root), "新笔记")
        self.assertTrue(created["ok"], created)
        self.assertFalse((self.root / "新笔记.md").exists())
        self.assertTrue(Path(created["path"]).is_file())
        self.assertIn("workcopies", created["path"].replace("\\", "/"))
        self.assertTrue(created["source_path"].endswith("新笔记.md"))

    def test_new_from_template_writes_copy_not_source(self):
        (self.root / "模板").mkdir()
        (self.root / "模板" / "会议.md").write_text("# {{title}}\n", encoding="utf-8")
        self.api.list_folder(str(self.root))
        listed = self.api.list_templates(str(self.root))
        self.assertTrue(listed["ok"], listed)
        created = self.api.new_from_template(
            str(self.root), listed["items"][0]["path"], "今日周会"
        )
        self.assertTrue(created["ok"], created)
        self.assertIn("今日周会", created["content"])
        self.assertFalse(any(self.root.glob("今日周会*.md")))
        self.assertIn("workcopies", created["path"].replace("\\", "/"))

    def test_capture_writes_copy_not_source(self):
        self.api.list_folder(str(self.root))
        res = self.api.append_capture(str(self.root), "一条收集")
        self.assertTrue(res["ok"], res)
        self.assertFalse((self.root / "收集箱.md").exists())
        self.assertTrue(Path(res["path"]).is_file())
        self.assertIn("一条收集", Path(res["path"]).read_text(encoding="utf-8"))

    def test_rename_stays_on_copy(self):
        listed = self.api.list_folder(str(self.root))
        work = str(Path(listed["root"]) / "hello.md")
        res = self.api.rename_file(work, "renamed.md")
        self.assertTrue(res["ok"], res)
        self.assertTrue((Path(listed["root"]) / "renamed.md").is_file())
        self.assertTrue((self.root / "hello.md").is_file())
        self.assertFalse((self.root / "renamed.md").exists())
        self.assertTrue(res["source_path"].endswith("renamed.md"))

    def test_delete_on_copy_does_not_restore_from_source(self):
        listed = self.api.list_folder(str(self.root))
        work = Path(listed["root"]) / "hello.md"
        res = self.api.delete_file(str(work))
        self.assertTrue(res["ok"], res)
        self.assertFalse(work.exists())
        self.assertTrue((self.root / "hello.md").is_file())
        again = self.api.list_folder(str(self.root))
        self.assertTrue(again["ok"], again)
        self.assertFalse((Path(again["root"]) / "hello.md").exists())
        self.assertTrue((self.root / "hello.md").is_file())

    def test_rename_does_not_restore_old_name(self):
        listed = self.api.list_folder(str(self.root))
        work = str(Path(listed["root"]) / "hello.md")
        self.assertTrue(self.api.rename_file(work, "renamed.md")["ok"])
        again = self.api.list_folder(str(self.root))
        names: list[str] = []

        def walk(nodes):
            for n in nodes or []:
                names.append(n.get("name"))
                walk(n.get("children"))

        walk(again.get("tree"))
        self.assertIn("renamed.md", names)
        self.assertNotIn("hello.md", names)
        self.assertTrue((self.root / "hello.md").is_file())

    def test_file_session_edits_survive_folder_open(self):
        opened = self.api.read_file(str(self.root / "hello.md"))
        self.assertTrue(opened["ok"], opened)
        self.api.save_file(opened["path"], "# 源\n\nfile-session-edit\n")
        listed = self.api.list_folder(str(self.root))
        work = Path(listed["root"]) / "hello.md"
        self.assertIn("file-session-edit", work.read_text(encoding="utf-8"))
        self.assertEqual((self.root / "hello.md").read_text(encoding="utf-8"), "# 源\n\n原文\n")
        again = self.api.read_file(str(self.root / "hello.md"))
        self.assertTrue(again["ok"], again)
        self.assertIn("file-session-edit", again["content"])
        self.assertIn("workcopies", again["path"].replace("\\", "/"))
        self.assertNotEqual(Path(again["path"]).parent, Path(opened["path"]).parent)

    def test_save_source_path_without_open_writes_copy(self):
        res = self.api.save_file(str(self.root / "hello.md"), "# 源\n\n另存改\n")
        self.assertTrue(res["ok"], res)
        self.assertEqual((self.root / "hello.md").read_text(encoding="utf-8"), "# 源\n\n原文\n")
        self.assertIn("另存改", Path(res["path"]).read_text(encoding="utf-8"))
        self.assertIn("workcopies", res["path"].replace("\\", "/"))

    def test_search_last_file_uses_copy_when_project_registered(self):
        added = self.api.add_project(str(self.root), "搜索库")
        self.assertTrue(added.get("ok"), added)
        opened = self.api.read_file(str(self.root / "hello.md"))
        self.assertTrue(opened["ok"], opened)
        self.api.save_file(opened["path"], "# 源\n\nWorkOnlySearchToken\n")
        self.api.config.set("last_folder", "")
        res = self.api.search_notes("", "WorkOnlySearchToken", "content")
        self.assertTrue(res.get("ok"), res)
        hits = res.get("hits") or []
        blob = " ".join(
            str(h.get("snippet") or "") + " " + str(h.get("name") or "") for h in hits
        )
        self.assertIn("WorkOnlySearchToken", blob)
        self.assertNotIn("WorkOnlySearchToken", (self.root / "hello.md").read_text(encoding="utf-8"))
        still = self.api.save_file(opened["path"], "# 源\n\nAfterSearchToken\n")
        self.assertTrue(still["ok"], still)
        self.assertIn("AfterSearchToken", Path(still["path"]).read_text(encoding="utf-8"))
        self.assertNotIn("AfterSearchToken", (self.root / "hello.md").read_text(encoding="utf-8"))

    def test_search_does_not_absorb_file_session(self):
        self.api.add_project(str(self.root), "库")
        opened = self.api.read_file(str(self.root / "hello.md"))
        old_work = opened["path"]
        self.api.save_file(old_work, "# 源\n\nKeepFileSession\n")
        self.api.config.set("last_folder", "")
        self.api.search_notes("", "KeepFileSession", "content")
        self.assertTrue(Path(old_work).is_file(), old_work)
        self.assertIn("KeepFileSession", Path(old_work).read_text(encoding="utf-8"))

    def test_orphan_work_path_save_follows_last_file(self):
        opened = self.api.read_file(str(self.root / "hello.md"))
        old_work = opened["path"]
        self.api.save_file(old_work, "# 源\n\nedit1\n")
        listed = self.api.list_folder(str(self.root))
        self.assertFalse(Path(old_work).exists())
        saved = self.api.save_file(old_work, "# 源\n\nedit2\n")
        self.assertTrue(saved["ok"], saved)
        self.assertIn("edit2", (Path(listed["root"]) / "hello.md").read_text(encoding="utf-8"))
        self.assertEqual((self.root / "hello.md").read_text(encoding="utf-8"), "# 源\n\n原文\n")

    def test_orphan_rel_keeps_nested_tree_suffix(self):
        raw = str(self.api.workdir.base / "sid" / "tree" / "docs" / "note.md")
        self.assertEqual(self.api._orphan_rel(raw).replace("\\", "/"), "docs/note.md")

    def test_orphan_nested_without_last_file_uses_tree_rel(self):
        listed = self.api.list_folder(str(self.root))
        work = Path(listed["root"]) / "docs" / "note.md"
        ghost = str(self.api.workdir.base / "dead" / "tree" / "docs" / "note.md")
        self.api.config.set("last_file", "")
        saved = self.api.save_file(ghost, "# 笔记\n\nnested-folder-orphan\n")
        self.assertTrue(saved["ok"], saved)
        self.assertIn("nested-folder-orphan", work.read_text(encoding="utf-8"))
        self.assertNotIn("nested-folder-orphan", (self.root / "docs" / "note.md").read_text(encoding="utf-8"))

    def test_orphan_nested_file_save_follows_last_file(self):
        opened = self.api.read_file(str(self.root / "docs" / "note.md"))
        old_work = opened["path"]
        self.api.save_file(old_work, "# 笔记\n\nnested-edit\n")
        listed = self.api.list_folder(str(self.root))
        self.assertFalse(Path(old_work).exists())
        saved = self.api.save_file(old_work, "# 笔记\n\nnested-edit-2\n")
        self.assertTrue(saved["ok"], saved)
        self.assertIn("nested-edit-2", (Path(listed["root"]) / "docs" / "note.md").read_text(encoding="utf-8"))
        self.assertNotIn("nested-edit-2", (self.root / "docs" / "note.md").read_text(encoding="utf-8"))

    def test_orphan_work_path_push_follows_last_file(self):
        opened = self.api.read_file(str(self.root / "hello.md"))
        old_work = opened["path"]
        self.api.save_file(old_work, "# 源\n\nOrphanPushToken\n")
        listed = self.api.list_folder(str(self.root))
        self.assertFalse(Path(old_work).exists())
        pushed = self.api.workdir_push(old_work)
        self.assertTrue(pushed.get("ok"), pushed)
        self.assertIn("OrphanPushToken", (self.root / "hello.md").read_text(encoding="utf-8"))
        self.assertIn("OrphanPushToken", (Path(listed["root"]) / "hello.md").read_text(encoding="utf-8"))

    def test_search_does_not_use_file_session_outside_hint(self):
        self.api.add_project(str(self.root), "库")
        opened = self.api.read_file(str(self.root / "hello.md"))
        self.api.save_file(opened["path"], "# 源\n\nScopedToken\n")
        other = tempfile.TemporaryDirectory(prefix="ryuumd-other-")
        try:
            res = self.api.search_notes(other.name, "ScopedToken", "content")
            self.assertTrue(res.get("ok"), res)
            blob = " ".join(
                str(h.get("snippet") or "") + " " + str(h.get("name") or "")
                for h in (res.get("hits") or [])
            )
            self.assertNotIn("ScopedToken", blob)
        finally:
            other.cleanup()

    def test_recent_copy_only_file_counts_as_exists(self):
        self.api.list_folder(str(self.root))
        created = self.api.new_file(str(self.root), "仅副本")
        self.assertTrue(created["ok"], created)
        items = self.api.get_recent()["items"]
        hit = next((it for it in items if str(it.get("path") or "").endswith("仅副本.md")), None)
        self.assertIsNotNone(hit, items)
        self.assertTrue(hit["exists"], hit)

    def test_knowledge_cache_invalidates_on_copy_save(self):
        listed = self.api.list_folder(str(self.root))
        key = self.api._knowledge_key(listed["root"])
        self.api._knowledge_cache[key] = {"ok": True, "text": "旧谱系"}
        saved = self.api.save_file(str(Path(listed["root"]) / "hello.md"), "# 源\n\n改谱系\n")
        self.assertTrue(saved["ok"], saved)
        self.assertNotIn(key, self.api._knowledge_cache)

    def test_live_like_open_save_push(self):
        self.api.add_project(str(self.root), "库")
        listed = self.api.list_folder(str(self.root))
        opened = self.api.read_file(str(self.root / "hello.md"))
        self.assertTrue(opened["ok"], opened)
        saved = self.api.save_file(opened["path"], "# 源\n\nWorkdirCopyToken\n")
        self.assertTrue(saved["ok"], saved)
        self.assertEqual((self.root / "hello.md").read_text(encoding="utf-8"), "# 源\n\n原文\n")
        pushed = self.api.workdir_push(opened["path"])
        self.assertTrue(pushed.get("ok"), pushed)
        self.assertIn("WorkdirCopyToken", (self.root / "hello.md").read_text(encoding="utf-8"))
        self.assertIn("WorkdirCopyToken", Path(listed["root"]).joinpath("hello.md").read_text(encoding="utf-8"))

    def test_merge_all_does_not_restore_renamed(self):
        listed = self.api.list_folder(str(self.root))
        work = str(Path(listed["root"]) / "hello.md")
        self.assertTrue(self.api.rename_file(work, "renamed.md")["ok"])
        res = self.api.workdir_merge_all(listed["root"], "keep_source")
        self.assertTrue(res["ok"], res)
        self.assertTrue((Path(listed["root"]) / "renamed.md").is_file())
        self.assertFalse((Path(listed["root"]) / "hello.md").exists())
        self.assertTrue((self.root / "hello.md").is_file())


if __name__ == "__main__":
    unittest.main(verbosity=2)
