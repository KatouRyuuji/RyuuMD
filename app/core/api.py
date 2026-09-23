"""暴露给前端 JS 的 API：文件读写、文件夹树、配置存取。

pywebview 会把 Api 实例的公开方法挂到 window.pywebview.api.* 上，
前端通过 await window.pywebview.api.xxx() 调用。所有方法返回可 JSON 序列化的数据。
"""

from __future__ import annotations

import base64
import html as html_lib
import os
import shutil
import subprocess
import sys
import threading
import urllib.parse
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

try:
    import webview
except Exception:  # noqa: BLE001
    webview = None  # CLI / TUI / MCP 不需要 GUI 后端

from . import file_assoc
from .cloud_sync import get_engine, public_cloud, save_cloud
from . import anthropic as ai_client
from .config import Config
from .fsutil import IMAGE_EXTS, IGNORE_DIRS, MD_EXTS, atomic_write_text, recycle_file, skip_dir_name  # noqa: F401
from .projects import ProjectStore
from . import search as vault_search
from .workdir import EDIT_WORKDIR, TREE_DIRNAME, WorkdirStore, normalize_edit_mode

# 文件夹树扫描上限：层级与总条目数，超出即截断并在返回数据中标注
MAX_TREE_DEPTH = 8
MAX_TREE_ENTRIES = 2000
# 最近打开列表上限（最新在前，去重）
RECENT_MAX = 15
_KNOWLEDGE_CACHE_LOCK = threading.RLock()
_KNOWLEDGE_CACHE_REGISTRY: dict[str, dict[str, dict[str, Any]]] = {}


