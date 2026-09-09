"""配置持久化：操作风格、主题、上次打开的文件/文件夹、欢迎弹窗状态等。

配置写入用户数据目录，与程序文件分离，保证打包后仍可读写。
Windows: %APPDATA%/RyuuMD；macOS: ~/Library/Application Support/RyuuMD。
测试通过设置 APPDATA 覆盖任意平台的落点。
"""

from __future__ import annotations

import copy
import json
import os
import sys
import threading
from pathlib import Path
from typing import Any

APP_NAME = "RyuuMD"

# RyuujiDesign A 语言色板 id；旧 phycat id 在 Config._load 时映射到此集合
_VALID_PALETTES = frozenset({"a1", "a2", "a3", "a4", "a5", "a6"})
_LEGACY_PALETTE_MAP = {
    "cherry": "a2", "vampire": "a2", "caramel": "a2", "sakura": "a6", "mauve": "a3",
    "mint": "a5", "abyss": "a5", "forest": "a4", "radiation": "a4",
    "sky": "a1", "prussian": "a1",
}

# Anthropic Messages 默认；DEFAULTS["ai"] 与此同一份，避免两处漂移
DEFAULT_AI: dict[str, Any] = {
    "base_url": "https://api.anthropic.com",
    "api_key": "",
    "model": "claude-sonnet-4-20250514",
}

# 默认配置
DEFAULTS: dict[str, Any] = {
    # 操作风格：notion = typora+notion（/h1 等英文/符号触发）
    #          wolai  = typora+wolai（/bt1、/dmk 等拼音缩写触发）
    "operation_style": "notion",
    # 主题明暗：light | dark（决定 Vditor setTheme 与基础明暗）
    "theme": "light",
    # 配色方案：RyuujiDesign v6.1 A 语言色板 a1 霜靛 / a2 和红 / a3 藤色 / a4 柳染 /
    # a5 水浅葱 / a6 樱花；每板自带明暗双态（palettes.css）。旧 phycat 的
    # palette_light/palette_dark 键由前端读取时经映射表迁移为本键（api.py/app.js 各一份）
    "palette": "a1",
    # 界面字体；空字符串 = 跟随主题默认（霞鹜文楷）
    "font_ui": "",
    # 等宽字体；空字符串 = 跟随主题默认（Cascadia Code）
    "font_mono": "",
    # 显示模式：ir = 渲染模式（Typora 式即时渲染），sv = 源码模式
    "display_mode": "ir",
    # 公式渲染引擎：katex（默认，快）| mathjax（Typora 同款，兼容更多 LaTeX 宏/语法）
    "math_engine": "katex",
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
    # 启动页：home = 始终显示首页（默认）；restore = 恢复上次会话（无会话则进首页）。
    # 前端仅在值为 restore 时走恢复；缺失 / 非法值一律按 home，避免缺字段误进恢复。
    "startup_page": "home",
    # 已有窗口运行时再次启动程序：new = 新开一个窗口（默认）；focus = 仅激活当前窗口。
    # 双击 md 文件（带路径）不受此项影响，始终开新窗口。
    "second_launch": "new",
    # 已保存文档在停止输入后自动写盘（Typora / Joplin 同款；未保存的新文档不弹框）
    "auto_save": True,
    # 编辑方式：source = 直接读写源文件（默认）；workdir = 进入仓库时生成工作副本，
    # 只在副本上读写，用「保存至源文件 / 合并源文件」与源同步。
    "edit_mode": "source",
    # 每日笔记子目录（相对当前仓库根），默认「日记」
    "daily_note_folder": "日记",
    # 编辑区缩放百分比（Ctrl+= / Ctrl+- / Ctrl+0）
    "editor_zoom": 100,
    # Typora 式可读宽度：正文限制最大行宽，居中排版
    "readable_width": False,
    # 侧栏宽度（像素），拖拽右缘调整，范围 180–480，双击恢复 256
    "sidebar_width": 256,
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
    # Anthropic Messages 协议：用户自备兼容端点（官方或本地中转）
    "ai": dict(DEFAULT_AI),
}


def _data_dir_base() -> Path:
    """用户数据根目录（不含 APP_NAME）。APPDATA 优先，便于测试隔离。"""
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
    """线程安全的简单 JSON 配置。"""

    # pywebview 会递归暴露 js_api 上的公开属性；禁止把 Config / Path 扫进 JS 桥。
    _serializable = False

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._path = _data_dir() / "config.json"
        self._data: dict[str, Any] = copy.deepcopy(DEFAULTS)
        self._load()

    def _migrate_palette(self, loaded: dict[str, Any]) -> None:
        """旧配置只有 palette_light/dark 或 phycat id 时，写入 palette a1–a6。"""
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
