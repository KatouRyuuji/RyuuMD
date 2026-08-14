# -*- coding: utf-8 -*-
"""云同步：配置门闩、WebDAV 客户端、双向同步引擎（内存 DAV + 本地 HTTP DAV）。"""

from __future__ import annotations

import base64
import os
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_TMP_APPDATA = tempfile.TemporaryDirectory(prefix="ryuumd-cloud-appdata-")
os.environ["APPDATA"] = _TMP_APPDATA.name

from app.core.api import Api  # noqa: E402
from app.core.cloud_sync import (  # noqa: E402
    CloudEngine,
    get_engine,
    iter_local_files,
    load_cloud,
    public_cloud,
    save_cloud,
)
from app.core.config import Config  # noqa: E402
from app.core.projects import ProjectStore  # noqa: E402
from app.core.webdav import DavEntry, DavError, WebDavClient, _parse_mtime  # noqa: E402


def touch(path: Path, text: str = "x") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


class MemoryDav:
    """进程内 DAV，供引擎单测注入（无网络）。"""

    def __init__(self) -> None:
        self.files: dict[str, tuple[bytes, float]] = {}
        self.probed = False

    def probe(self) -> None:
        self.probed = True

    def mkdir_p(self, relpath: str) -> None:
        return

    def put(self, relpath: str, data: bytes) -> None:
        self.files[relpath.replace("\\", "/")] = (data, time.time())

    def get(self, relpath: str) -> bytes:
        key = relpath.replace("\\", "/")
        if key not in self.files:
            raise DavError("not found", 404)
        return self.files[key][0]

    def list_files(self, prefix: str = "") -> dict:
        prefix = prefix.strip("/")
        out = {}
        for k, (data, mt) in self.files.items():
            if prefix and k != prefix and not k.startswith(prefix + "/"):
                continue
            out[k] = DavEntry(relpath=k, is_dir=False, size=len(data), mtime=mt)
        return out


class TestCloudConfig(unittest.TestCase):
    def setUp(self) -> None:
        self.api = Api(Config())
        self.api.config.set("cloud_sync", {
            "enabled": False, "provider": "webdav", "url": "", "username": "",
            "password": "", "remote_root": "RyuuMD", "auto_on_save": False,
            "auto_on_start": False, "insecure_ssl": False, "sync_all_projects": False,
        })

    def test_default_disabled(self):
        cs = self.api.get_config()["cloud_sync"]
        self.assertFalse(cs["enabled"])
        self.assertFalse(cs["password_set"])
        self.assertEqual(cs["password"], "")

    def test_save_strips_password_and_keeps_on_empty(self):
        res = self.api.save_cloud_settings({
            "enabled": True, "url": "https://dav.example/dav/",
            "username": "u", "password": "secret", "remote_root": "Notes",
        })
        self.assertTrue(res["ok"])
        self.assertEqual(res["cloud_sync"]["password"], "")
        self.assertTrue(res["cloud_sync"]["password_set"])
        self.assertTrue(res["cloud_sync"]["enabled"])
        self.assertEqual(load_cloud(self.api.config)["password"], "secret")
        # 空密码 = 保持原密码
        res2 = self.api.save_cloud_settings({"username": "u2", "password": ""})
        self.assertTrue(res2["ok"])
        self.assertEqual(load_cloud(self.api.config)["password"], "secret")
        self.assertEqual(load_cloud(self.api.config)["username"], "u2")

    def test_public_cloud_never_leaks(self):
        pub = public_cloud({"password": "abc", "enabled": True, "url": "http://x"})
        self.assertEqual(pub["password"], "")
        self.assertTrue(pub["password_set"])

    def test_sync_refuses_when_disabled(self):
        res = self.api.sync_cloud("")
        self.assertFalse(res["ok"])
        self.assertIn("未启用", res["error"])

    def test_sync_refuses_when_unconfigured(self):
        self.api.save_cloud_settings({"enabled": True, "url": "", "username": ""})
        res = self.api.sync_cloud("")
        self.assertFalse(res["ok"])
        self.assertIn("WebDAV", res["error"])

    def test_get_config_does_not_expose_password(self):
        self.api.save_cloud_settings({"enabled": True, "url": "http://x", "username": "u", "password": "p"})
        self.assertEqual(self.api.get_config()["cloud_sync"]["password"], "")
        self.assertTrue(self.api.get_config()["cloud_sync"]["password_set"])


