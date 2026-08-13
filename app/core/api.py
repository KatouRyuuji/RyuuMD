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

import webview

from . import file_assoc
from .cloud_sync import get_engine, public_cloud, save_cloud
from .config import Config
from .fsutil import IMAGE_EXTS, IGNORE_DIRS, MD_EXTS, recycle_file, skip_dir_name  # noqa: F401
from .projects import ProjectStore
from . import search as vault_search

# 文件夹树扫描上限：层级与总条目数，超出即截断并在返回数据中标注
MAX_TREE_DEPTH = 8
MAX_TREE_ENTRIES = 2000
# 最近打开列表上限（最新在前，去重）
RECENT_MAX = 15


class Api:
    def __init__(
        self,
        config: Config,
        window_manager: Any = None,
        initial_path: str = "",
    ) -> None:
        self.config = config
        self.projects = ProjectStore(config)
        self._cloud = get_engine(config, self.projects)
        self._window: Optional["webview.Window"] = None
        # 多窗口管理器（main.WindowManager）；测试/单窗口环境可为 None
        self._window_manager = window_manager
        # 本窗口的初始打开路径（命令行 / 单实例转发 / 新窗口传入），前端启动时拉取
        self._initial_path = initial_path

    def bind_window(self, window: "webview.Window") -> None:
        self._window = window

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
        return data

    def set_config(self, key: str, value: Any) -> dict[str, Any]:
        self.config.set(key, value)
        return {"ok": True}

    def update_config(self, values: dict[str, Any]) -> dict[str, Any]:
        self.config.update(values or {})
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
            out.append(
                {
                    "path": raw,
                    "name": p.name or raw,
                    "kind": it.get("kind", "file"),
                    # 失效（已移动/删除）项保留展示但前端灰显，点击打开失败时自动移除
                    "exists": p.exists(),
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
    def read_file(self, path: str) -> dict[str, Any]:
        try:
            p = Path(path)
            if not p.exists() or not p.is_file():
                return {"ok": False, "error": "文件不存在"}
            content = p.read_text(encoding="utf-8")
            self.config.set("last_file", str(p))
            self._add_recent(str(p), "file")
            st = p.stat()
            return {
                "ok": True,
                "path": str(p),
                "name": p.name,
                "content": content,
                "size": st.st_size,
                "mtime": st.st_mtime,
            }
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": str(e)}

    def save_file(self, path: str, content: str) -> dict[str, Any]:
        try:
            p = Path(path)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content, encoding="utf-8")
            self.config.set("last_file", str(p))
            self._add_recent(str(p), "file")
            self._maybe_cloud_push(str(p))
            st = p.stat()
            return {"ok": True, "path": str(p), "mtime": st.st_mtime, "size": st.st_size}
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": str(e)}

    def new_file(self, folder: str, name: str) -> dict[str, Any]:
        try:
            if not name.lower().endswith(tuple(MD_EXTS)):
                name += ".md"
            if any(sep in name for sep in ("/", "\\", ":")):
                return {"ok": False, "error": "名称不能包含路径分隔符"}
            target = Path(folder) / name if folder else Path(name)
            if target.exists():
                return {"ok": False, "error": "同名文件已存在"}
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("", encoding="utf-8")
            self._add_recent(str(target), "file")
            return {"ok": True, "path": str(target), "name": target.name}
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": str(e)}

    def new_folder(self, parent: str, name: str) -> dict[str, Any]:
        """在已打开的仓库目录下新建子文件夹。"""
        try:
            folder = Path(parent)
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
            return {"ok": True, "path": str(target), "name": target.name}
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

    def rename_file(self, path: str, new_name: str) -> dict[str, Any]:
        """重命名真实 md 文件（仅同目录改名，不接受带路径的名字）。"""
        try:
            p = Path(path)
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
            if os.path.normcase(str(target)) == os.path.normcase(str(p)):
                return {"ok": True, "path": str(p), "name": p.name}
            if target.exists():
                return {"ok": False, "error": "同名文件已存在"}
            os.rename(p, target)
            self._sync_path_refs(str(p), str(target))
            return {"ok": True, "path": str(target), "name": target.name}
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": str(e)}

    def move_file(self, path: str, target_folder: str) -> dict[str, Any]:
        """把真实 md 文件移动到目标目录（shutil.move 跨盘安全）。"""
        try:
            p = Path(path)
            if not self._is_md_file(p):
                return {"ok": False, "error": "不是已存在的 Markdown 文件"}
            folder = Path(target_folder)
            if not folder.is_dir():
                return {"ok": False, "error": "目标文件夹不存在"}
            same = os.path.normcase(os.path.normpath(str(folder)))
            if same == os.path.normcase(os.path.normpath(str(p.parent))):
                return {"ok": False, "error": "文件已在该文件夹中"}
            target = folder / p.name
            if target.exists():
                return {"ok": False, "error": "目标位置已存在同名文件"}
            shutil.move(str(p), str(target))
            self._sync_path_refs(str(p), str(target))
            return {"ok": True, "path": str(target)}
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
            p = Path(path)
            if not self._is_md_file(p):
                return {"ok": False, "error": "不是已存在的 Markdown 文件"}
            recycle_file(str(p))
            self.remove_recent(str(p))
            if self.config.get("last_file", "") == str(p):
                self.config.set("last_file", "")
            return {"ok": True}
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": str(e)}

    def duplicate_file(self, path: str) -> dict[str, Any]:
        """在同目录复制一份 md（`名 副本.md` / `名 副本 2.md`）。"""
        try:
            p = Path(path)
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
            self._add_recent(str(dest), "file")
            return {"ok": True, "path": str(dest), "name": dest.name}
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": str(e)}

    # ------------------------------------------------------------------
    # 文件夹树
    # ------------------------------------------------------------------
    def list_folder(self, path: str) -> dict[str, Any]:
        try:
            root = Path(path)
            if not root.exists() or not root.is_dir():
                return {"ok": False, "error": "文件夹不存在"}
            self.config.set("last_folder", str(root))
            self._add_recent(str(root), "folder")
            # 条目预算贯穿整棵树；truncated 让前端明确提示「已截断」
            budget: dict[str, Any] = {"left": MAX_TREE_ENTRIES, "truncated": False}
            tree = self._scan_dir(root, 0, budget)
            return {
                "ok": True,
                "root": str(root),
                "name": root.name,
                "tree": tree,
                "truncated": budget["truncated"],
            }
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
    def _vault_root(self, folder: str = "") -> str:
        """当前仓库根：入参优先，否则 last_folder。"""
        if folder and Path(folder).is_dir():
            return str(Path(folder))
        last = self.config.get("last_folder", "") or ""
        if last and Path(last).is_dir():
            return last
        return ""

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
            p = Path(path)
            if not p.is_file():
                return {"ok": False, "exists": False}
            st = p.stat()
            return {"ok": True, "exists": True, "mtime": st.st_mtime, "size": st.st_size, "path": str(p)}
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
                dest.write_text(existing.rstrip() + "\n" + block, encoding="utf-8")
            else:
                dest.write_text("# " + dest.stem + "\n" + block, encoding="utf-8")
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
        md = Path(md_path) if md_path else None
        if md and md.is_file():
            dest_dir = md.parent / f"{md.stem}.assets"
            base = md.parent
        elif folder and Path(folder).is_dir():
            dest_dir = Path(folder) / "assets"
            base = Path(folder)
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
        doc = wrap_html_export(title, html or "")
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


def wrap_html_export(title: str, body: str) -> str:
    """把 Vditor HTML 片段包成可独立打开的文档（相对图片路径保持原样）。"""
    safe_title = html_lib.escape(title or "导出")
    return (
        "<!DOCTYPE html>\n<html lang=\"zh-CN\">\n<head>\n"
        "<meta charset=\"UTF-8\" />\n"
        f"<title>{safe_title}</title>\n"
        "<style>\n"
        "body{max-width:800px;margin:2.2em auto;padding:0 1.2em 3em;"
        "font-family:'LXGW WenKai','Segoe UI','Microsoft YaHei',sans-serif;"
        "line-height:1.75;color:#2c3e50;}\n"
        "img{max-width:100%;} pre,code{font-family:Consolas,monospace;}\n"
        "blockquote{border-left:4px solid #3498db;margin:0;padding:.2em 1em;color:#5b7187;}\n"
        "table{border-collapse:collapse;} th,td{border:1px solid #e7eef6;padding:.4em .7em;}\n"
        "</style>\n</head>\n<body>\n"
        f"{body}\n"
        "</body>\n</html>\n"
    )

