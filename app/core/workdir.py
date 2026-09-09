"""工作副本：进入仓库时生成可编辑副本，源文件只在显式回写 / 合并时变动。

直改模式（edit_mode=source）不经过本模块。工作副本落在用户数据目录
`workcopies/<id>/tree/`，源路径只写进清单，最近打开与 last_* 仍记录源路径。
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import threading
import time
from pathlib import Path
from typing import Any, Optional

from .fsutil import IGNORE_DIRS, MD_EXTS, atomic_write_bytes, atomic_write_text

EDIT_SOURCE = "source"
EDIT_WORKDIR = "workdir"
VALID_EDIT_MODES = frozenset({EDIT_SOURCE, EDIT_WORKDIR})

MAX_COPY_BYTES = 20 * 1024 * 1024
MANIFEST_NAME = "manifest.json"
TREE_DIRNAME = "tree"

# 复制时跳过隐藏目录与依赖目录；*.assets 必须保留（配图）。
_SKIP_COPY_DIRS = set(IGNORE_DIRS) | {".workflow"}


def normalize_edit_mode(raw: Any) -> str:
    text = str(raw or EDIT_SOURCE).strip().lower()
    return text if text in VALID_EDIT_MODES else EDIT_SOURCE


def file_hash(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _norm(path: str) -> str:
    try:
        return os.path.normcase(str(Path(path).resolve()))
    except OSError:
        return os.path.normcase(os.path.normpath(str(path)))


def _is_under(child: str, parent: str) -> bool:
    c, p = _norm(child), _norm(parent)
    if c == p:
        return True
    sep = os.sep
    return c.startswith(p + sep)


def _rel_posix(root: str, path: str) -> str:
    rel = os.path.relpath(str(path), str(root))
    return rel.replace("\\", "/")


def _skip_copy_dir(name: str) -> bool:
    if not name or name.startswith("."):
        return True
    return name in _SKIP_COPY_DIRS


class WorkdirStore:
    """工作副本清单与同步。只给 Python 用，禁止被 pywebview 扫进 JS 桥。"""

    _serializable = False

    def __init__(self, data_dir: Path) -> None:
        self.base = Path(data_dir) / "workcopies"
        self.base.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()

    # ------------------------------------------------------------------
    # 会话身份
    # ------------------------------------------------------------------
    @staticmethod
    def source_id(source_root: str) -> str:
        return hashlib.sha256(_norm(source_root).encode("utf-8")).hexdigest()[:16]

    def session_dir(self, sid: str) -> Path:
        return self.base / sid

    def _manifest_path(self, sid: str) -> Path:
        return self.session_dir(sid) / MANIFEST_NAME

    def _load_manifest(self, sid: str) -> Optional[dict[str, Any]]:
        p = self._manifest_path(sid)
        if not p.is_file():
            return None
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        return data if isinstance(data, dict) and data.get("work_root") else None

    def _save_manifest(self, sess: dict[str, Any]) -> None:
        sid = sess["id"]
        self.session_dir(sid).mkdir(parents=True, exist_ok=True)
        raw = json.dumps(sess, ensure_ascii=False, indent=2)
        atomic_write_text(self._manifest_path(sid), raw)

    def _session_rank(self, sess: dict[str, Any], path: str) -> Optional[tuple[int, int]]:
        """匹配强度：副本根 > 单文件锚点 > 整库源根（最长前缀优先）。"""
        work = str(sess.get("work_root") or "")
        src = str(sess.get("source_root") or "")
        if work and _is_under(path, work):
            return (3, len(_norm(work)))
        kind = sess.get("kind") or "folder"
        if kind == "file":
            anchor = str(sess.get("anchor") or "")
            if not anchor or not src:
                return None
            src_file = str(Path(src) / anchor)
            if _norm(path) == _norm(src_file):
                return (2, len(_norm(src_file)))
            assets = Path(src) / (Path(anchor).stem + ".assets")
            try:
                if assets.is_dir() and _is_under(path, str(assets)):
                    return (2, len(_norm(str(assets))))
            except OSError:
                return None
            return None
        if src and _is_under(path, src):
            return (1, len(_norm(src)))
        return None

    def find_session_for_path(self, path: str) -> Optional[dict[str, Any]]:
        """路径落在已知源根或副本根时返回清单。"""
        if not (path or "").strip():
            return None
        best: Optional[dict[str, Any]] = None
        best_rank: tuple[int, int] = (-1, -1)
        with self._lock:
            if not self.base.is_dir():
                return None
            for child in self.base.iterdir():
                if not child.is_dir():
                    continue
                sess = self._load_manifest(child.name)
                if not sess:
                    continue
                rank = self._session_rank(sess, path)
                if rank and rank > best_rank:
                    best, best_rank = sess, rank
        return best

    # ------------------------------------------------------------------
    # 建立 / 刷新副本
    # ------------------------------------------------------------------
    def ensure_folder(self, source_root: str) -> dict[str, Any]:
        root = Path(source_root)
        if not root.is_dir():
            raise FileNotFoundError("文件夹不存在")
        sid = self.source_id(str(root))
        with self._lock:
            sess = self._load_manifest(sid)
            if not sess:
                work = self.session_dir(sid) / TREE_DIRNAME
                work.mkdir(parents=True, exist_ok=True)
                sess = {
                    "id": sid,
                    "kind": "folder",
                    "source_root": str(root.resolve()),
                    "work_root": str(work),
                    "created_at": int(time.time()),
                    "files": {},
                }
            added = self._refresh_snapshot(sess)
            sess["last_open_at"] = int(time.time())
            sess["last_added"] = added
            self._save_manifest(sess)
            return self._absorb_file_sessions(sess)

    def ensure_file(self, source_file: str) -> dict[str, Any]:
        src = Path(source_file)
        if not src.is_file():
            raise FileNotFoundError("文件不存在")
        parent = str(src.parent.resolve())
        sid = self.source_id(parent + "::" + src.name.lower())
        with self._lock:
            sess = self._load_manifest(sid)
            if not sess:
                work = self.session_dir(sid) / TREE_DIRNAME
                work.mkdir(parents=True, exist_ok=True)
                sess = {
                    "id": sid,
                    "kind": "file",
                    "source_root": parent,
                    "anchor": src.name,
                    "work_root": str(work),
                    "created_at": int(time.time()),
                    "files": {},
                }
            added = self._refresh_file_session(sess, src)
            sess["last_open_at"] = int(time.time())
            sess["last_added"] = added
            self._save_manifest(sess)
            return sess

    def ensure_for_path(self, path: str, vault_hint: str = "") -> dict[str, Any]:
        """打开任意路径：已有会话复用；目录建整库副本；文件优先归入所属仓库。"""
        raw = (path or "").strip()
        if not raw:
            raise FileNotFoundError("路径为空")
        p = Path(raw)
        existing = self.find_session_for_path(raw)
        if existing:
            # 先开过单文件、再打开其父目录：升级为整库副本，避免文件树只剩一篇
            if p.is_dir() and existing.get("kind") == "file":
                return self.ensure_folder(str(p))
            if existing.get("kind") == "folder":
                with self._lock:
                    added = self._refresh_snapshot(existing)
                    existing["last_added"] = added
                    existing["last_open_at"] = int(time.time())
                    self._save_manifest(existing)
                return self._absorb_file_sessions(existing)
            return existing
        hint = (vault_hint or "").strip()
        if hint:
            hp = Path(hint)
            if hp.is_dir() and _is_under(raw, str(hp)):
                return self.ensure_folder(str(hp))
        if p.is_dir():
            return self.ensure_folder(str(p))
        if p.is_file():
            return self.ensure_file(str(p))
        parent = p.parent
        if hint:
            hp = Path(hint)
            if hp.is_dir() and parent.is_dir() and _is_under(str(p), str(hp)):
                return self.ensure_folder(str(hp))
        parent_sess = self.find_session_for_path(str(parent)) if parent.is_dir() else None
        if parent_sess and parent_sess.get("kind") == "folder":
            return parent_sess
        raise FileNotFoundError("路径不存在")

    def _refresh_snapshot(self, sess: dict[str, Any]) -> int:
        source = Path(sess["source_root"])
        work = Path(sess["work_root"])
        files = sess.setdefault("files", {})
        added = 0
        if not source.is_dir():
            return 0
        for dirpath, dirnames, filenames in os.walk(source):
            dirnames[:] = [d for d in dirnames if not _skip_copy_dir(d)]
            for name in filenames:
                src = Path(dirpath) / name
                try:
                    if src.stat().st_size > MAX_COPY_BYTES:
                        continue
                except OSError:
                    continue
                rel = _rel_posix(str(source), str(src))
                dst = work / rel
                if dst.exists():
                    if rel not in files:
                        files[rel] = {"base_hash": file_hash(dst)}
                    continue
                if rel in files:
                    # 副本里删过或改过名：不要在刷新时从源再拷回来
                    continue
                dst.parent.mkdir(parents=True, exist_ok=True)
                try:
                    shutil.copy2(src, dst)
                except OSError:
                    continue
                files[rel] = {"base_hash": file_hash(src)}
                added += 1
        return added

    def find_file_sessions_under(self, folder: str) -> list[dict[str, Any]]:
        """源根落在该目录内的单文件会话（打开父目录时要收编）。"""
        found: list[dict[str, Any]] = []
        if not (folder or "").strip():
            return found
        with self._lock:
            if not self.base.is_dir():
                return found
            for child in self.base.iterdir():
                if not child.is_dir():
                    continue
                sess = self._load_manifest(child.name)
                if not sess or sess.get("kind") != "file":
                    continue
                src = str(sess.get("source_root") or "")
                if src and (_norm(src) == _norm(folder) or _is_under(src, folder)):
                    found.append(sess)
        return found

    def _drop_session(self, sess: dict[str, Any]) -> None:
        sid = str(sess.get("id") or "")
        if not sid:
            return
        d = self.session_dir(sid)
        with self._lock:
            try:
                if d.is_dir():
                    shutil.rmtree(d, ignore_errors=True)
            except OSError:
                pass

    def _merge_file_session_into(
        self, folder_sess: dict[str, Any], file_sess: dict[str, Any]
    ) -> dict[str, Any]:
        """把单文件副本上的编辑盖进整库副本，并保留 base_hash。"""
        src_work = Path(file_sess.get("work_root") or "")
        dest_work = Path(folder_sess.get("work_root") or "")
        file_source = str(file_sess.get("source_root") or "")
        folder_source = str(folder_sess.get("source_root") or "")
        if not src_work.is_dir() or not dest_work.is_dir():
            return folder_sess
        mid = _rel_posix(folder_source, file_source) if file_source and folder_source else "."
        files = folder_sess.setdefault("files", {})
        file_files = file_sess.get("files") or {}
        for dirpath, dirnames, filenames in os.walk(src_work):
            dirnames[:] = [d for d in dirnames if not _skip_copy_dir(d)]
            for name in filenames:
                src = Path(dirpath) / name
                rel = _rel_posix(str(src_work), str(src))
                dest = dest_work / rel if mid in (".", "") else dest_work / mid / rel
                dest.parent.mkdir(parents=True, exist_ok=True)
                try:
                    shutil.copy2(src, dest)
                except OSError:
                    continue
                folder_rel = _rel_posix(str(dest_work), str(dest))
                meta = file_files.get(rel)
                files[folder_rel] = dict(meta) if isinstance(meta, dict) else {"base_hash": file_hash(dest)}
        with self._lock:
            self._save_manifest(folder_sess)
        return folder_sess

    def _absorb_file_sessions(self, folder_sess: dict[str, Any]) -> dict[str, Any]:
        root = str(folder_sess.get("source_root") or "")
        if not root:
            return folder_sess
        for fs in self.find_file_sessions_under(root):
            if fs.get("id") == folder_sess.get("id"):
                continue
            folder_sess = self._merge_file_session_into(folder_sess, fs)
            self._drop_session(fs)
        return folder_sess

    def _refresh_file_session(self, sess: dict[str, Any], src: Path) -> int:
        work = Path(sess["work_root"])
        rel = src.name
        dst = work / rel
        files = sess.setdefault("files", {})
        added = 0
        if not dst.exists():
            if rel in files:
                return added
            shutil.copy2(src, dst)
            files[rel] = {"base_hash": file_hash(src)}
            added += 1
        assets = src.with_name(src.stem + ".assets")
        if assets.is_dir():
            dest_assets = work / assets.name
            for dirpath, dirnames, filenames in os.walk(assets):
                dirnames[:] = [d for d in dirnames if not _skip_copy_dir(d)]
                for name in filenames:
                    s = Path(dirpath) / name
                    r = assets.name + "/" + _rel_posix(str(assets), str(s))
                    d = work / r
                    if d.exists():
                        continue
                    try:
                        if s.stat().st_size > MAX_COPY_BYTES:
                            continue
                        d.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(s, d)
                        files[r] = {"base_hash": file_hash(s)}
                        added += 1
                    except OSError:
                        continue
        return added

    # ------------------------------------------------------------------
    # 路径映射
    # ------------------------------------------------------------------
    def map_to_work(self, sess: dict[str, Any], path: str) -> str:
        work = str(sess["work_root"])
        source = str(sess["source_root"])
        if _is_under(path, work):
            return str(Path(path))
        if _is_under(path, source):
            rel = _rel_posix(source, path)
            if rel in (".", ""):
                return work
            return str(Path(work) / rel)
        return str(Path(work) / Path(path).name)

    def map_to_source(self, sess: dict[str, Any], path: str) -> str:
        work = str(sess["work_root"])
        source = str(sess["source_root"])
        if _is_under(path, source):
            return str(Path(path))
        if _is_under(path, work):
            rel = _rel_posix(work, path)
            if rel in (".", ""):
                return source
            return str(Path(source) / rel)
        return str(Path(source) / Path(path).name)

    def rel_of(self, sess: dict[str, Any], path: str) -> str:
        work = self.map_to_work(sess, path)
        return _rel_posix(str(sess["work_root"]), work)

    # ------------------------------------------------------------------
    # 状态 / 回写 / 合并
    # ------------------------------------------------------------------
    def file_status(self, sess: dict[str, Any], path: str) -> dict[str, Any]:
        rel = self.rel_of(sess, path)
        src = Path(sess["source_root"]) / rel
        wrk = Path(sess["work_root"]) / rel
        src_ok = src.is_file()
        wrk_ok = wrk.is_file()
        base = ((sess.get("files") or {}).get(rel) or {}).get("base_hash") or ""
        sh = file_hash(src) if src_ok else ""
        wh = file_hash(wrk) if wrk_ok else ""
        if not src_ok and not wrk_ok:
            state = "missing"
        elif not src_ok and wrk_ok:
            state = "copy_only"
        elif src_ok and not wrk_ok:
            state = "source_only"
        elif sh == wh:
            state = "in_sync"
        elif base and sh == base and wh != base:
            state = "copy_ahead"
        elif base and wh == base and sh != base:
            state = "source_ahead"
        else:
            state = "diverged"
        return {
            "ok": True,
            "rel": rel,
            "status": state,
            "source_path": str(src),
            "work_path": str(wrk),
            "source_exists": src_ok,
            "work_exists": wrk_ok,
            "source_hash": sh,
            "work_hash": wh,
            "base_hash": base,
            "source_root": sess["source_root"],
            "work_root": sess["work_root"],
        }

    def push_file(self, sess: dict[str, Any], path: str, force: bool = False) -> dict[str, Any]:
        info = self.file_status(sess, path)
        if info["status"] == "missing":
            return {**info, "ok": False, "error": "文件不存在"}
        if not info["work_exists"]:
            return {**info, "ok": False, "error": "工作副本中没有该文件"}
        if info["status"] == "in_sync":
            return {**info, "ok": True, "message": "已与源文件一致", "pushed": False}
        if info["status"] == "source_ahead" and not force:
            return {
                **info,
                "ok": False,
                "needs_confirm": True,
                "reason": "source_ahead",
                "error": "源文件较新，覆盖将丢掉源文件上的改动",
            }
        if info["status"] == "diverged" and not force:
            return {
                **info,
                "ok": False,
                "needs_confirm": True,
                "reason": "diverged",
                "error": "源文件与工作副本都有改动",
            }
        src = Path(info["source_path"])
        wrk = Path(info["work_path"])
        data = wrk.read_bytes()
        atomic_write_bytes(src, data)
        rel = info["rel"]
        with self._lock:
            fresh = self._load_manifest(sess["id"]) or sess
            files = fresh.setdefault("files", {})
            files[rel] = {"base_hash": file_hash(src)}
            self._save_manifest(fresh)
            sess.clear()
            sess.update(fresh)
        info = self.file_status(sess, path)
        return {**info, "ok": True, "message": "已保存至源文件", "pushed": True}

    def merge_file(self, sess: dict[str, Any], path: str, strategy: str = "") -> dict[str, Any]:
        info = self.file_status(sess, path)
        if info["status"] == "missing":
            return {**info, "ok": False, "error": "文件不存在"}
        if not info["source_exists"]:
            return {**info, "ok": False, "error": "源文件不存在，无法合并"}
        if info["status"] == "in_sync":
            return {**info, "ok": True, "message": "已与源文件一致", "merged": False}
        if info["status"] == "copy_ahead" and not strategy:
            return {
                **info,
                "ok": False,
                "needs_confirm": True,
                "reason": "copy_ahead",
                "error": "工作副本有未回写的修改，合并将用源文件覆盖副本",
            }
        if info["status"] == "diverged" and not strategy:
            return {
                **info,
                "ok": False,
                "needs_confirm": True,
                "reason": "diverged",
                "error": "源文件与工作副本都有改动",
            }
        if strategy == "keep_copy":
            return {**info, "ok": True, "message": "已保留工作副本", "merged": False}

        src = Path(info["source_path"])
        wrk = Path(info["work_root"]) / info["rel"]
        if strategy == "markers" and info["work_exists"]:
            try:
                left = wrk.read_text(encoding="utf-8")
                right = src.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                strategy = "keep_source"
            else:
                merged = (
                    "<<<<<<< 工作副本\n"
                    + left
                    + ("" if left.endswith("\n") else "\n")
                    + "=======\n"
                    + right
                    + ("" if right.endswith("\n") else "\n")
                    + ">>>>>>> 源文件\n"
                )
                atomic_write_text(wrk, merged)
                # 冲突标记后副本与源仍不同，base 对齐源，状态变为 copy_ahead
                with self._lock:
                    fresh = self._load_manifest(sess["id"]) or sess
                    fresh.setdefault("files", {})[info["rel"]] = {"base_hash": file_hash(src)}
                    self._save_manifest(fresh)
                    sess.clear()
                    sess.update(fresh)
                out = self.file_status(sess, path)
                try:
                    content = wrk.read_text(encoding="utf-8")
                except OSError:
                    content = ""
                return {
                    **out,
                    "ok": True,
                    "message": "已插入冲突标记，请在副本中手动处理",
                    "merged": True,
                    "content": content,
                }

        wrk.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, wrk)
        with self._lock:
            fresh = self._load_manifest(sess["id"]) or sess
            fresh.setdefault("files", {})[info["rel"]] = {"base_hash": file_hash(src)}
            self._save_manifest(fresh)
            sess.clear()
            sess.update(fresh)
        out = self.file_status(sess, path)
        content = ""
        if wrk.suffix.lower() in MD_EXTS:
            try:
                content = wrk.read_text(encoding="utf-8")
            except OSError:
                content = ""
        return {**out, "ok": True, "message": "已合并源文件到工作副本", "merged": True, "content": content}

    def iter_md_rels(self, sess: dict[str, Any]) -> list[str]:
        work = Path(sess["work_root"])
        source = Path(sess["source_root"])
        found: set[str] = set()
        for root in (work, source):
            if not root.is_dir():
                continue
            for dirpath, dirnames, filenames in os.walk(root):
                dirnames[:] = [d for d in dirnames if not _skip_copy_dir(d)]
                for name in filenames:
                    if Path(name).suffix.lower() not in MD_EXTS:
                        continue
                    found.add(_rel_posix(str(root), str(Path(dirpath) / name)))
        return sorted(found)

    def summary(self, sess: dict[str, Any]) -> dict[str, Any]:
        counts = {
            "in_sync": 0,
            "copy_ahead": 0,
            "source_ahead": 0,
            "diverged": 0,
            "copy_only": 0,
            "source_only": 0,
            "missing": 0,
        }
        for rel in self.iter_md_rels(sess):
            st = self.file_status(sess, str(Path(sess["work_root"]) / rel))
            counts[st["status"]] = counts.get(st["status"], 0) + 1
        pending = counts["copy_ahead"] + counts["copy_only"] + counts["diverged"]
        incoming = counts["source_ahead"] + counts["source_only"] + counts["diverged"]
        return {
            "ok": True,
            "source_root": sess["source_root"],
            "work_root": sess["work_root"],
            "kind": sess.get("kind") or "folder",
            "counts": counts,
            "pending": pending,
            "incoming": incoming,
        }

    def push_all(self, sess: dict[str, Any], force: bool = False) -> dict[str, Any]:
        pushed = 0
        pushed_rels: list[str] = []
        skipped: list[dict[str, Any]] = []
        for rel in self.iter_md_rels(sess):
            res = self.push_file(sess, str(Path(sess["work_root"]) / rel), force=force)
            if res.get("pushed"):
                pushed += 1
                pushed_rels.append(rel)
            elif res.get("needs_confirm"):
                skipped.append({"rel": rel, "reason": res.get("reason"), "error": res.get("error")})
            elif not res.get("ok") and res.get("status") not in ("in_sync", "source_only", "missing"):
                skipped.append({"rel": rel, "error": res.get("error")})
        return {
            "ok": True,
            "pushed": pushed,
            "pushed_rels": pushed_rels,
            "skipped": skipped,
            "message": f"已保存 {pushed} 个文件至源" + (f"，{len(skipped)} 个需确认" if skipped else ""),
        }

    def merge_all(self, sess: dict[str, Any], strategy: str = "") -> dict[str, Any]:
        merged = 0
        skipped: list[dict[str, Any]] = []
        for rel in self.iter_md_rels(sess):
            st = self.file_status(sess, str(Path(sess["work_root"]) / rel))
            if st["status"] in ("in_sync", "copy_only", "missing"):
                continue
            if st["status"] == "source_only":
                # 副本里删过或改过名：不要从源把旧路径复活回来
                wrk = Path(sess["work_root"]) / rel
                if rel in (sess.get("files") or {}) and not wrk.is_file():
                    continue
            if st["status"] == "copy_ahead" and not strategy:
                skipped.append({"rel": rel, "reason": "copy_ahead"})
                continue
            use = strategy or "keep_source"
            if st["status"] in ("source_ahead", "source_only"):
                use = "keep_source"
            res = self.merge_file(sess, str(Path(sess["work_root"]) / rel), strategy=use)
            if res.get("merged"):
                merged += 1
            elif res.get("needs_confirm"):
                skipped.append({"rel": rel, "reason": res.get("reason")})
        return {
            "ok": True,
            "merged": merged,
            "skipped": skipped,
            "message": f"已合并 {merged} 个源文件" + (f"，{len(skipped)} 个已跳过" if skipped else ""),
        }