class Api:
    def __init__(
        self,
        config: Config,
        window_manager: Any = None,
        initial_path: str = "",
    ) -> None:
        self.config = config
        self.projects = ProjectStore(config)
        self.workdir = WorkdirStore(config.data_dir)
        self._cloud = get_engine(config, self.projects)
        self._window: Optional["webview.Window"] = None
        # 多窗口管理器（main.WindowManager）；测试/单窗口环境可为 None
        self._window_manager = window_manager
        # 本窗口的初始打开路径（命令行 / 单实例转发 / 新窗口传入），前端启动时拉取
        self._initial_path = initial_path
        self._latest_open_request = 0
        self._open_request_lock = threading.Lock()
        # 知识谱系缓存：规范化仓库根 → 生成结果；写盘或 refresh 时失效
        try:
            cache_owner = os.path.normcase(str(config.data_dir.resolve()))
        except OSError:
            cache_owner = os.path.normcase(str(config.data_dir))
        with _KNOWLEDGE_CACHE_LOCK:
            self._knowledge_cache = _KNOWLEDGE_CACHE_REGISTRY.setdefault(cache_owner, {})
        # 即时通讯传输；测试注入 MemoryTransport，未注入则按配置走 webhook
        self._im_transport: Any = None

    def bind_window(self, window: "webview.Window") -> None:
        self._window = window

    def _edit_mode(self) -> str:
        return normalize_edit_mode(self.config.get("edit_mode"))

    def _workdir_on(self) -> bool:
        return self._edit_mode() == EDIT_WORKDIR

    @staticmethod
    def _same_path(a: str, b: str) -> bool:
        if not a or not b:
            return False
        return os.path.normcase(os.path.normpath(a)) == os.path.normcase(os.path.normpath(b))

    @staticmethod
    def _path_under(child: str, parent: str) -> bool:
        if not child or not parent:
            return False
        try:
            Path(child).resolve().relative_to(Path(parent).resolve())
            return True
        except (OSError, ValueError):
            return False

    def _orphan_rel(self, raw: str) -> str:
        """从残骸副本路径取出 tree/ 之后的相对路径；没有则退回文件名。"""
        try:
            parts = Path(raw).parts
            if TREE_DIRNAME in parts:
                idx = len(parts) - 1 - parts[::-1].index(TREE_DIRNAME)
                rel = Path(*parts[idx + 1 :])
                if rel.parts:
                    return rel.as_posix()
        except (OSError, ValueError, IndexError):
            pass
        return Path(raw).name

    def _resolve_orphan_work_path(self, raw: str) -> tuple[str, str, Optional[dict[str, Any]]]:
        """单文件会话被整库收编后，把残骸路径改挂到 last_file / last_folder。"""
        last_file = (self.config.get("last_file") or "").strip()
        last_folder = (self.config.get("last_folder") or "").strip()
        if last_file and not self._same_path(last_file, raw) and not self._is_orphan_work_path(last_file):
            return self._resolve_io(last_file, create=True)
        if last_folder and not self._same_path(last_folder, raw) and not self._is_orphan_work_path(last_folder):
            _io, _logical, sess = self._resolve_io(last_folder, create=True)
            if sess:
                rel = self._orphan_rel(raw)
                work = str(Path(sess["work_root"]) / rel)
                logical = str(Path(sess["source_root"]) / rel)
                return work, logical, sess
        return raw, raw, None

    def _resolve_io(self, path: str, *, create: bool = False) -> tuple[str, str, Optional[dict[str, Any]]]:
        """工作副本模式下把源路径映射到副本；直改模式原样返回。"""
        raw = (path or "").strip()
        if not raw or not self._workdir_on():
            return raw, raw, None
        sess = self.workdir.find_session_for_path(raw)
        if not sess and self._is_orphan_work_path(raw):
            return self._resolve_orphan_work_path(raw)
        if not sess and create:
            hint = ""
            try:
                proj = self.projects.find_by_containing_path(raw)
                if proj:
                    hint = str(proj.get("path") or "")
            except Exception:  # noqa: BLE001
                hint = ""
            if not hint:
                hint = self.config.get("last_folder", "") or ""
            try:
                sess = self.workdir.ensure_for_path(raw, hint)
            except FileNotFoundError:
                sess = None
        if not sess:
            return raw, raw, None
        work = self.workdir.map_to_work(sess, raw)
        logical = self.workdir.map_to_source(sess, raw)
        return work, logical, sess

    def _is_orphan_work_path(self, path: str) -> bool:
        """副本目录里已无会话的路径（单文件会话被整库收编后）。"""
        if not path or not self._workdir_on():
            return False
        try:
            base = str(self.workdir.base)
            child = os.path.normcase(str(Path(path).resolve()))
            parent = os.path.normcase(str(Path(base).resolve()))
        except OSError:
            return False
        if child != parent and not child.startswith(parent + os.sep):
            return False
        return self.workdir.find_session_for_path(path) is None

    def _workdir_mapped(self, path: str) -> str:
        """已有会话才映射，不创建、不收编。"""
        io_path, _logical, sess = self._resolve_io(path, create=False)
        if sess and io_path:
            return io_path
        return ""

    def _search_root_from_file_session(self, hint: str = "") -> str:
        """只用当前打开文件的副本当搜索根，避免搜索升级整库。"""
        last_file = (self.config.get("last_file") or "").strip()
        if not last_file:
            return ""
        if hint and not self._path_under(last_file, hint):
            return ""
        io_path, _logical, sess = self._resolve_io(last_file, create=False)
        if not sess or not io_path:
            return ""
        wp = Path(io_path)
        return str(wp.parent if wp.is_file() else wp)

    def _attach_workdir_meta(self, payload: dict[str, Any], sess: Optional[dict[str, Any]]) -> dict[str, Any]:
        payload["edit_mode"] = self._edit_mode()
        if sess:
            payload["source_root"] = sess.get("source_root") or ""
            payload["work_root"] = sess.get("work_root") or ""
            payload["workdir_new_files"] = int(sess.get("last_added") or 0)
        return payload

    # ------------------------------------------------------------------
    # 窗口启动参数
    # ------------------------------------------------------------------
    def get_initial_path(self) -> str:
        """前端 boot 时主动拉取本窗口初始路径（替代 evaluate_js 注入，无竞态）。"""
        return self._initial_path or ""

    # ------------------------------------------------------------------
    # 配置
    # ------------------------------------------------------------------
    def get_config(self) -> dict[str, Any]:
        data = self.config.all()
        data["cloud_sync"] = public_cloud(data.get("cloud_sync"))
        data["ai"] = ai_client.public_ai(data.get("ai"))
        return data

    def set_config(self, key: str, value: Any) -> dict[str, Any]:
        if key == "ai":
            value = ai_client.merge_ai(self.config.get("ai"), value)
        self.config.set(key, value)
        return {"ok": True}

    def update_config(self, values: dict[str, Any]) -> dict[str, Any]:
        values = dict(values or {})
        if "ai" in values:
            values["ai"] = ai_client.merge_ai(self.config.get("ai"), values.get("ai"))
        self.config.update(values)
        return {"ok": True}

    def set_titlebar_theme(self, dark: bool) -> dict[str, Any]:
        """前端主题切换时同步原生标题栏明暗（Windows DWM 沉浸式深色）。"""
        from app.core.titlebar import set_dark_titlebar

        set_dark_titlebar(bool(dark))
        return {"ok": True}

    # ------------------------------------------------------------------
    # 最近打开列表（文件/文件夹，最新在前、去重、限长）
    # ------------------------------------------------------------------
    def _add_recent(self, path: str, kind: str) -> None:
        """记录一次打开。在各打开动作成功时调用；失败不影响主流程。"""
        try:
            items = self.config.get("recent_files", []) or []
            items = [it for it in items if isinstance(it, dict) and it.get("path") != path]
            items.insert(0, {"path": path, "kind": kind})
            self.config.set("recent_files", items[:RECENT_MAX])
        except Exception:  # noqa: BLE001
            pass

    def get_recent(self) -> dict[str, Any]:
        items = self.config.get("recent_files", []) or []
        out: list[dict[str, Any]] = []
        for it in items:
            if not isinstance(it, dict):
                continue
            raw = it.get("path", "")
            if not raw:
                continue
            p = Path(raw)
            exists = p.exists()
            if not exists and self._workdir_on():
                io_path = self._workdir_mapped(raw)
                if io_path:
                    exists = Path(io_path).exists()
            out.append(
                {
                    "path": raw,
                    "name": p.name or raw,
                    "kind": it.get("kind", "file"),
                    # 失效（已移动/删除）项保留展示但前端灰显，点击打开失败时自动移除
                    "exists": exists,
                }
            )
        return {"ok": True, "items": out}

    def clear_recent(self) -> dict[str, Any]:
        self.config.set("recent_files", [])
        return {"ok": True}

    def remove_recent(self, path: str) -> dict[str, Any]:
        items = self.config.get("recent_files", []) or []
        self.config.set(
            "recent_files",
            [it for it in items if isinstance(it, dict) and it.get("path") != path],
        )
        return {"ok": True}

    # ------------------------------------------------------------------
    # 文件读写
    # ------------------------------------------------------------------
    def read_file(self, path: str, open_request_id: Optional[int] = None) -> dict[str, Any]:
        try:
            io_path, logical, sess = self._resolve_io(path, create=True)
            p = Path(io_path)
            if not p.exists() or not p.is_file():
                return {"ok": False, "error": "文件不存在"}
            content = p.read_text(encoding="utf-8")
            record_open = True
            if open_request_id is not None:
                request_id = int(open_request_id)
                with self._open_request_lock:
                    if request_id < self._latest_open_request:
                        record_open = False
                    else:
                        self._latest_open_request = request_id
            if record_open:
                self.config.set("last_file", logical)
                self._add_recent(logical, "file")
            st = p.stat()
            return self._attach_workdir_meta(
                {
                    "ok": True,
                    "path": str(p),
                    "source_path": logical,
                    "name": p.name,
                    "content": content,
                    "size": st.st_size,
                    "mtime": st.st_mtime,
                },
                sess,
            )
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": str(e)}

    def save_file(self, path: str, content: str) -> dict[str, Any]:
        try:
            io_path, logical, sess = self._resolve_io(path, create=True)
            p = Path(io_path)
            # 原子写：自动保存高频触发，崩溃/断电不得留下半截笔记
            atomic_write_text(p, content)
            self.config.set("last_file", logical)
            self._add_recent(logical, "file")
            self._invalidate_knowledge(logical)
            # 工作副本只写副本；云同步跟源文件走，等「保存至源」再推
            if not sess:
                self._maybe_cloud_push(str(p))
            st = p.stat()
            return self._attach_workdir_meta(
                {"ok": True, "path": str(p), "source_path": logical, "mtime": st.st_mtime, "size": st.st_size},
                sess,
            )
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": str(e)}

    def new_file(self, folder: str, name: str) -> dict[str, Any]:
        try:
            if not name.lower().endswith(tuple(MD_EXTS)):
                name += ".md"
            if any(sep in name for sep in ("/", "\\", ":")):
                return {"ok": False, "error": "名称不能包含路径分隔符"}
            raw_folder = (folder or "").strip()
            io_folder, logical_folder, sess = (
                self._resolve_io(raw_folder, create=True) if raw_folder else ("", "", None)
            )
            target = Path(io_folder) / name if io_folder else Path(name)
            logical_path = str(Path(logical_folder) / name) if logical_folder else str(target)
            if target.exists():
                return {"ok": False, "error": "同名文件已存在"}
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("", encoding="utf-8")
            self._add_recent(logical_path, "file")
            self._invalidate_knowledge(logical_path)
            return self._attach_workdir_meta(
                {"ok": True, "path": str(target), "name": target.name, "source_path": logical_path},
                sess,
            )
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": str(e)}

    def new_folder(self, parent: str, name: str) -> dict[str, Any]:
        """在已打开的仓库目录下新建子文件夹。"""
        try:
            raw_parent = (parent or "").strip()
            io_parent, logical_parent, sess = self._resolve_io(raw_parent, create=True)
            folder = Path(io_parent)
            if not folder.is_dir():
                return {"ok": False, "error": "父文件夹不存在"}
            raw = (name or "").strip()
            if not raw:
                return {"ok": False, "error": "名称不能为空"}
            if any(sep in raw for sep in ("/", "\\", ":")) or raw in (".", ".."):
                return {"ok": False, "error": "名称不能包含路径分隔符"}
            target = folder / raw
            if target.exists():
                return {"ok": False, "error": "同名文件夹已存在"}
            target.mkdir()
            logical_path = str(Path(logical_parent) / raw) if logical_parent else str(target)
            return self._attach_workdir_meta(
                {"ok": True, "path": str(target), "name": target.name, "source_path": logical_path},
                sess,
            )
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": str(e)}

    # ------------------------------------------------------------------
    # 文件管理：重命名 / 移动 / 删除（直接操作磁盘上的真实文件）
    # ------------------------------------------------------------------
    def _sync_path_refs(self, old: str, new: str) -> None:
        """重命名/移动后同步配置中的路径引用（最近列表 + last_file），失败不影响主流程。"""
        try:
            items = self.config.get("recent_files", []) or []
            changed = False
            for it in items:
                if isinstance(it, dict) and it.get("path") == old:
                    it["path"] = new
                    changed = True
            if changed:
                self.config.set("recent_files", items)
            if self.config.get("last_file", "") == old:
                self.config.set("last_file", new)
        except Exception:  # noqa: BLE001
            pass

    @staticmethod
    def _is_md_file(p: Path) -> bool:
        return p.is_file() and p.suffix.lower() in MD_EXTS

    def _finish_path_change(
        self,
        old_io: str,
        old_logical: str,
        new_io: str,
        sess: Optional[dict[str, Any]],
        *,
        name: str = "",
    ) -> dict[str, Any]:
        new_logical = self.workdir.map_to_source(sess, new_io) if sess else new_io
        self._sync_path_refs(old_logical, new_logical)
        if old_io != old_logical:
            self._sync_path_refs(old_io, new_io)
        self._invalidate_knowledge(old_logical)
        self._invalidate_knowledge(new_logical)
        return self._attach_workdir_meta(
            {
                "ok": True,
                "path": new_io,
                "name": name or Path(new_io).name,
                "source_path": new_logical,
            },
            sess,
        )

    def rename_file(self, path: str, new_name: str) -> dict[str, Any]:
        """重命名真实 md 文件（仅同目录改名，不接受带路径的名字）。"""
        try:
            io_path, logical, sess = self._resolve_io(path, create=False)
            p = Path(io_path)
            if not self._is_md_file(p):
                return {"ok": False, "error": "不是已存在的 Markdown 文件"}
            name = (new_name or "").strip()
            if not name:
                return {"ok": False, "error": "名称不能为空"}
            if any(sep in name for sep in ("/", "\\", ":")):
                return {"ok": False, "error": "名称不能包含路径分隔符"}
            if not name.lower().endswith(tuple(MD_EXTS)):
                name += ".md"
            target = p.with_name(name)
            # 按文件名大小写折叠比较。Darwin 上 os.path.normcase 不去大小写，
            # 不能靠它识别 note.md → NOTE.md。
            if p.name.lower() == target.name.lower():
                if p.name == target.name:
                    return self._attach_workdir_meta(
                        {"ok": True, "path": str(p), "name": p.name, "source_path": logical},
                        sess,
                    )
                # 仅大小写变化：大小写不敏感文件系统上 target.exists() 恒真，
                # 不能被「同名已存在」拦截。Linux 上 NOTE.md 可能是另一文件。
                if target.exists():
                    try:
                        if not os.path.samefile(p, target):
                            return {"ok": False, "error": "同名文件已存在"}
                    except OSError:
                        return {"ok": False, "error": "同名文件已存在"}
                os.rename(p, target)
                return self._finish_path_change(str(p), logical, str(target), sess, name=target.name)
            if target.exists():
                return {"ok": False, "error": "同名文件已存在"}
            os.rename(p, target)
            return self._finish_path_change(str(p), logical, str(target), sess, name=target.name)
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": str(e)}

    def move_file(self, path: str, target_folder: str) -> dict[str, Any]:
        """把真实 md 文件移动到目标目录（shutil.move 跨盘安全）。"""
        try:
            io_path, logical, sess = self._resolve_io(path, create=False)
            io_folder, _logical_folder, sess_folder = self._resolve_io(target_folder, create=True)
            p = Path(io_path)
            if not self._is_md_file(p):
                return {"ok": False, "error": "不是已存在的 Markdown 文件"}
            folder = Path(io_folder)
            if not folder.is_dir():
                return {"ok": False, "error": "目标文件夹不存在"}
            same = os.path.normcase(os.path.normpath(str(folder)))
            if same == os.path.normcase(os.path.normpath(str(p.parent))):
                return {"ok": False, "error": "文件已在该文件夹中"}
            target = folder / p.name
            if target.exists():
                return {"ok": False, "error": "目标位置已存在同名文件"}
            shutil.move(str(p), str(target))
            return self._finish_path_change(
                str(p), logical, str(target), sess_folder, name=target.name
            )
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": str(e)}

    def move_file_dialog(self, path: str) -> dict[str, Any]:
        """弹文件夹选择框，把文件移动到所选目录。"""
        if not self._window:
            return {"ok": False, "error": "窗口未就绪"}
        result = self._window.create_file_dialog(webview.FOLDER_DIALOG)
        if not result:
            return {"ok": False, "cancelled": True}
        return self.move_file(path, result[0])

    def delete_file(self, path: str) -> dict[str, Any]:
        """把真实 md 文件移入系统回收站（风险确认弹窗由前端负责）。"""
        try:
            io_path, logical, sess = self._resolve_io(path, create=False)
            p = Path(io_path)
            if not self._is_md_file(p):
                return {"ok": False, "error": "不是已存在的 Markdown 文件"}
            recycle_file(str(p))
            self.remove_recent(str(p))
            self.remove_recent(logical)
            if path and path not in (str(p), logical):
                self.remove_recent(path)
            self._invalidate_knowledge(logical)
            last = self.config.get("last_file", "")
            if last in (str(p), logical, path):
                self.config.set("last_file", "")
            return self._attach_workdir_meta({"ok": True, "source_path": logical}, sess)
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": str(e)}

    def duplicate_file(self, path: str) -> dict[str, Any]:
        """在同目录复制一份 md（`名 副本.md` / `名 副本 2.md`）。"""
        try:
            io_path, _logical, sess = self._resolve_io(path, create=False)
            p = Path(io_path)
            if not self._is_md_file(p):
                return {"ok": False, "error": "不是已存在的 Markdown 文件"}
            dest = None
            for n in range(1, 100):
                suffix = " 副本" if n == 1 else f" 副本 {n}"
                cand = p.with_name(p.stem + suffix + p.suffix)
                if not cand.exists():
                    dest = cand
                    break
            if dest is None:
                return {"ok": False, "error": "副本过多"}
            shutil.copy2(str(p), str(dest))
            dest_logical = self.workdir.map_to_source(sess, str(dest)) if sess else str(dest)
            self._add_recent(dest_logical, "file")
            self._invalidate_knowledge(dest_logical)
            return self._attach_workdir_meta(
                {"ok": True, "path": str(dest), "name": dest.name, "source_path": dest_logical},
                sess,
            )
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": str(e)}

    # ------------------------------------------------------------------
    # 文件夹树
    # ------------------------------------------------------------------
    def list_folder(self, path: str) -> dict[str, Any]:
        try:
            io_path, logical, sess = self._resolve_io(path, create=True)
            root = Path(io_path)
            if not root.exists() or not root.is_dir():
                return {"ok": False, "error": "文件夹不存在"}
            self.config.set("last_folder", logical)
            self._add_recent(logical, "folder")
            # 条目预算贯穿整棵树；truncated 让前端明确提示「已截断」
            budget: dict[str, Any] = {"left": MAX_TREE_ENTRIES, "truncated": False}
            tree = self._scan_dir(root, 0, budget)
            display = Path(logical)
            return self._attach_workdir_meta(
                {
                    "ok": True,
                    "root": str(root),
                    "name": display.name or root.name,
                    "tree": tree,
                    "truncated": budget["truncated"],
                },
                sess,
            )
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": str(e)}

    def _scan_dir(self, directory: "Path | str", depth: int, budget: dict[str, Any]) -> list[dict[str, Any]]:
        """递归扫描目录。os.scandir 一次目录读取即携带类型信息（免逐文件 stat）；
        深度/条目双上限截断；不跟随符号链接（防目录环）。"""
        items: list[dict[str, Any]] = []
        if depth > MAX_TREE_DEPTH:
            # 深度超限：下面还有条目才标注截断，避免空目录误报
            try:
                with os.scandir(directory) as it:
                    if next(it, None) is not None:
                        budget["truncated"] = True
            except OSError:
                pass
            return items
        try:
            with os.scandir(directory) as it:
                entries = list(it)
        except OSError:
            return items
        # 目录在前、按名称排序；is_dir 读 scandir 缓存，无额外系统调用
        entries.sort(key=lambda e: (not e.is_dir(follow_symlinks=False), e.name.lower()))
        for entry in entries:
            if entry.is_dir(follow_symlinks=False):
                # 隐藏目录与依赖/构建类巨型目录一律跳过
                if skip_dir_name(entry.name):
                    continue
                if budget["left"] <= 0:
                    budget["truncated"] = True
                    break
                budget["left"] -= 1
                items.append(
                    {
                        "type": "dir",
                        "name": entry.name,
                        "path": entry.path,
                        "children": self._scan_dir(entry.path, depth + 1, budget),
                    }
                )
            elif os.path.splitext(entry.name)[1].lower() in MD_EXTS:
                if budget["left"] <= 0:
                    budget["truncated"] = True
                    break
                budget["left"] -= 1
                items.append({"type": "file", "name": entry.name, "path": entry.path})
        return items

    # ------------------------------------------------------------------
    # 原生对话框
    # ------------------------------------------------------------------
    def _workdir_session(
        self, path: str = "", *, create: bool = False
    ) -> tuple[Optional[dict[str, Any]], Optional[dict[str, Any]], str]:
        if not self._workdir_on():
            return None, {"ok": False, "error": "当前为直改源文件", "edit_mode": "source"}, path or ""
        raw = (path or "").strip() or (self.config.get("last_folder", "") or "")
        if not raw:
            raw = self.config.get("last_file", "") or ""
        if not raw:
            return None, {"ok": False, "error": "请先打开仓库或文件", "edit_mode": EDIT_WORKDIR}, ""
        io_path, _logical, sess = self._resolve_io(raw, create=create)
        if not sess:
            sess = self.workdir.find_session_for_path(raw)
        if not sess:
            return None, {"ok": False, "error": "尚未建立工作副本", "edit_mode": EDIT_WORKDIR}, raw
        return sess, None, io_path or raw

    def workdir_status(self, path: str = "") -> dict[str, Any]:
        """当前文件相对源文件的同步状态（未回写 / 源有更新 / 冲突）。"""
        raw = (path or "").strip() or (self.config.get("last_file", "") or "")
        sess, err, resolved = self._workdir_session(raw)
        if err:
            return err
        assert sess is not None
        if not raw:
            return {"ok": True, "status": "idle", **self.workdir.summary(sess), "edit_mode": EDIT_WORKDIR}
        out = self.workdir.file_status(sess, resolved)
        out["edit_mode"] = EDIT_WORKDIR
        return out

    def workdir_summary(self, folder: str = "") -> dict[str, Any]:
        sess, err, _resolved = self._workdir_session(folder, create=False)
        if err:
            return err
        assert sess is not None
        out = self.workdir.summary(sess)
        out["edit_mode"] = EDIT_WORKDIR
        return out

    def workdir_push(self, path: str = "", force: bool = False) -> dict[str, Any]:
        """把当前工作副本写回源文件。"""
        raw = (path or "").strip() or (self.config.get("last_file", "") or "")
        sess, err, resolved = self._workdir_session(raw)
        if err:
            return err
        assert sess is not None
        out = self.workdir.push_file(sess, resolved, force=bool(force))
        out["edit_mode"] = EDIT_WORKDIR
        if out.get("ok") and out.get("pushed"):
            src = out.get("source_path") or ""
            if src:
                self._invalidate_knowledge(src)
                self._maybe_cloud_push(src)
        return out

    def workdir_merge(self, path: str = "", strategy: str = "") -> dict[str, Any]:
        """把源文件合并进工作副本。strategy: keep_source | keep_copy | markers。"""
        raw = (path or "").strip() or (self.config.get("last_file", "") or "")
        sess, err, resolved = self._workdir_session(raw)
        if err:
            return err
        assert sess is not None
        out = self.workdir.merge_file(sess, resolved, strategy=str(strategy or ""))
        out["edit_mode"] = EDIT_WORKDIR
        if out.get("ok") and out.get("merged"):
            self._invalidate_knowledge(resolved)
        return out

    def workdir_push_all(self, folder: str = "", force: bool = False) -> dict[str, Any]:
        sess, err, _resolved = self._workdir_session(folder, create=False)
        if err:
            return err
        assert sess is not None
        out = self.workdir.push_all(sess, force=bool(force))
        out["edit_mode"] = EDIT_WORKDIR
        if out.get("ok") and out.get("pushed"):
            self._invalidate_knowledge(sess["source_root"])
            for rel in out.get("pushed_rels") or []:
                self._maybe_cloud_push(str(Path(sess["source_root"]) / rel))
        return out

    def workdir_merge_all(self, folder: str = "", strategy: str = "") -> dict[str, Any]:
        sess, err, _resolved = self._workdir_session(folder, create=False)
        if err:
            return err
        assert sess is not None
        out = self.workdir.merge_all(sess, strategy=str(strategy or ""))
        out["edit_mode"] = EDIT_WORKDIR
        if out.get("ok") and out.get("merged"):
            self._invalidate_knowledge(sess["work_root"])
        return out

    def open_file_dialog(self) -> dict[str, Any]:
        if not self._window:
            return {"ok": False, "error": "窗口未就绪"}
        # 过滤词与 MD_EXTS 全量对齐（.mdown/.mkd 等也能从对话框选中）
        exts = ";".join(f"*{e}" for e in sorted(MD_EXTS))
        result = self._window.create_file_dialog(
            webview.OPEN_DIALOG,
            allow_multiple=False,
            file_types=(f"Markdown ({exts})", "All files (*.*)"),
        )
        if not result:
            return {"ok": False, "cancelled": True}
        return self.read_file(result[0])

    def open_folder_dialog(self) -> dict[str, Any]:
        if not self._window:
            return {"ok": False, "error": "窗口未就绪"}
        result = self._window.create_file_dialog(webview.FOLDER_DIALOG)
        if not result:
            return {"ok": False, "cancelled": True}
        return self.list_folder(result[0])

    def save_file_dialog(self, content: str, suggested: str = "未命名.md") -> dict[str, Any]:
        if not self._window:
            return {"ok": False, "error": "窗口未就绪"}
        result = self._window.create_file_dialog(
            webview.SAVE_DIALOG,
            save_filename=suggested,
            file_types=("Markdown (*.md)", "All files (*.*)"),
        )
        if not result:
            return {"ok": False, "cancelled": True}
        path = result if isinstance(result, str) else result[0]
        return self.save_file(path, content)

    # ------------------------------------------------------------------
    # 拖入路径解析（前端拿到本地路径后调用）
    # ------------------------------------------------------------------
    def open_path(self, path: str) -> dict[str, Any]:
        p = Path(path)
        if p.is_dir():
            return self.list_folder(str(p))
        if p.is_file() and p.suffix.lower() in MD_EXTS:
            return self.read_file(str(p))
        return {"ok": False, "error": "不支持的文件类型"}

    def open_dropped_files(self, names: list[str]) -> dict[str, Any]:
        """前端捕获阶段 drop 转发的落地处理：按文件名取真实路径并打开。

        背景：Vditor 编辑器面板的 drop 处理器首行即 stopPropagation，pywebview
        的 document 级 drop 监听（main.py _on_drop 通道）收不到事件。前端改为
        捕获阶段接管（app.js bindDragDrop），先经 WebView2 的
        postMessageWithAdditionalObjects("FilesDropped", files) 把真实 File 回传
        原生层 —— pywebview 会把路径暂存到 webview.dom._dnd_state['paths']
        （(basename, fullpath) 列表），本方法按文件名匹配取出首个可打开项。

        注意：依赖 pywebview 的 _dnd_state 内部结构（FilesDropped 机制的官方落点，
        与原 _on_drop 通道同源）；两条 WebMessage 按序派发，本方法执行时 paths
        必然已就绪，无竞态。
        """
        from webview.dom import _dnd_state  # pywebview FilesDropped 通道路径暂存

        try:
            for name in names or []:
                match = [
                    item
                    for item in _dnd_state["paths"]
                    if urllib.parse.unquote(item[0]) == name
                ]
                if not match:
                    continue
                full = urllib.parse.unquote(match[0][1])
                _dnd_state["paths"].remove(match[0])
                p = Path(full)
                if p.is_dir() or (p.is_file() and p.suffix.lower() in MD_EXTS):
                    safe = full.replace("\\", "\\\\").replace("'", "\\'")
                    if self._window:
                        # fire-and-forget：__openDroppedPath 是 async（含读文件与
                        # Vditor 渲染），evaluate_js 默认会同步等到 Promise 完成，
                        # 整个打开期间占死本次调用的 Python 线程；逗号表达式立即
                        # 返回 true，打开流程在 JS 侧后台推进（失败由前端 toast）。
                        self._window.evaluate_js(
                            "window.__openDroppedPath && "
                            f"(window.__openDroppedPath('{safe}'), true)"
                        )
                    return {"ok": True, "path": full}
            return {"ok": False, "error": "无可打开的拖入项"}
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": str(e)}

    # ------------------------------------------------------------------
    # 启动时恢复上次会话
    # ------------------------------------------------------------------
    def restore_session(self) -> dict[str, Any]:
        out: dict[str, Any] = {"ok": True, "folder": None, "file": None}
        folder = self.config.get("last_folder", "")
        if folder and Path(folder).is_dir():
            out["folder"] = self.list_folder(folder)
        last = self.config.get("last_file", "")
        if last and Path(last).is_file():
            out["file"] = self.read_file(last)
        return out

    # ------------------------------------------------------------------
    # 仓库（项目）管理：首页仓库列表的数据源
    # ------------------------------------------------------------------
    def list_projects(self) -> dict[str, Any]:
        return {"ok": True, "items": self.projects.list()}

    def add_project(self, path: str, name: str = "") -> dict[str, Any]:
        return self.projects.add(path, name)

    def create_project(self, parent: str, name: str) -> dict[str, Any]:
        """在父目录下新建文件夹并注册为仓库。"""
        return self.projects.create(parent, name)

    def create_directory(self, parent: str, name: str) -> dict[str, Any]:
        """在任意已存在的父目录下新建文件夹。"""
        folder = Path(parent or "")
        if not folder.is_dir():
            return {"ok": False, "error": "父文件夹不存在"}
        raw = (name or "").strip()
        if not raw:
            return {"ok": False, "error": "名称不能为空"}
        if any(sep in raw for sep in ("/", "\\", ":")) or raw in (".", ".."):
            return {"ok": False, "error": "名称不能包含路径分隔符"}
        target = folder / raw
        if target.exists():
            if target.is_dir():
                return {"ok": True, "path": str(target), "name": target.name, "existed": True}
            return {"ok": False, "error": "同名文件已存在"}
        try:
            target.mkdir()
        except OSError as e:
            return {"ok": False, "error": str(e)}
        return {"ok": True, "path": str(target), "name": target.name, "existed": False}

    def scratch_path(self) -> str:
        return str(self.config.data_dir / "scratch.md")

    def read_scratch(self) -> dict[str, Any]:
        """读取首页随手记（用户数据目录 scratch.md），不改 last_file / 最近打开。"""
        p = Path(self.scratch_path())
        if not p.is_file():
            return {"ok": True, "path": str(p), "name": "scratch.md", "content": "", "size": 0}
        try:
            content = p.read_text(encoding="utf-8")
            st = p.stat()
            return {
                "ok": True,
                "path": str(p),
                "name": "scratch.md",
                "content": content,
                "size": st.st_size,
            }
        except OSError as e:
            return {"ok": False, "error": str(e), "path": str(p), "content": ""}

    def write_scratch(self, content: str) -> dict[str, Any]:
        """写入首页随手记，不改 last_file / 最近打开。"""
        p = Path(self.scratch_path())
        try:
            atomic_write_text(p, content if content is not None else "")
            st = p.stat()
            return {"ok": True, "path": str(p), "name": "scratch.md", "size": st.st_size}
        except OSError as e:
            return {"ok": False, "error": str(e)}

    def preview_file(self, path: str = "", max_chars: int = 1600) -> dict[str, Any]:
        """有界读取供首页速览，不改 last_file / 最近打开。"""
        raw = (path or "").strip()
        p = Path(raw)
        if not p.is_file():
            return {"ok": False, "error": "文件不存在"}
        limit = max(200, int(max_chars or 1600))
        try:
            with open(p, "rb") as f:
                data = f.read(limit * 4 + 16)
            text = data.decode("utf-8", errors="replace")
            st = p.stat()
        except OSError as e:
            return {"ok": False, "error": str(e)}
        preview = text[:limit]
        return {
            "ok": True,
            "path": str(p),
            "name": p.name,
            "preview": preview,
            "content": preview,
            "truncated": len(text) > limit,
            "size": st.st_size,
        }

    def invoke_mcp_tool(self, name: str, arguments: Optional[dict[str, Any]] = None) -> dict[str, Any]:
        """本应用内调用 MCP / AISkill 工具表中的一只工具。"""
        from . import adv

        return adv.invoke_tool(self, name, arguments or {})

    def _im_channel(self) -> Any:
        from . import im as im_mod

        if self._im_transport is not None:
            return self._im_transport
        raw = self.config.get("im") or {}
        hooks: dict[str, str] = {}
        if isinstance(raw, dict):
            for pid in im_mod.PROVIDERS:
                item = raw.get(pid) or {}
                if isinstance(item, dict):
                    hooks[pid] = str(item.get("webhook") or "")
        outbox = im_mod.FileOutboxTransport(self.config.data_dir / "im-outbox.json")
        return im_mod.ChannelTransport(hooks, outbox)

    def im_providers(self) -> dict[str, Any]:
        from . import im as im_mod

        return {"ok": True, "items": im_mod.provider_list()}

    def im_send(self, provider: str, markdown: str, title: str = "") -> dict[str, Any]:
        """把一篇 Markdown 经指定即时通讯通道发出。"""
        from . import im as im_mod

        return im_mod.send_markdown(provider, markdown, self._im_channel(), title=title)

    def im_receive(self, provider: str) -> dict[str, Any]:
        """从指定即时通讯通道取回最近一封 Markdown。"""
        from . import im as im_mod

        return im_mod.receive_markdown(provider, self._im_channel())

    def list_aiskills(self) -> dict[str, Any]:
        from . import adv

        return {"ok": True, "skills": adv.list_skills(), "tools": adv.list_tools()}

    def add_project_dialog(self) -> dict[str, Any]:
        """弹文件夹选择框，把所选目录添加为仓库。"""
        if not self._window:
            return {"ok": False, "error": "窗口未就绪"}
        result = self._window.create_file_dialog(webview.FOLDER_DIALOG)
        if not result:
            return {"ok": False, "cancelled": True}
        return self.projects.add(result[0])

    def remove_project(self, project_id: str) -> dict[str, Any]:
        return self.projects.remove(project_id)

    def rename_project(self, project_id: str, name: str) -> dict[str, Any]:
        return self.projects.rename(project_id, name)

    def pin_project(self, project_id: str, pinned: bool) -> dict[str, Any]:
        return self.projects.set_pinned(project_id, pinned)

    def open_project(self, project_id: str) -> dict[str, Any]:
        """在当前窗口打开仓库（返回文件夹树，前端切入编辑器视图）。"""
        proj = self.projects.get(project_id)
        if not proj:
            return {"ok": False, "error": "仓库不存在"}
        res = self.list_folder(proj["path"])
        if res.get("ok"):
            self.projects.touch(project_id)
        return res

    # ------------------------------------------------------------------
    # 学习仓库
    # ------------------------------------------------------------------
    def open_tutorial(self) -> dict[str, Any]:
        """内嵌学习仓库：把程序内置的 tutorial 复制到用户数据目录下「学习仓库」，
        注册（并置顶）为首页仓库，返回入口文档。

        幂等：只补缺失文件，不覆盖用户在学习仓库里的改动；重复调用/首启静默
        注册都安全。仓库被用户从首页移除后再次调用会重新注册（磁盘目录不动）。
        """
        base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[2]))
        src = base / "app" / "web" / "tutorial"
        if not src.is_dir():
            return {"ok": False, "error": "内置学习仓库资源缺失"}
        dst = self.config.data_dir / "学习仓库"
        try:
            copied = 0
            for f in src.rglob("*"):
                if not f.is_file():
                    continue
                target = dst / f.relative_to(src)
                if target.exists():
                    continue  # 保留用户改动，只补缺失
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(str(f), str(target))
                copied += 1
        except OSError as e:
            return {"ok": False, "error": str(e)}
        res = self.projects.add(str(dst), "学习仓库")
        if not res.get("ok"):
            return res
        project = res["project"]
        if not res.get("existed") and not project.get("pinned"):
            # 新注册时置顶，保证新用户在首页第一眼看到（对标 Notion 预置教程页）
            self.projects.set_pinned(project["id"], True)
            project["pinned"] = True
        return {
            "ok": True,
            "path": str(dst),
            "entry": str(dst / "欢迎使用 RyuuMD.md"),
            "copied": copied,
            "project": project,
        }

    # ------------------------------------------------------------------
    # 多窗口
    # ------------------------------------------------------------------
    def open_new_window(self, path: str = "") -> dict[str, Any]:
        """开新窗口；path 为空时新窗口显示首页。"""
        if not self._window_manager:
            return {"ok": False, "error": "当前环境不支持多窗口"}
        self._window_manager.create(path or "")
        return {"ok": True}

    def open_project_new_window(self, project_id: str) -> dict[str, Any]:
        proj = self.projects.get(project_id)
        if not proj:
            return {"ok": False, "error": "仓库不存在"}
        if not Path(proj["path"]).is_dir():
            return {"ok": False, "error": "仓库目录不存在（可能已移动或删除）"}
        res = self.open_new_window(proj["path"])
        if res.get("ok"):
            self.projects.touch(project_id)
        return res

    # ------------------------------------------------------------------
    # Markdown 默认应用（Windows 文件关联）
    # ------------------------------------------------------------------
    def get_md_assoc_status(self) -> dict[str, Any]:
        return file_assoc.status()

    def set_default_md_app(self) -> dict[str, Any]:
        return file_assoc.prompt_set_default(self.config)

    # ------------------------------------------------------------------
    # 系统集成
    # ------------------------------------------------------------------
    def reveal_in_explorer(self, path: str) -> dict[str, Any]:
        """在资源管理器中显示目录/文件（首页仓库卡片操作）。"""
        p = Path(path)
        if not p.exists():
            return {"ok": False, "error": "路径不存在"}
        try:
            if sys.platform == "win32":
                if p.is_dir():
                    os.startfile(str(p))  # noqa: S606
                else:
                    subprocess.Popen(["explorer", "/select,", str(p)])  # noqa: S603
            elif sys.platform == "darwin":
                subprocess.Popen(["open", "-R", str(p)])  # noqa: S603
            else:
                subprocess.Popen(["xdg-open", str(p.parent if p.is_file() else p)])  # noqa: S603
            return {"ok": True}
        except OSError as e:
            return {"ok": False, "error": str(e)}

    def _maybe_cloud_push(self, path: str) -> None:
        """保存成功后按需后台上传；未启用时 push_file 立即 skipped，线程极短。"""
        try:
            threading.Thread(
                target=lambda: self._cloud.push_file(path),
                daemon=True,
                name="ryuumd-cloud-push",
            ).start()
        except Exception:  # noqa: BLE001
            pass

    # ------------------------------------------------------------------
    # 云同步（可选，用户自备 WebDAV）
    # ------------------------------------------------------------------
    def save_cloud_settings(self, values: dict[str, Any]) -> dict[str, Any]:
        try:
            pub = save_cloud(self.config, values or {})
            return {"ok": True, "cloud_sync": pub}
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": str(e)}

    def test_cloud(self) -> dict[str, Any]:
        return self._cloud.test_connection()

    def sync_cloud(self, project_id: str = "") -> dict[str, Any]:
        return self._cloud.sync(project_id or "")

    def get_cloud_status(self) -> dict[str, Any]:
        return {"ok": True, **self._cloud.status()}

    def set_project_cloud(self, project_id: str, enabled: bool) -> dict[str, Any]:
        return self.projects.set_cloud_enabled(project_id, enabled)

    # ------------------------------------------------------------------
    # 仓库检索 / 双向链接 / 贴图 / 每日笔记 / 导出
    # ------------------------------------------------------------------
    def _as_existing_dir(self, raw: str) -> str:
        text = (raw or "").strip()
        if not text:
            return ""
        try:
            p = Path(text)
            if p.is_dir():
                return str(p)
        except OSError:
            return ""
        return ""

    def _vault_root(self, folder: str = "") -> str:
        """当前仓库根：入参目录优先，否则 last_folder。单文件父目录不当仓库。

        工作副本模式下映射到副本根，避免日记 / 收集箱 / 模板写穿源文件。
        """
        hit = self._as_existing_dir(folder)
        if not hit:
            hit = self._as_existing_dir(self.config.get("last_folder", "") or "")
        if not hit:
            return ""
        if self._workdir_on():
            io_path, _logical, sess = self._resolve_io(hit, create=True)
            if sess and io_path:
                return io_path
        return hit

    def _vault_for_path(self, path: str) -> str:
        """已注册仓库或 last_folder 若包含该路径，返回仓库根。"""
        if not (path or "").strip():
            return ""
        proj = self.projects.find_by_containing_path(path)
        if proj:
            root = self._as_existing_dir(str(proj.get("path") or ""))
            if root:
                return root
        last = self._as_existing_dir(self.config.get("last_folder", "") or "")
        if not last:
            return ""
        try:
            Path(path).resolve().relative_to(Path(last).resolve())
            return last
        except (OSError, ValueError):
            return ""

    def _search_root(self, folder: str = "") -> str:
        """搜索根：目录 → 文件所属仓库 → last_folder → last_file 父目录。

        工作副本下只映射已有会话，不为搜索创建整库副本（否则会收编并拆掉单文件会话）。
        """
        raw = (folder or "").strip()
        if raw:
            try:
                p = Path(raw)
                if p.is_dir():
                    mapped = self._workdir_mapped(str(p))
                    if mapped:
                        return mapped
                    scoped = self._search_root_from_file_session(str(p))
                    if scoped:
                        return scoped
                    return str(p)
                if p.is_file():
                    vault = self._vault_for_path(str(p)) or str(p.parent)
                    mapped = self._workdir_mapped(vault) if vault else ""
                    if mapped:
                        return mapped
                    scoped = self._search_root_from_file_session(vault or str(p.parent))
                    if scoped:
                        return scoped
                    mapped_file = self._workdir_mapped(str(p))
                    if mapped_file:
                        wp = Path(mapped_file)
                        return str(wp.parent if wp.is_file() else wp)
                    return vault
            except OSError:
                pass
        last = self._as_existing_dir(self.config.get("last_folder", "") or "")
        if last:
            mapped = self._workdir_mapped(last)
            if mapped:
                return mapped
            scoped = self._search_root_from_file_session(last)
            if scoped:
                return scoped
            return last
        last_file = (self.config.get("last_file", "") or "").strip()
        if not last_file:
            return last if last else ""
        try:
            fp = Path(last_file)
            hint = str(fp if fp.exists() else fp.parent)
            vault = self._vault_for_path(hint)
            if vault:
                mapped = self._workdir_mapped(vault)
                if mapped:
                    return mapped
                scoped = self._search_root_from_file_session(vault)
                if scoped:
                    return scoped
                return vault
            if fp.parent.is_dir():
                parent = str(fp.parent)
                mapped = self._workdir_mapped(str(fp))
                if mapped:
                    wp = Path(mapped)
                    return str(wp.parent if wp.is_file() else wp)
                return parent
        except OSError:
            return last if last else ""
        return last if last else ""

    def _md_in_vault(self, folder: str, path: str) -> Path | None:
        """path 必须是仓库内已存在的 markdown，防止模板接口读出仓库外文件。"""
        root = self._vault_root(folder)
        if not root or not path:
            return None
        try:
            p = Path(path).resolve()
            p.relative_to(Path(root).resolve())
        except (OSError, ValueError):
            return None
        if not self._is_md_file(p):
            return None
        return p

    def list_templates(self, folder: str = "") -> dict[str, Any]:
        """列出仓库 `模板/` 与 `templates/` 下一层的 md（不递归）。"""
        root = self._vault_root(folder)
        if not root:
            return {"ok": False, "error": "请先打开仓库或文件夹", "items": []}
        items: list[dict[str, str]] = []
        seen: set[str] = set()
        for dirname in ("模板", "templates"):
            d = Path(root) / dirname
            if not d.is_dir():
                continue
            try:
                files = sorted(d.iterdir(), key=lambda x: x.name.lower())
            except OSError:
                continue
            for p in files:
                if not p.is_file() or p.suffix.lower() not in MD_EXTS:
                    continue
                key = os.path.normcase(str(p))
                if key in seen:
                    continue
                seen.add(key)
                items.append({
                    "path": str(p),
                    "name": p.stem,
                    "rel": f"{dirname}/{p.name}",
                })
        return {"ok": True, "items": items, "dir": str(Path(root) / "模板")}

    def render_template(self, folder: str = "", path: str = "", title: str = "") -> dict[str, Any]:
        p = self._md_in_vault(folder, path)
        if p is None:
            return {"ok": False, "error": "模板不存在或不在当前仓库"}
        try:
            raw = p.read_text(encoding="utf-8", errors="replace")
        except OSError as e:
            return {"ok": False, "error": str(e)}
        name = (title or p.stem).strip()
        return {"ok": True, "content": apply_template_vars(raw, name), "name": p.stem}

    def new_from_template(
        self, folder: str = "", template_path: str = "", name: str = ""
    ) -> dict[str, Any]:
        rendered = self.render_template(folder, template_path, name)
        if not rendered.get("ok"):
            return rendered
        created = self.new_file(folder, name or rendered.get("name") or "未命名")
        if not created.get("ok"):
            return created
        try:
            Path(created["path"]).write_text(rendered["content"], encoding="utf-8")
        except OSError as e:
            return {"ok": False, "error": str(e)}
        return self.read_file(created["path"])

    def ensure_templates_dir(self, folder: str = "") -> dict[str, Any]:
        """确保仓库下有 `模板/` 目录（不自动写样例文件）。"""
        root = self._vault_root(folder)
        if not root:
            return {"ok": False, "error": "请先打开仓库或文件夹"}
        d = Path(root) / "模板"
        try:
            d.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            return {"ok": False, "error": str(e)}
        return {"ok": True, "path": str(d)}

    def list_md_files(self, folder: str = "", query: str = "") -> dict[str, Any]:
        root = self._vault_root(folder)
        if not root:
            return {"ok": False, "error": "请先打开仓库或文件夹", "items": []}
        return vault_search.list_md_files(root, query)

    def search_vault(self, folder: str = "", query: str = "") -> dict[str, Any]:
        root = self._vault_root(folder)
        if not root:
            return {"ok": False, "error": "请先打开仓库或文件夹", "hits": []}
        return vault_search.search_vault(root, query)

    def _ai_cfg(self) -> dict[str, Any]:
        return ai_client.normalize_ai(self.config.get("ai"))

    def test_ai(self) -> dict[str, Any]:
        """向已配置的 Messages 端点发送最小请求，验证地址、密钥与模型。"""
        return ai_client.complete(
            self._ai_cfg(), "Reply with OK.", max_tokens=1, timeout=20
        )

    def search_notes(self, folder: str = "", query: str = "", mode: str = "title") -> dict[str, Any]:
        """标题 / 内容 / 语义检索；查询以 `ask ` 为前缀时走 AI 问答。"""
        is_ask, _rest = ai_client.parse_ask_prefix(query)
        root = self._search_root(folder)
        if not is_ask and not root:
            return {"ok": False, "error": "请先打开仓库或文件夹", "hits": []}
        return ai_client.run_search(root, query, mode, self._ai_cfg())

    def _knowledge_key(self, root: str) -> str:
        try:
            return os.path.normcase(str(Path(root).resolve()))
        except OSError:
            return os.path.normcase(str(root or ""))

    def _invalidate_knowledge(self, path_or_folder: str = "") -> None:
        """笔记写盘后丢掉所属仓库的谱系缓存。"""
        raw = (path_or_folder or "").strip()
        if not raw:
            with _KNOWLEDGE_CACHE_LOCK:
                self._knowledge_cache.clear()
            return
        targets: set[str] = set()

        def _add(path: str) -> None:
            text = (path or "").strip()
            if not text:
                return
            try:
                targets.add(os.path.normcase(str(Path(text).resolve())))
            except OSError:
                targets.add(os.path.normcase(text))

        _add(raw)
        if self._workdir_on():
            io_path, logical, sess = self._resolve_io(raw, create=False)
            _add(io_path)
            _add(logical)
            if sess:
                _add(str(sess.get("work_root") or ""))
                _add(str(sess.get("source_root") or ""))
        with _KNOWLEDGE_CACHE_LOCK:
            drop: list[str] = []
            for key in self._knowledge_cache:
                root = os.path.normcase(str(key))
                for tgt in targets:
                    if tgt == root or tgt.startswith(root + os.sep) or root.startswith(tgt + os.sep):
                        drop.append(key)
                        break
            for key in drop:
                self._knowledge_cache.pop(key, None)

    def summarize_document(self, path: str = "", content: str = "", folder: str = "") -> dict[str, Any]:
        """概括当前文档：优先用传入正文（含未保存编辑），读盘时受仓库边界约束。"""
        title = Path(path).name if path else "未命名"
        body = content if content is not None else ""
        if not str(body).strip() and path:
            vault = self._vault_root(folder)
            if vault:
                p = self._md_in_vault(folder, path)
                if p is None:
                    return {"ok": False, "error": "只能概括当前仓库内的 Markdown", "text": ""}
            else:
                p = Path(path)
                if not self._is_md_file(p):
                    return {"ok": False, "error": "文件不存在", "text": ""}
            try:
                body = p.read_text(encoding="utf-8", errors="replace")
            except OSError as e:
                return {"ok": False, "error": str(e), "text": ""}
            title = p.name
        if not str(body).strip():
            return {"ok": False, "error": "请先打开一篇文档", "text": ""}
        return ai_client.summarize_document(title, body, self._ai_cfg())

    def summarize_vault(self, folder: str = "") -> dict[str, Any]:
        """概括当前仓库内的笔记材料。"""
        root = self._vault_root(folder)
        if not root:
            return {"ok": False, "error": "请先打开仓库或文件夹", "text": ""}
        return ai_client.summarize_vault(root, self._ai_cfg())

    def ask_ai(self, folder: str = "", question: str = "") -> dict[str, Any]:
        """AI 侧栏提问：附当前仓库摘录；模型若请求工具则走本应用 MCP / AISkill 表。"""
        from . import adv

        return adv.ask_with_tools(
            question,
            self._vault_root(folder),
            self._ai_cfg(),
            dispatch=lambda n, a: adv.invoke_tool(self, n, a),
        )

    def knowledge_tree(self, folder: str = "", refresh: bool = False) -> dict[str, Any]:
        """生成仓库知识谱系；结果按仓库缓存，refresh=True 重新生成。"""
        root = self._vault_root(folder)
        if not root:
            return {"ok": False, "error": "请先打开仓库或文件夹", "text": ""}
        key = self._knowledge_key(root)
        if not refresh:
            with _KNOWLEDGE_CACHE_LOCK:
                cached = self._knowledge_cache.get(key)
                if cached:
                    result = dict(cached)
                    result["cached"] = True
                    return result
        res = ai_client.knowledge_tree(root, self._ai_cfg())
        if res.get("ok"):
            with _KNOWLEDGE_CACHE_LOCK:
                self._knowledge_cache[key] = dict(res)
        res["cached"] = False
        return res

    def resolve_wikilink(
        self, folder: str = "", name: str = "", current_file: str = ""
    ) -> dict[str, Any]:
        root = self._vault_root(folder)
        return vault_search.resolve_wikilink(root, name, current_file)

    def find_backlinks(self, folder: str = "", path: str = "") -> dict[str, Any]:
        root = self._vault_root(folder)
        if not root:
            return {"ok": True, "hits": []}
        return vault_search.find_backlinks(root, path or "")

    def file_stat(self, path: str) -> dict[str, Any]:
        try:
            io_path, logical, _sess = self._resolve_io(path, create=False)
            p = Path(io_path)
            if not p.is_file():
                return {"ok": False, "exists": False, "source_path": logical}
            st = p.stat()
            return {
                "ok": True,
                "exists": True,
                "mtime": st.st_mtime,
                "size": st.st_size,
                "path": str(p),
                "source_path": logical,
            }
        except OSError as e:
            return {"ok": False, "exists": False, "error": str(e)}

    def vault_index(
        self,
        folder: str = "",
        kind: str = "tasks",
        query: str = "",
        path: str = "",
    ) -> dict[str, Any]:
        root = self._vault_root(folder)
        if not root:
            return {"ok": False, "error": "请先打开仓库或文件夹", "items": []}
        k = (kind or "tasks").strip().lower()
        if k == "tasks":
            return vault_search.list_tasks(root, query)
        if k == "tags":
            return vault_search.list_tags(root, query)
        if k in ("broken", "broken_links"):
            return vault_search.list_broken_wikilinks(root, query)
        if k in ("orphans", "orphan"):
            return vault_search.list_orphans(root, query)
        if k in ("mentions", "unlinked"):
            return vault_search.list_unlinked_mentions(root, path or "", query)
        return {"ok": False, "error": "未知索引类型", "items": []}

    def vault_stats(self, folder: str = "") -> dict[str, Any]:
        root = self._vault_root(folder)
        if not root:
            return {"ok": False, "error": "请先打开仓库或文件夹"}
        return vault_search.vault_stats(root)

    def append_capture(
        self,
        folder: str = "",
        text: str = "",
        name: str = "收集箱.md",
    ) -> dict[str, Any]:
        """把一段文字追加到仓库根的收集箱（默认「收集箱.md」）。"""
        root = self._vault_root(folder)
        if not root:
            return {"ok": False, "error": "请先打开仓库或文件夹"}
        body = (text or "").strip()
        if not body:
            return {"ok": False, "error": "收集内容不能为空"}
        if len(body) > 8000:
            return {"ok": False, "error": "内容过长"}
        raw = (name or "收集箱.md").strip() or "收集箱.md"
        if any(sep in raw for sep in ("/", "\\", ":")) or raw in (".", ".."):
            return {"ok": False, "error": "文件名不合法"}
        if any(ord(c) < 32 for c in raw):
            return {"ok": False, "error": "文件名不合法"}
        if not raw.lower().endswith(tuple(MD_EXTS)):
            raw += ".md"
        dest = Path(root) / raw
        try:
            dest.resolve().relative_to(Path(root).resolve())
        except (OSError, ValueError):
            return {"ok": False, "error": "文件名不合法"}
        if dest.exists() and not dest.is_file():
            return {"ok": False, "error": "目标不是文件"}
        stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
        block = f"\n## {stamp}\n\n{body}\n"
        try:
            if dest.is_file():
                existing = dest.read_text(encoding="utf-8", errors="replace")
                atomic_write_text(dest, existing.rstrip() + "\n" + block)
            else:
                atomic_write_text(dest, "# " + dest.stem + "\n" + block)
            self._invalidate_knowledge(str(dest))
            if not self._workdir_on():
                self._maybe_cloud_push(str(dest))
            st = dest.stat()
            return {
                "ok": True,
                "path": str(dest),
                "name": dest.name,
                "mtime": st.st_mtime,
            }
        except OSError as e:
            return {"ok": False, "error": str(e)}

    def save_image(
        self,
        md_path: str = "",
        folder: str = "",
        filename: str = "",
        data_b64: str = "",
        mime: str = "",
    ) -> dict[str, Any]:
        """把粘贴/拖入的图片落到 Typora 式 `{文件名}.assets/`（未保存文档则用仓库 `assets/`）。"""
        try:
            raw = _decode_b64(data_b64)
        except Exception:
            return {"ok": False, "error": "图片数据无效"}
        if not raw:
            return {"ok": False, "error": "图片为空"}
        if len(raw) > 12 * 1024 * 1024:
            return {"ok": False, "error": "图片超过 12MB"}
        ext = _image_ext(filename, mime)
        io_md, _logical_md, _sess = self._resolve_io(md_path, create=False) if md_path else ("", "", None)
        md = Path(io_md) if io_md else None
        if md and md.is_file():
            dest_dir = md.parent / f"{md.stem}.assets"
            base = md.parent
        elif folder:
            io_folder, _logical_folder, _fsess = self._resolve_io(folder, create=True)
            if io_folder and Path(io_folder).is_dir():
                dest_dir = Path(io_folder) / "assets"
                base = Path(io_folder)
            else:
                last = self._vault_root("")
                if last:
                    dest_dir = Path(last) / "assets"
                    base = Path(last)
                else:
                    return {"ok": False, "error": "请先保存文档或打开仓库，再插入图片"}
        else:
            last = self._vault_root("")
            if last:
                dest_dir = Path(last) / "assets"
                base = Path(last)
            else:
                return {"ok": False, "error": "请先保存文档或打开仓库，再插入图片"}
        try:
            dest_dir.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            name = f"pasted-{stamp}{ext}"
            dest = dest_dir / name
            n = 1
            while dest.exists():
                name = f"pasted-{stamp}-{n}{ext}"
                dest = dest_dir / name
                n += 1
            dest.write_bytes(raw)
            rel = os.path.relpath(str(dest), str(base)).replace("\\", "/")
            return {"ok": True, "path": str(dest), "rel": rel, "name": dest.stem}
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": str(e)}

    def open_daily_note(self, folder: str = "", subfolder: str = "") -> dict[str, Any]:
        """打开（必要时创建）今日日记：`{仓库}/{日记目录}/YYYY-MM-DD.md`。"""
        root = self._vault_root(folder)
        if not root:
            return {"ok": False, "error": "请先打开仓库或文件夹"}
        sub = (subfolder or self.config.get("daily_note_folder") or "日记").strip() or "日记"
        sub_path = Path(sub)
        if sub_path.is_absolute() or ".." in sub_path.parts:
            return {"ok": False, "error": "日记目录不合法"}
        now = datetime.now()
        week = "一二三四五六日"[now.weekday()]
        date = now.strftime("%Y-%m-%d")
        target = Path(root) / sub_path / f"{date}.md"
        try:
            if not target.exists():
                target.parent.mkdir(parents=True, exist_ok=True)
                body = f"# {date} 周{week}\n\n"
                for rel in ("模板/日记.md", "templates/daily.md", "模板/daily.md"):
                    tpl = Path(root) / rel
                    if tpl.is_file():
                        try:
                            raw = tpl.read_text(encoding="utf-8", errors="replace")
                        except OSError:
                            raw = ""
                        if raw:
                            body = apply_template_vars(raw, title=date, now=now)
                        break
                target.write_text(body, encoding="utf-8")
                self._invalidate_knowledge(str(target))
            return self.read_file(str(target))
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": str(e)}

    def save_html_dialog(self, html: str, suggested: str = "导出.html") -> dict[str, Any]:
        if not self._window:
            return {"ok": False, "error": "窗口未就绪"}
        name = suggested or "导出.html"
        if not name.lower().endswith((".html", ".htm")):
            name += ".html"
        result = self._window.create_file_dialog(
            webview.SAVE_DIALOG,
            save_filename=name,
            file_types=("HTML (*.html)", "All files (*.*)"),
        )
        if not result:
            return {"ok": False, "cancelled": True}
        path = result if isinstance(result, str) else result[0]
        title = Path(path).stem
        doc = wrap_html_export(title, html or "", self.config.all())
        try:
            Path(path).write_text(doc, encoding="utf-8")
            return {"ok": True, "path": str(path)}
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": str(e)}

    def open_external(self, url: str) -> dict[str, Any]:
        """用系统默认浏览器打开 http(s)/mailto，避免 WebView2 把应用整页导航走。"""
        u = (url or "").strip()
        if not u.startswith(("http://", "https://", "mailto:")):
            return {"ok": False, "error": "不支持的链接"}
        try:
            if sys.platform == "win32":
                os.startfile(u)  # noqa: S606
            elif sys.platform == "darwin":
                subprocess.Popen(["open", u])  # noqa: S603
            else:
                subprocess.Popen(["xdg-open", u])  # noqa: S603
            return {"ok": True}
        except OSError as e:
            return {"ok": False, "error": str(e)}


