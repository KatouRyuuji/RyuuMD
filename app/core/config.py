"""配置持久化：操作风格、主题、上次打开的文件/文件夹、欢迎弹窗状态等。

配置写入用户数据目录（Windows: %APPDATA%/RyuuMD），与程序文件分离，
保证打包后仍可读写。
"""

from __future__ import annotations

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
    # 主题：light = phycat sky，dark = phycat vampire，system 跟随系统
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
    # 窗口尺寸
    "window_width": 1280,
    "window_height": 820,
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
        self._data: dict[str, Any] = dict(DEFAULTS)
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
            self._path.write_text(
                json.dumps(self._data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
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
