# -*- coding: utf-8 -*-
"""仓库检索 / 双向链接 / 贴图 / 每日笔记 / HTML 导出包装 单元测试。"""

from __future__ import annotations

import base64
import os
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_TMP_APPDATA = tempfile.TemporaryDirectory(prefix="ryuumd-test-search-appdata-")
os.environ["APPDATA"] = _TMP_APPDATA.name

from app.core.api import Api, wrap_html_export, apply_template_vars  # noqa: E402
from app.core.config import Config  # noqa: E402
from app.core.search import parse_wikilink  # noqa: E402


def make_api() -> Api:
    return Api(Config())


def touch(path: Path, text: str = "x") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


PNG_1x1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwADhAGAhVMF/wAAAABJRU5ErkJggg=="
)


class TestListAndSearch(unittest.TestCase):
    def setUp(self) -> None:
        self.dir = tempfile.TemporaryDirectory(prefix="ryuumd-search-")
        self.root = Path(self.dir.name)
        self.api = make_api()
        touch(self.root / "欢迎.md", "# 欢迎\n\n这是首页")
        touch(self.root / "docs" / "架构.md", "# 架构\n\n使用 WebDAV 同步")
        touch(self.root / "docs" / "笔记.md", "# 笔记\n\nTODO 项")
        touch(self.root / "node_modules" / "pkg" / "x.md", "# 不应出现")
        touch(self.root / "欢迎.assets" / "ghost.md", "# 配图目录内不应索引")

    def tearDown(self) -> None:
        self.dir.cleanup()

    def test_list_md_files_filters_and_query(self):
        res = self.api.list_md_files(str(self.root), "")
        self.assertTrue(res["ok"])
        names = {it["name"] for it in res["items"]}
        self.assertEqual(names, {"欢迎.md", "架构.md", "笔记.md"})
        q = self.api.list_md_files(str(self.root), "架构")
        self.assertEqual(len(q["items"]), 1)
        self.assertEqual(q["items"][0]["stem"], "架构")

    def test_list_requires_folder(self):
        self.api.config.set("last_folder", "")
        res = self.api.list_md_files("", "")
        self.assertFalse(res["ok"])
        self.assertIn("打开", res["error"])

    def test_search_name_then_content(self):
        res = self.api.search_vault(str(self.root), "WebDAV")
        self.assertTrue(res["ok"])
        self.assertTrue(any(h["kind"] == "content" and "架构" in h["name"] for h in res["hits"]))
        by_name = self.api.search_vault(str(self.root), "欢迎")
        self.assertTrue(any(h["kind"] == "name" for h in by_name["hits"]))

    def test_search_empty_query(self):
        res = self.api.search_vault(str(self.root), "  ")
        self.assertTrue(res["ok"])
        self.assertEqual(res["hits"], [])

    def test_tree_skips_assets_dir(self):
        res = self.api.list_folder(str(self.root))
        names = [n["name"] for n in res["tree"]]
        self.assertNotIn("欢迎.assets", names)
        self.assertNotIn("node_modules", names)
        self.assertIn("docs", names)


class TestWikilink(unittest.TestCase):
    def setUp(self) -> None:
        self.dir = tempfile.TemporaryDirectory(prefix="ryuumd-wiki-")
        self.root = Path(self.dir.name)
        self.api = make_api()
        touch(self.root / "首页.md", "# 首页")
        touch(self.root / "sub" / "详情.md", "# 详情")
        touch(self.root / "sub" / "首页.md", "# 子首页")

    def tearDown(self) -> None:
        self.dir.cleanup()

    def test_parse_alias_and_heading(self):
        self.assertEqual(parse_wikilink("详情#节|别名"), ("详情", "节"))
        self.assertEqual(parse_wikilink("  架构.md  "), ("架构.md", ""))

    def test_resolve_relative_and_prefer_same_dir(self):
        cur = str(self.root / "sub" / "详情.md")
        same = self.api.resolve_wikilink(str(self.root), "首页", cur)
        self.assertTrue(same["ok"] and same["exists"])
        self.assertEqual(Path(same["path"]).parent, self.root / "sub")

        rel = self.api.resolve_wikilink(str(self.root), "详情", str(self.root / "首页.md"))
        self.assertTrue(rel["exists"])
        self.assertEqual(Path(rel["path"]).name, "详情.md")

    def test_resolve_missing_suggests_create(self):
        res = self.api.resolve_wikilink(str(self.root), "不存在的笔记", str(self.root / "首页.md"))
        self.assertTrue(res["ok"])
        self.assertFalse(res["exists"])
        self.assertTrue(res["suggested"].endswith("不存在的笔记.md"))

    def test_backlinks_finds_wikilink(self):
        touch(self.root / "首页.md", "见 [[详情]] 与 [相对](sub/详情.md)\n")
        res = self.api.find_backlinks(str(self.root), str(self.root / "sub" / "详情.md"))
        self.assertTrue(res["ok"])
        names = {h["name"] for h in res["hits"]}
        self.assertIn("首页.md", names)
        self.assertNotIn("详情.md", names)