def _decode_b64(data: str) -> bytes:
    s = (data or "").strip()
    if not s:
        return b""
    if "," in s and s[:5].lower() == "data:":
        s = s.split(",", 1)[1]
    return base64.b64decode(s)


def _image_ext(filename: str, mime: str) -> str:
    ext = Path(filename or "").suffix.lower()
    if ext in IMAGE_EXTS:
        return ext
    mime_map = {
        "image/png": ".png",
        "image/jpeg": ".jpg",
        "image/jpg": ".jpg",
        "image/gif": ".gif",
        "image/webp": ".webp",
        "image/bmp": ".bmp",
        "image/svg+xml": ".svg",
    }
    key = (mime or "").split(";", 1)[0].strip().lower()
    return mime_map.get(key, ".png")


def apply_template_vars(text: str, title: str = "", now: datetime | None = None) -> str:
    """替换模板占位符：{{title}} {{date}} {{time}} {{week}} {{year}} {{month}} {{day}}。"""
    stamp = now or datetime.now()
    week = "一二三四五六日"[stamp.weekday()]
    mapping = {
        "{{title}}": title or "",
        "{{date}}": stamp.strftime("%Y-%m-%d"),
        "{{time}}": stamp.strftime("%H:%M"),
        "{{week}}": week,
        "{{year}}": stamp.strftime("%Y"),
        "{{month}}": stamp.strftime("%m"),
        "{{day}}": stamp.strftime("%d"),
    }
    out = text or ""
    for key, val in mapping.items():
        out = out.replace(key, val)
    return out


