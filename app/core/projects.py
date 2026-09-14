"""仓库（项目）管理：把不同的笔记目录注册为仓库，支持置顶、重命名、快速切换。

参考 Obsidian 的 vault 模型：仓库 = 用户显式收藏的目录，与「最近打开」
（自动记录）互不混淆。数据持久化在 config["projects"]，结构：
[{id, name, path, pinned, created_at, last_opened_at}]
"""

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
    """路径规范化，用于同仓库去重。

    Windows：normcase 折叠大小写。macOS：normcase 是空操作，且 /var 与
    /private/var 是同一目录，需 resolve + casefold。
    """
    p = os.path.normpath(str(path))
    if sys.platform == "darwin":
        try:
            p = str(Path(p).resolve())
        except OSError:
            pass
        return p.casefold()
    return os.path.normcase(p)


class ProjectStore:
    """仓库列表的增删改查。所有写操作立即持久化到 config。"""

    # 与 Config 相同：只给 Python 用，不要被 pywebview 扫成 JS API。
    _serializable = False

    def __init__(self, config: Config) -> None:
        self.config = config

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------
    def _all(self) -> list[dict[str, Any]]:
        items = self.config.get("projects", []) or []
        return [it for it in items if isinstance(it, dict) and it.get("path") and it.get("id")]

    def _save(self, items: list[dict[str, Any]]) -> None:
        self.config.set("projects", items)

    # ------------------------------------------------------------------
    # 查询
    # ------------------------------------------------------------------
    def list(self, with_stats: bool = True) -> list[dict[str, Any]]:
        """返回展示用仓库列表：置顶在前，其余按最后打开时间倒序。

        with_stats 时附带 md 文件计数（有预算上限，大仓库显示 N+）。
        失效仓库（目录被移动/删除）保留展示，由前端灰显。
        """
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
        """找包含该文件/目录的仓库（最长前缀，避免父子目录误匹配）。"""
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

    # ------------------------------------------------------------------
    # 变更
    # ------------------------------------------------------------------
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
        """在父目录下新建文件夹并注册为仓库。"""
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
        """记录一次打开（更新 last_opened_at，用于排序）。"""
        items = self._all()
        for it in items:
            if it["id"] == project_id:
                it["last_opened_at"] = int(time.time())
                self._save(items)
                return