class TestIterLocalAndProjectCloud(unittest.TestCase):
    def setUp(self) -> None:
        self.dir = tempfile.TemporaryDirectory(prefix="ryuumd-cloud-io-")
        self.root = Path(self.dir.name)
        self.api = Api(Config())
        self.api.config.set("projects", [])

    def tearDown(self) -> None:
        self.dir.cleanup()

    def test_iter_skips_hidden_and_conflict(self):
        touch(self.root / "a.md", "1")
        touch(self.root / "pic.png", "img")
        touch(self.root / ".hidden" / "x.md", "no")
        touch(self.root / "node_modules" / "x.md", "no")
        touch(self.root / "note.conflict-20260101.md", "c")
        files = iter_local_files(str(self.root))
        self.assertEqual(set(files), {"a.md", "pic.png"})

    def test_project_cloud_flag_and_containing_path(self):
        d = self.root / "vault"
        f = touch(d / "sub" / "n.md", "hi")
        pid = self.api.add_project(str(d), "")["project"]["id"]
        self.assertFalse(self.api.list_projects()["items"][0].get("cloud_enabled"))
        self.assertTrue(self.api.set_project_cloud(pid, True)["ok"])
        self.assertTrue(self.api.list_projects()["items"][0]["cloud_enabled"])
        found = self.api.projects.find_by_containing_path(str(f))
        self.assertEqual(found["id"], pid)
        self.assertIsNone(self.api.projects.find_by_containing_path(str(self.root / "other.md")))


class TestCloudEngine(unittest.TestCase):
    def setUp(self) -> None:
        self.dir = tempfile.TemporaryDirectory(prefix="ryuumd-eng-")
        self.root = Path(self.dir.name)
        self.cfg = Config()
        self.cfg.set("projects", [])
        self.cfg.set("cloud_sync", {
            "enabled": True, "provider": "webdav", "url": "http://example/dav",
            "username": "u", "password": "p", "remote_root": "RyuuMD",
            "auto_on_save": True, "auto_on_start": False, "insecure_ssl": False,
            "sync_all_projects": False,
        })
        self.dav = MemoryDav()
        self.store = ProjectStore(self.cfg)
        self.engine = CloudEngine(self.cfg, self.store, client=self.dav)

    def tearDown(self) -> None:
        self.dir.cleanup()

    def _vault(self, name="v1"):
        d = self.root / name
        d.mkdir(parents=True, exist_ok=True)
        res = self.store.add(str(d), name)
        pid = res["project"]["id"]
        self.store.set_cloud_enabled(pid, True)
        return pid, d

    def test_upload_new_local(self):
        pid, d = self._vault()
        touch(d / "hello.md", "# hi")
        res = self.engine.sync()
        self.assertTrue(res["ok"], res)
        self.assertEqual(res["uploaded"], 1)
        self.assertIn(f"{pid}/hello.md", self.dav.files)
        self.assertEqual(self.dav.files[f"{pid}/hello.md"][0], b"# hi")

    def test_download_new_remote(self):
        pid, d = self._vault()
        self.dav.put(f"{pid}/from-cloud.md", b"# cloud")
        res = self.engine.sync()
        self.assertTrue(res["ok"], res)
        self.assertEqual(res["downloaded"], 1)
        self.assertEqual((d / "from-cloud.md").read_text(encoding="utf-8"), "# cloud")

    def test_skip_unchanged_second_sync(self):
        pid, d = self._vault()
        touch(d / "a.md", "same")
        self.engine.sync()
        res = self.engine.sync()
        self.assertTrue(res["ok"], res)
        self.assertEqual(res["uploaded"], 0)
        self.assertGreaterEqual(res["skipped"], 1)

    def test_same_size_skip_records_fingerprint(self):
        # 首次同步双方同尺寸 → skip，但须补记本地指纹；
        # 否则此后本地同尺寸改动永远走「首次」分支按尺寸跳过，改动静默丢失
        pid, d = self._vault()
        local = touch(d / "a.md", "abcd")
        self.dav.put(f"{pid}/a.md", b"abcd")
        res = self.engine.sync()
        self.assertTrue(res["ok"], res)
        self.assertEqual(res["uploaded"], 0)
        self.assertGreaterEqual(res["skipped"], 1)
        # 本地改成同尺寸不同内容 → 有指纹应识别为本地改动并上传
        local.write_text("wxyz", encoding="utf-8")
        res2 = self.engine.sync()
        self.assertTrue(res2["ok"], res2)
        self.assertEqual(res2["uploaded"], 1)
        self.assertEqual(self.dav.files[f"{pid}/a.md"][0], b"wxyz")

    def test_conflict_keeps_local_and_saves_remote_copy(self):
        pid, d = self._vault()
        local = touch(d / "note.md", "local-v1")
        self.engine.sync()
        # 双方都改：本地内容变，远端 size 也变
        local.write_text("local-v2-longer", encoding="utf-8")
        self.dav.put(f"{pid}/note.md", b"remote-v2")
        res = self.engine.sync()
        self.assertTrue(res["ok"], res)
        self.assertEqual(res["conflicts"], 1)
        self.assertEqual(local.read_text(encoding="utf-8"), "local-v2-longer")
        conflicts = list(d.glob("note.conflict-*.md"))
        self.assertEqual(len(conflicts), 1)
        self.assertEqual(conflicts[0].read_bytes(), b"remote-v2")
        self.assertEqual(self.dav.files[f"{pid}/note.md"][0], b"local-v2-longer")

    def test_vault_gate_without_flag(self):
        d = self.root / "off"
        d.mkdir()
        self.store.add(str(d), "off")
        touch(d / "a.md", "x")
        res = self.engine.sync()
        self.assertFalse(res["ok"])
        self.assertIn("没有已开启", res["error"])

    def test_sync_all_projects_bypasses_flag(self):
        d = self.root / "all"
        d.mkdir()
        pid = self.store.add(str(d), "all")["project"]["id"]
        touch(d / "a.md", "x")
        self.cfg.set("cloud_sync", {**load_cloud(self.cfg), "sync_all_projects": True})
        res = self.engine.sync()
        self.assertTrue(res["ok"], res)
        self.assertIn(f"{pid}/a.md", self.dav.files)

    def test_push_file_respects_auto_on_save(self):
        pid, d = self._vault()
        f = touch(d / "s.md", "saved")
        res = self.engine.push_file(str(f))
        self.assertTrue(res["ok"])
        self.assertTrue(res.get("uploaded"))
        self.assertIn(f"{pid}/s.md", self.dav.files)

    def test_push_file_skipped_when_auto_off(self):
        pid, d = self._vault()
        self.cfg.set("cloud_sync", {**load_cloud(self.cfg), "auto_on_save": False})
        f = touch(d / "s.md", "saved")
        res = self.engine.push_file(str(f))
        self.assertTrue(res["skipped"])
        self.assertEqual(self.dav.files, {})
        _ = pid