# 旧色板 id → 当前板（一次性迁移）
_LEGACY_PALETTE_MAP = {
    "cherry": "a6", "vampire": "a6", "caramel": "a6", "a2": "a6", "sakura": "a6", "mauve": "a3",
    "mint": "a5", "abyss": "a5", "forest": "a4", "radiation": "a4",
    "sky": "a1", "prussian": "a1",
}


def _export_palette(config: Optional[dict[str, Any]]) -> dict[str, str]:
    """按当前 palette + theme 从运行时 palettes.css 取导出配色；旧 id 经映射表迁移。"""
    from .palette_css import load_export_palettes

    tables = load_export_palettes()
    cfg = config or {}
    pal = str(cfg.get("palette") or "")
    if pal not in tables:
        legacy = pal or str(
            cfg.get("palette_dark") if cfg.get("theme") == "dark" else cfg.get("palette_light") or ""
        )
        pal = _LEGACY_PALETTE_MAP.get(legacy, "a1")
    if pal not in tables:
        pal = "a1"
    theme = "dark" if cfg.get("theme") == "dark" else "light"
    row = (tables.get(pal) or {}).get(theme) or (tables.get("a1") or {}).get("light") or {}
    return {
        "primary": row.get("primary", ""),
        "deep": row.get("deep", ""),
        "bg": row.get("bg", ""),
        "text": row.get("text", ""),
        "muted": row.get("muted", ""),
        "border": row.get("border", ""),
    }


