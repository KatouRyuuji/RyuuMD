"""配置持久化到用户数据目录，与程序文件分离，保证打包后仍可读写。
Windows: %APPDATA%/RyuuMD；macOS: ~/Library/Application Support/RyuuMD。测试经 APPDATA 覆盖落点。"""

from __future__ import annotations

import copy
import json
import os
import sys
import threading
from pathlib import Path
from typing import Any

APP_NAME = "RyuuMD"

# 当前色板 id；旧 id（含已退役的 a2）在 Config._load 时映射到此集合
_VALID_PALETTES = frozenset({"a1", "a3", "a4", "a5", "a6"})
_LEGACY_PALETTE_MAP = {
    "cherry": "a6", "vampire": "a6", "caramel": "a6", "a2": "a6", "sakura": "a6", "mauve": "a3",
    "mint": "a5", "abyss": "a5", "forest": "a4", "radiation": "a4",
    "sky": "a1", "prussian": "a1",
}

# Anthropic Messages 默认；DEFAULTS["ai"] 与此同一份，避免两处漂移
DEFAULT_AI: dict[str, Any] = {
    "base_url": "https://api.anthropic.com",
    "api_key": "",
    "model": "claude-sonnet-4-20250514",
}

DEFAULTS: dict[str, Any] = {
    # 操作风格：notion = typora+notion（/h1 等英文/符号触发）
    #          wolai  = typora+wolai（/bt1、/dmk 等拼音缩写触发）
    "operation_style": "notion",
    "theme": "light",
    # 配色方案：a1 霜靛 / a3 藤色 / a4 柳染 / a5 水浅葱 / a6 樱花；每板自带明暗双态。
    # 旧 palette_light/palette_dark 与已退役 a2 在读取时映射到本键。
    "palette": "a1",
    "reduced_motion": False,
    "font_ui": "",
    "font_mono": "",
    # 显示模式：ir = 渲染模式（Typora 式即时渲染），sv = 源码模式
    "display_mode": "ir",
    # 公式渲染引擎：katex（默认，快）| mathjax（Typora 同款，兼容更多 LaTeX 宏/语法）
    "math_engine": "katex",
    "welcome_shown": False,
    "last_file": "",
    "last_folder": "",
    "recent_files": [],
    "projects": [],
    "home_view": "card",
    # 启动页：home = 始终显示首页（默认）；restore = 恢复上次会话（无会话则进首页）。
    # 前端仅在值为 restore 时走恢复；缺失 / 非法值一律按 home，避免缺字段误进恢复。
    "startup_page": "home",
    # 已有窗口运行时再次启动程序：new = 新开一个窗口（默认）；focus = 仅激活当前窗口。
    # 双击 md 文件（带路径）不受此项影响，始终开新窗口。
    "second_launch": "new",
    "auto_save": True,
    # 编辑方式：source = 直接读写源文件（默认）；workdir = 进入仓库时生成工作副本，
    # 只在副本上读写，用「保存至源文件 / 合并源文件」与源同步。
    "edit_mode": "source",
    "daily_note_folder": "日记",
    # 粘贴/拖入图片的存放方式（Obsidian 式）：
    #   subfolder = 笔记同级子文件夹（默认，名取 attachment_folder）
    #   assets    = Typora 式 `{笔记名}.assets/`
    #   vault     = 仓库根下固定文件夹（名取 attachment_folder）
    #   same      = 与笔记同目录
    "attachment_mode": "subfolder",
    "attachment_folder": "image",
    "vertical_layout": False,
    "editor_zoom": 100,
    "readable_width": False,
    # 侧栏宽度（像素），拖拽右缘调整，范围 180–480，双击恢复 256
    "sidebar_width": 256,
    "window_width": 1280,
    "window_height": 820,
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
    "ai": dict(DEFAULT_AI),
    "im": {
        "feishu": {"webhook": ""},
        "popo": {"webhook": ""},
        "dingtalk": {"webhook": ""},
        "wecom": {"webhook": ""},
        "wechat": {"webhook": ""},
        "qq": {"webhook": ""},
    },
}


def _data_dir_base() -> Path:
    env = os.environ.get("APPDATA")
    if env:
        return Path(env)
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support"
    return Path.home()


def _data_dir() -> Path:
    d = _data_dir_base() / APP_NAME
    d.mkdir(parents=True, exist_ok=True)
    return d


class Config:

    # pywebview 会递归暴露 js_api 上的公开属性；禁止把 Config / Path 扫进 JS 桥。
    _serializable = False

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._path = _data_dir() / "config.json"
        self._data: dict[str, Any] = copy.deepcopy(DEFAULTS)
        self._load()

    def _migrate_palette(self, loaded: dict[str, Any]) -> None:
        pal = self._data.get("palette")
        if "palette" not in loaded:
            theme = self._data.get("theme")
            legacy = loaded.get("palette_dark" if theme == "dark" else "palette_light")
            self._data["palette"] = _LEGACY_PALETTE_MAP.get(str(legacy or ""), "a1")
            return
        if pal not in _VALID_PALETTES:
            self._data["palette"] = _LEGACY_PALETTE_MAP.get(str(pal or ""), "a1")

    def _load(self) -> None:
        if self._path.exists():
            try:
                if os.name != "nt":
                    try:
                        self._path.chmod(0o600)
                    except OSError:
                        pass
                loaded = json.loads(self._path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    self._data.update(loaded)
                    before = loaded.get("palette")
                    self._migrate_palette(loaded)
                    if self._data.get("palette") != before:
                        self._save()
            except Exception:
                # 配置损坏时回退到默认值，不阻断启动
                pass

    def _save(self) -> None:
        try:
            tmp = self._path.with_suffix(".json.tmp")
            tmp.write_text(
                json.dumps(self._data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            if os.name != "nt":
                try:
                    tmp.chmod(0o600)
                except OSError:
                    pass
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
