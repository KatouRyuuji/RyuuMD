"""配置持久化：操作风格、主题、上次打开的文件/文件夹、欢迎弹窗状态等。

配置写入用户数据目录（Windows: %APPDATA%/RyuuMD），与程序文件分离，
保证打包后仍可读写。
"""

from __future__ import annotations

import copy
import json
import os
import threading
from pathlib import Path
from typing import Any

APP_NAME = "RyuuMD"

# 默认配置
DEFAULTS: dict[str, Any] = {
    # 操作风格：notion = typora+notion（/h1 等英文/符号触发）
    #          wolai  = typora+wolai（/bt1、/dmk 等拼音缩写触发）
    "operation_style": "notion",
    # 主题：light = phycat sky，dark = phycat vampire
    "theme": "light",
    # 显示模式：ir = 渲染模式（Typora 式即时渲染），sv = 源码模式
    "display_mode": "ir",
    # 是否已展示过首次欢迎窗口（含操作风格选择）
    "welcome_shown": False,
    # 上次打开的单文件路径
    "last_file": "",
    # 上次打开的工作文件夹
    "last_folder": "",
    # 最近打开列表（最新在前）：[{path, kind}]，kind = file | folder
    "recent_files": [],
    # 仓库列表：[{id, name, path, pinned, created_at, last_opened_at}]
    "projects": [],
    # 首页仓库视图：card = 卡片，list = 列表
    "home_view": "card",
    # 启动页：home = 始终显示首页（默认），restore = 恢复上次会话（无会话则进首页）
    "startup_page": "home",
    # 已保存文档在停止输入后自动写盘（Typora / Joplin 同款；未保存的新文档不弹框）
    "auto_save": True,
    # 每日笔记子目录（相对当前仓库根），默认「日记」
    "daily_note_folder": "日记",
    # 窗口尺寸
    "window_width": 1280,
    "window_height": 820,
    # 云同步（默认关闭；官方不提供云，用户自备 WebDAV 后在设置中勾选启用）
    "cloud_sync": {
        "enabled": False,
        "provider": "webdav",
        "url": "",
        "username": "",
        "password": "",
        "remote_root": "RyuuMD",
        "auto_on_save": False,
        "auto_on_start": False,
        "insecure_ssl": False,
        "sync_all_projects": False,
    },
}


def _data_dir() -> Path:
    base = os.environ.get("APPDATA") or os.path.expanduser("~")
    d = Path(base) / APP_NAME
    d.mkdir(parents=True, exist_ok=True)
    return d


class Config:
    """线程安全的简单 JSON 配置。"""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._path = _data_dir() / "config.json"
        self._data: dict[str, Any] = copy.deepcopy(DEFAULTS)
        self._load()

    def _load(self) -> None:
        if self._path.exists():
            try:
                loaded = json.loads(self._path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    self._data.update(loaded)
            except Exception:
                # 配置损坏时回退到默认值，不阻断启动
                pass

    def _save(self) -> None:
        try:
            # 原子写：先写同目录临时文件再 replace，进程中断/崩溃不留半截 JSON
            tmp = self._path.with_suffix(".json.tmp")
            tmp.write_text(
                json.dumps(self._data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            os.replace(tmp, self._path)
        except Exception:
            pass

    def get(self, key: str, default: Any = None) -> Any:
        with self._lock:
            return self._data.get(key, DEFAULTS.get(key, default))

    def set(self, key: str, value: Any) -> None:
        with self._lock:
            self._data[key] = value
            self._save()

    def update(self, values: dict[str, Any]) -> None:
        with self._lock:
            self._data.update(values)
            self._save()

    def all(self) -> dict[str, Any]:
        with self._lock:
            return dict(self._data)

    @property
    def data_dir(self) -> Path:
        return _data_dir()