def _safe_css_font(name: str, fallback: str) -> str:
    """把用户字体名拼进 CSS font-family，去掉可注入字符。"""
    raw = (name or "").strip().replace("<", "").replace(">", "").replace("{", "").replace("}", "")
    raw = raw.replace("\n", " ").replace('"', "'")
    if not raw:
        return fallback
    if "," in raw:
        return f"{raw},{fallback}"
    return f"'{raw}',{fallback}"


_UI_FONT_FALLBACK = "'LXGW WenKai','Segoe UI','Microsoft YaHei',sans-serif"
_MONO_FONT_FALLBACK = "'Cascadia Code',Consolas,monospace"


def wrap_html_export(title: str, body: str, config: Optional[dict[str, Any]] = None) -> str:
    """把 Vditor HTML 片段包成可独立打开的文档（相对图片路径保持原样）。

    配色/字体跟随当前 palette + theme + font_*；未传 config 时回退 a1 亮色 + 霞鹜文楷。
    """
    safe_title = html_lib.escape(title or "导出")
    cfg = config or {}
    pal = _export_palette(cfg)
    ui = _safe_css_font(str(cfg.get("font_ui") or ""), _UI_FONT_FALLBACK)
    mono = _safe_css_font(str(cfg.get("font_mono") or ""), _MONO_FONT_FALLBACK)
    return (
        "<!DOCTYPE html>\n<html lang=\"zh-CN\">\n<head>\n"
        "<meta charset=\"UTF-8\" />\n"
        f"<title>{safe_title}</title>\n"
        "<style>\n"
        "body{max-width:800px;margin:2.2em auto;padding:0 1.2em 3em;"
        f"font-family:{ui};"
        f"line-height:1.75;color:{pal['text']};background:{pal['bg']};}}\n"
        f"img{{max-width:100%;}} pre,code{{font-family:{mono};}}\n"
        f"blockquote{{border-left:4px solid {pal['primary']};margin:0;padding:.2em 1em;color:{pal['muted']};}}\n"
        f"table{{border-collapse:collapse;}} th,td{{border:1px solid {pal['border']};padding:.4em .7em;}}\n"
        f"a{{color:{pal['deep']};}}\n"
        "</style>\n</head>\n<body>\n"
        f"{body}\n"
        "</body>\n</html>\n"
    )
