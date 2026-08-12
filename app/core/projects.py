"""仓库（项目）管理：把不同的笔记目录注册为仓库，支持置顶、重命名、快速切换。

参考 Obsidian 的 vault 模型：仓库 = 用户显式收藏的目录，与「最近打开」
（自动记录）互不混淆。数据持久化在 config["projects"]，结构：
[{id, name, path, pinned, created_at, last_opened_at}]
"""

from __future__ import annotations

import os
import time
import uuid
from pathlib import Path
from typing import Any, Optional

from .config import Config
from .fsutil import count_md_files


def _norm(path: str) -> str:
    """路径规范化（Windows 大小写不敏感），用于同仓库去重判断。"""
    return os.path.normcase(os.path.normpath(str(path)))


class ProjectStore:
    """仓库列表的增删改查。所有写操作立即持久化到 config。"""

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
            "name": (name or p.name or str(p)).strip(),
            "path": str(p),
            "pinned": False,
            "created_at": int(time.time()),
            "last_opened_at": 0,
        }
        items = self._all()
        items.append(project)
        self._save(items)
        return {"ok": True, "project": project, "existed": False}

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

    def touch(self, project_id: str) -> None:
        """记录一次打开（更新 last_opened_at，用于排序）。"""
        items = self._all()
        for it in items:
            if it["id"] == project_id:
                it["last_opened_at"] = int(time.time())
                self._save(items)
                return