class TestSaveImageAndDaily(unittest.TestCase):
    def setUp(self) -> None:
        self.dir = tempfile.TemporaryDirectory(prefix="ryuumd-img-")
        self.root = Path(self.dir.name)
        self.api = make_api()
        self.md = touch(self.root / "文章.md", "# 文")

    def tearDown(self) -> None:
        self.dir.cleanup()

    def test_save_image_next_to_note(self):
        b64 = base64.b64encode(PNG_1x1).decode("ascii")
        res = self.api.save_image(str(self.md), "", "clip.png", b64, "image/png")
        self.assertTrue(res["ok"], res)
        self.assertTrue(res["rel"].startswith("文章.assets/"))
        self.assertTrue(Path(res["path"]).is_file())
        self.assertGreater(Path(res["path"]).stat().st_size, 10)

    def test_save_image_rejects_empty_and_needs_target(self):
        self.assertFalse(self.api.save_image("", "", "a.png", "", "image/png")["ok"])
        orphan = make_api()
        orphan.config.set("last_folder", "")
        b64 = base64.b64encode(PNG_1x1).decode("ascii")
        self.assertFalse(orphan.save_image("", "", "a.png", b64, "image/png")["ok"])

    def test_daily_note_creates_once(self):
        res = self.api.open_daily_note(str(self.root), "日记")
        self.assertTrue(res["ok"], res)
        date = datetime.now().strftime("%Y-%m-%d")
        self.assertTrue(res["name"].startswith(date))
        self.assertIn(date, res["content"])
        again = self.api.open_daily_note(str(self.root), "日记")
        self.assertEqual(again["path"], res["path"])

    def test_daily_note_rejects_traversal(self):
        bad = self.api.open_daily_note(str(self.root), "../outside")
        self.assertFalse(bad["ok"])

    def test_daily_note_uses_template(self):
        tpl = self.root / "模板"
        tpl.mkdir()
        (tpl / "日记.md").write_text("# {{date}} {{title}}\nhello\n", encoding="utf-8")
        res = self.api.open_daily_note(str(self.root), "日记")
        self.assertTrue(res["ok"], res)
        date = datetime.now().strftime("%Y-%m-%d")
        self.assertIn(date, res["content"])
        self.assertIn("hello", res["content"])

    def test_wrap_html_export_escapes_title(self):
        doc = wrap_html_export("<脚本>", "<p>正文</p>")
        self.assertIn("&lt;脚本&gt;", doc)
        self.assertIn("<p>正文</p>", doc)
        self.assertTrue(doc.startswith("<!DOCTYPE html>"))
        # 未传 config 时回退 sky，避免老调用方丢默认配色
        self.assertIn("#3498db", doc)
        self.assertIn("#2c3e50", doc)

    def test_wrap_html_export_follows_palette_and_font(self):
        doc = wrap_html_export(
            "导出",
            "<p>正文</p>",
            {
                "theme": "dark",
                "palette_dark": "abyss",
                "font_ui": "KaiTi",
                "font_mono": "Consolas",
            },
        )
        self.assertIn("#00f3ff", doc)
        self.assertIn("#0f111a", doc)
        self.assertIn("KaiTi", doc)
        self.assertIn("Consolas", doc)
        self.assertNotIn("#3498db", doc)


class TestTemplates(unittest.TestCase):
    def setUp(self) -> None:
        self.dir = tempfile.TemporaryDirectory(prefix="ryuumd-tpl-")
        self.root = Path(self.dir.name)
        self.api = make_api()

    def tearDown(self) -> None:
        self.dir.cleanup()

    def test_apply_template_vars(self):
        now = datetime(2026, 8, 13, 15, 4, 0)
        out = apply_template_vars("# {{title}} {{date}} {{week}}", "会议", now)
        self.assertIn("会议", out)
        self.assertIn("2026-08-13", out)
        self.assertIn("四", out)

    def test_list_and_render_and_new(self):
        touch(self.root / "模板" / "会议.md", "# {{title}}\n日期 {{date}}\n")
        listed = self.api.list_templates(str(self.root))
        self.assertTrue(listed["ok"], listed)
        self.assertEqual(len(listed["items"]), 1)
        self.assertEqual(listed["items"][0]["name"], "会议")
        rendered = self.api.render_template(
            str(self.root), listed["items"][0]["path"], "周会"
        )
        self.assertTrue(rendered["ok"], rendered)
        self.assertIn("周会", rendered["content"])
        created = self.api.new_from_template(
            str(self.root), listed["items"][0]["path"], "今日周会"
        )
        self.assertTrue(created["ok"], created)
        self.assertTrue(created["name"].startswith("今日周会"))
        self.assertIn("今日周会", created["content"])

    def test_render_rejects_outside_vault(self):
        outside = Path(tempfile.mkdtemp(prefix="ryuumd-out-")) / "evil.md"
        outside.write_text("secret", encoding="utf-8")
        res = self.api.render_template(str(self.root), str(outside), "x")
        self.assertFalse(res["ok"])

    def test_ensure_templates_dir(self):
        res = self.api.ensure_templates_dir(str(self.root))
        self.assertTrue(res["ok"], res)
        self.assertTrue(Path(res["path"]).is_dir())