class _DavHandler(BaseHTTPRequestHandler):
    """极简 WebDAV：PROPFIND/GET/PUT/MKCOL + Basic 认证。"""

    store: dict = {}
    user = "alice"
    password = "s3cret"

    def log_message(self, *args):  # noqa: ANN002
        return

    def _auth_ok(self) -> bool:
        h = self.headers.get("Authorization") or ""
        if not h.startswith("Basic "):
            return False
        try:
            raw = base64.b64decode(h.split(" ", 1)[1]).decode("utf-8")
            u, p = raw.split(":", 1)
            return u == self.user and p == self.password
        except Exception:  # noqa: BLE001
            return False

    def _rel(self) -> str:
        from urllib.parse import unquote, urlparse

        path = unquote(urlparse(self.path).path)
        return path

    def _read_body(self) -> bytes:
        n = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(n) if n else b""

    def _deny(self) -> None:
        self._read_body()
        self.send_response(401)
        self.send_header("WWW-Authenticate", 'Basic realm="dav"')
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_PROPFIND(self):  # noqa: N802
        if not self._auth_ok():
            self._deny()
            return
        self._read_body()
        prefix = self._rel().rstrip("/")
        items = []
        # 自身
        items.append((prefix + "/", True, 0))
        for k, data in list(self.store.items()):
            parent = str(Path(k).parent).replace("\\", "/")
            if parent == prefix or k.rsplit("/", 1)[0] == prefix:
                items.append((k, False, len(data)))
            elif k.startswith(prefix + "/") and k.count("/") == prefix.count("/") + 1:
                items.append((k, False, len(data)))
        # 子目录：从文件路径推断
        dirs = set()
        for k in self.store:
            if k.startswith(prefix + "/"):
                rest = k[len(prefix) + 1:]
                first = rest.split("/")[0]
                if "/" in rest:
                    dirs.add(prefix + "/" + first + "/")
        for d in dirs:
            items.append((d, True, 0))
        xml = ['<?xml version="1.0"?><d:multistatus xmlns:d="DAV:">']
        gmt = "Thu, 01 Jan 2026 00:00:00 GMT"
        seen = set()
        for href, is_dir, size in items:
            if href in seen:
                continue
            seen.add(href)
            rtype = "<d:collection/>" if is_dir else ""
            xml.append(
                "<d:response><d:href>" + href + "</d:href><d:propstat><d:prop>"
                f"<d:resourcetype>{rtype}</d:resourcetype>"
                f"<d:getcontentlength>{size}</d:getcontentlength>"
                f"<d:getlastmodified>{gmt}</d:getlastmodified>"
                "</d:prop><d:status>HTTP/1.1 200 OK</d:status></d:propstat></d:response>"
            )
        xml.append("</d:multistatus>")
        body = "".join(xml).encode("utf-8")
        self.send_response(207)
        self.send_header("Content-Type", "application/xml")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_MKCOL(self):  # noqa: N802
        if not self._auth_ok():
            self._deny()
            return
        self._read_body()
        self.send_response(201)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_PUT(self):  # noqa: N802
        if not self._auth_ok():
            self._deny()
            return
        self.store[self._rel()] = self._read_body()
        self.send_response(201)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self):  # noqa: N802
        if not self._auth_ok():
            self._deny()
            return
        self._read_body()
        data = self.store.get(self._rel())
        if data is None:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_DELETE(self):  # noqa: N802
        if not self._auth_ok():
            self._deny()
            return
        self._read_body()
        self.store.pop(self._rel(), None)
        self.send_response(204)
        self.send_header("Content-Length", "0")
        self.end_headers()


