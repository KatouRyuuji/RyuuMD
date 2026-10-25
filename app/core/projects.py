"""仓库（项目）管理：把笔记目录注册为仓库（参考 Obsidian vault），持久化在 config["projects"]。"""

from __future__ import annotations

import os
import sys
import time
import uuid
from pathlib import Path
from typing import Any, Optional

from .config import Config
from .fsutil import count_md_files


def _norm(path: str) -> str:
    """macOS 的 normcase 是空操作，且 /var 与 /private/var 是同一目录，需 resolve + casefold。"""
    p = os.path.normpath(str(path))
    if sys.platform == "darwin":
        try:
            p = str(Path(p).resolve())
        except OSError:
            pass
        return p.casefold()
    return os.path.normcase(p)


class ProjectStore:

    # 与 Config 相同：只给 Python 用，不要被 pywebview 扫成 JS API。
    _serializable = False

    def __init__(self, config: Config) -> None:
        self.config = config

    def _all(self) -> list[dict[str, Any]]:
        items = self.config.get("projects", []) or []
        return [it for it in items if isinstance(it, dict) and it.get("path") and it.get("id")]

    def _save(self, items: list[dict[str, Any]]) -> None:
        self.config.set("projects", items)

    def list(self, with_stats: bool = True) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for it in self._all():
            info = dict(it)
            p = Path(it["path"])
            info["exists"] = p.is_dir()
            info["md_count"] = None
            info["md_count_capped"] = False
            if with_stats and info["exists"]:
                n, capped = count_md_files(str(p))
                info["md_count"] = n
                info["md_count_capped"] = capped
            out.append(info)
        out.sort(key=lambda x: (not x.get("pinned"), -(x.get("last_opened_at") or 0), x.get("name", "")))
        return out

    def get(self, project_id: str) -> Optional[dict[str, Any]]:
        for it in self._all():
            if it["id"] == project_id:
                return it
        return None

    def find_by_path(self, path: str) -> Optional[dict[str, Any]]:
        target = _norm(path)
        for it in self._all():
            if _norm(it["path"]) == target:
                return it
        return None

    def find_by_containing_path(self, path: str) -> Optional[dict[str, Any]]:
        """最长前缀匹配，避免父子目录误匹配。"""
        target = _norm(path)
        best: Optional[dict[str, Any]] = None
        best_len = -1
        sep = os.sep
        for it in self._all():
            root = _norm(it["path"])
            if target == root or target.startswith(root + sep):
                if len(root) > best_len:
                    best, best_len = it, len(root)
        return best

    def add(self, path: str, name: str = "") -> dict[str, Any]:
        p = Path(path)
        if not p.is_dir():
            return {"ok": False, "error": "文件夹不存在"}
        existed = self.find_by_path(str(p))
        if existed:
            return {"ok": True, "project": existed, "existed": True}
        project = {
            "id": uuid.uuid4().hex[:12],
            # 名字留白（含全空白）时回落到目录名，避免空名仓库
            "name": (name or "").strip() or p.name or str(p),
            "path": str(p),
            "pinned": False,
            "cloud_enabled": False,
            "created_at": int(time.time()),
            "last_opened_at": 0,
        }
        items = self._all()
        items.append(project)
        self._save(items)
        return {"ok": True, "project": project, "existed": False}

    def create(self, parent: str, name: str) -> dict[str, Any]:
        parent_p = Path(parent)
        if not parent_p.is_dir():
            return {"ok": False, "error": "父目录不存在"}
        raw = (name or "").strip()
        if not raw:
            return {"ok": False, "error": "名称不能为空"}
        if any(sep in raw for sep in ("/", "\\", ":")) or raw in (".", ".."):
            return {"ok": False, "error": "名称不能包含路径分隔符"}
        dest = parent_p / raw
        if dest.exists() and not dest.is_dir():
            return {"ok": False, "error": "同名文件已存在"}
        try:
            dest.mkdir(parents=False, exist_ok=True)
        except OSError as e:
            return {"ok": False, "error": str(e)}
        return self.add(str(dest), raw)

    def remove(self, project_id: str) -> dict[str, Any]:
        items = [it for it in self._all() if it["id"] != project_id]
        self._save(items)
        return {"ok": True}

    def rename(self, project_id: str, name: str) -> dict[str, Any]:
        name = (name or "").strip()
        if not name:
            return {"ok": False, "error": "名称不能为空"}
        items = self._all()
        for it in items:
            if it["id"] == project_id:
                it["name"] = name
                self._save(items)
                return {"ok": True, "project": it}
        return {"ok": False, "error": "仓库不存在"}

    def set_pinned(self, project_id: str, pinned: bool) -> dict[str, Any]:
        items = self._all()
        for it in items:
            if it["id"] == project_id:
                it["pinned"] = bool(pinned)
                self._save(items)
                return {"ok": True, "project": it}
        return {"ok": False, "error": "仓库不存在"}

    def set_cloud_enabled(self, project_id: str, enabled: bool) -> dict[str, Any]:
        items = self._all()
        for it in items:
            if it["id"] == project_id:
                it["cloud_enabled"] = bool(enabled)
                self._save(items)
                return {"ok": True, "project": it}
        return {"ok": False, "error": "仓库不存在"}

    def touch(self, project_id: str) -> None:
        items = self._all()
        for it in items:
            if it["id"] == project_id:
                it["last_opened_at"] = int(time.time())
                self._save(items)
                return