class TestVaultIndex(unittest.TestCase):
    def setUp(self) -> None:
        self.dir = tempfile.TemporaryDirectory(prefix="ryuumd-idx-")
        self.root = Path(self.dir.name)
        self.api = make_api()
        touch(self.root / "a.md", "- [ ] 买牛奶\n#work 今天\n[[missing]]\n")
        touch(self.root / "b.md", "见 [[a]]\n")
        touch(self.root / "c.md", "```\n- [ ] 假待办\n```\n完成 - [x] 已做\n")

    def tearDown(self) -> None:
        self.dir.cleanup()

    def test_list_tasks_skips_fence_and_done(self):
        res = self.api.vault_index(str(self.root), "tasks")
        self.assertTrue(res["ok"], res)
        titles = [it["title"] for it in res["items"]]
        self.assertIn("买牛奶", titles)
        self.assertNotIn("假待办", titles)
        self.assertFalse(any("已做" in t for t in titles))

    def test_list_tags_grouped(self):
        res = self.api.vault_index(str(self.root), "tags")
        self.assertTrue(res["ok"], res)
        tags = {it["tag"] for it in res["items"]}
        self.assertIn("work", tags)

    def test_broken_and_orphans(self):
        broken = self.api.vault_index(str(self.root), "broken")
        self.assertTrue(broken["ok"], broken)
        titles = [it["title"] for it in broken["items"]]
        self.assertTrue(any("missing" in t for t in titles))
        orphans = self.api.vault_index(str(self.root), "orphans")
        names = {it["name"] for it in orphans["items"]}
        self.assertIn("b.md", names)
        self.assertNotIn("a.md", names)

    def test_vault_stats_and_file_stat(self):
        st = self.api.vault_stats(str(self.root))
        self.assertTrue(st["ok"], st)
        self.assertGreaterEqual(st["files"], 3)
        self.assertGreaterEqual(st["tasks"], 1)
        fs = self.api.file_stat(str(self.root / "a.md"))
        self.assertTrue(fs["ok"] and fs["exists"])
        self.assertGreater(fs["mtime"], 0)
        self.assertFalse(self.api.file_stat(str(self.root / "nope.md"))["ok"])

    def test_unlinked_mentions_skips_wikilink(self):
        touch(self.root / "项目计划.md", "# 计划\n")
        touch(self.root / "wiki.md", "见 [[项目计划]]\n")
        touch(self.root / "plain.md", "项目计划 需要评审\n")
        res = self.api.vault_index(
            str(self.root), "mentions", "", str(self.root / "项目计划.md")
        )
        self.assertTrue(res["ok"], res)
        names = {it["name"] for it in res["items"]}
        self.assertIn("plain.md", names)
        self.assertNotIn("wiki.md", names)
        self.assertNotIn("项目计划.md", names)
        empty = self.api.vault_index(str(self.root), "mentions", "", "")
        self.assertFalse(empty["ok"])

    def test_append_capture_creates_and_appends(self):
        res = self.api.append_capture(str(self.root), "一条想法")
        self.assertTrue(res["ok"], res)
        dest = Path(res["path"])
        self.assertEqual(dest.name, "收集箱.md")
        text = dest.read_text(encoding="utf-8")
        self.assertIn("# 收集箱", text)
        self.assertIn("一条想法", text)
        self.assertRegex(text, r"## \d{4}-\d{2}-\d{2} \d{2}:\d{2}")
        again = self.api.append_capture(str(self.root), "第二条")
        self.assertTrue(again["ok"], again)
        text2 = dest.read_text(encoding="utf-8")
        self.assertIn("一条想法", text2)
        self.assertIn("第二条", text2)
        self.assertEqual(text2.count("一条想法"), 1)

    def test_append_capture_rejects_bad_name(self):
        self.assertFalse(self.api.append_capture(str(self.root), "  ")["ok"])
        self.assertFalse(self.api.append_capture(str(self.root), "x", "../evil.md")["ok"])
        self.assertFalse(self.api.append_capture(str(self.root), "x", "a\\b.md")["ok"])
        self.assertFalse(self.api.append_capture("", "x")["ok"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