class TestWebDavHttp(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        _DavHandler.store = {}
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), _DavHandler)
        cls.port = cls.httpd.server_address[1]
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f"http://127.0.0.1:{cls.port}/dav/"

    @classmethod
    def tearDownClass(cls) -> None:
        cls.httpd.shutdown()

    def setUp(self) -> None:
        _DavHandler.store.clear()

    def test_probe_put_get_list(self):
        c = WebDavClient(self.base, "alice", "s3cret")
        c.probe()
        c.put("RyuuMD/a.md", b"# n")
        self.assertEqual(c.get("RyuuMD/a.md"), b"# n")
        listed = c.list_files("RyuuMD")
        self.assertIn("RyuuMD/a.md", listed)

    def test_auth_required(self):
        c = WebDavClient(self.base, "alice", "wrong")
        with self.assertRaises(DavError):
            c.put("x.md", b"no")

    def test_parse_mtime(self):
        ts = _parse_mtime("Thu, 01 Jan 2026 00:00:00 GMT")
        self.assertGreater(ts, 0)

    def test_engine_over_http(self):
        cfg = Config()
        cfg.set("projects", [])
        cfg.set("cloud_sync", {
            "enabled": True, "provider": "webdav", "url": self.base.rstrip("/"),
            "username": "alice", "password": "s3cret", "remote_root": "RyuuMD",
            "auto_on_save": False, "auto_on_start": False, "insecure_ssl": False,
            "sync_all_projects": True,
        })
        d = Path(tempfile.mkdtemp(prefix="ryuumd-http-v-"))
        try:
            touch(d / "doc.md", "hello-http")
            store = ProjectStore(cfg)
            store.add(str(d), "httpv")
            eng = CloudEngine(cfg, store)  # 真实 WebDavClient
            res = eng.sync()
            self.assertTrue(res["ok"], res)
            self.assertEqual(res["uploaded"], 1)
        finally:
            # 临时目录留给 GC；测试结束即可
            pass


class TestGetEngineCache(unittest.TestCase):
    def test_same_datadir_reuses(self):
        cfg = Config()
        a = get_engine(cfg, ProjectStore(cfg))
        b = get_engine(cfg, ProjectStore(cfg))
        self.assertIs(a, b)


if __name__ == "__main__":
    unittest.main(verbosity=2)
