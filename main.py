"""RyuuMD —— 轻量、全本地、极速的 Markdown 编辑/阅读工具。
pywebview（Windows WebView2 / macOS WKWebView）+ Vditor 即时渲染编辑器。"""

from __future__ import annotations

import os
import sys
import threading
from pathlib import Path
from typing import Any

from app.core.api import MD_EXTS, Api
from app.core.config import APP_NAME, Config
from app.core.palette_css import chrome_background
from app.core.singleton import InstanceServer, try_forward

_CHROME_PALETTES = frozenset({"a1", "a3", "a4", "a5", "a6"})


def chrome_attrs(config: Config) -> tuple[str, str]:
    theme = "dark" if config.get("theme") == "dark" else "light"
    pal = str(config.get("palette") or "a1")
    if pal not in _CHROME_PALETTES:
        pal = "a1"
    return theme, pal


def window_background(config: Config) -> str:
    """与色板 --sys-bg 对齐，避免窗口先闪默认色。"""
    theme, pal = chrome_attrs(config)
    return chrome_background(pal, theme)


def resource_path(*parts: str) -> str:
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, *parts)


def webview_gui() -> str | None:
    if sys.platform == "win32":
        return "edgechromium"
    return None


def start_webview(*args, **kwargs):
    import webview

    gui = webview_gui()
    if gui is not None:
        kwargs.setdefault("gui", gui)
    return webview.start(*args, **kwargs)


def _app_icon() -> str | None:
    name = "icon.icns" if sys.platform == "darwin" else "icon.ico"
    path = resource_path("assets", name)
    return path if os.path.isfile(path) else None


def purge_stale_web_cache(storage: Path) -> bool:
    """程序换版后清掉 WebView2 的 HTTP 缓存。

    持久模式下页面 URL 跨版本不变，旧版缓存的 index.html / css / js 会被直接复用；
    以程序本体 mtime+size 作构建指纹，变了即清。只动 HTTP 与字节码缓存，localStorage 不受影响。"""
    import shutil

    build = Path(sys.executable if getattr(sys, "frozen", False) else resource_path("app", "web", "index.html"))
    try:
        st = build.stat()
    except OSError:
        return False
    stamp = f"{int(st.st_mtime)}-{st.st_size}"
    stamp_file = storage / ".build"
    try:
        if stamp_file.read_text(encoding="utf-8") == stamp:
            return False
    except OSError:
        pass
    profile = storage / "EBWebView" / "Default"
    for name in ("Cache", "Code Cache"):
        shutil.rmtree(profile / name, ignore_errors=True)
    storage.mkdir(parents=True, exist_ok=True)
    stamp_file.write_text(stamp, encoding="utf-8")
    return True


def _initial_path_from_argv() -> str:
    for arg in sys.argv[1:]:
        p = Path(arg)
        if p.is_dir() or (p.is_file() and p.suffix.lower() in MD_EXTS):
            return str(p)
    return ""


class WindowManager:

    def __init__(self, config: Config) -> None:
        self.config = config
        self._index = resource_path("app", "web", "index.html")
        # 建窗串行化：转发线程与前端 open_new_window 桥线程可能并发触发，
        # pywebview 的 create_window 非线程安全（windows 列表/事件注册）
        self._create_lock = threading.Lock()

    def create(self, initial_path: str = "") -> Any:
        import webview

        with self._create_lock:
            api = Api(self.config, window_manager=self, initial_path=initial_path)
            window = webview.create_window(
                title=APP_NAME,
                url=self._index,
                js_api=api,
                width=int(self.config.get("window_width", 1280)),
                height=int(self.config.get("window_height", 820)),
                min_size=(880, 600),
                background_color=window_background(self.config),
                text_select=True,
            )
            api.bind_window(window)
            # 原生标题栏随应用主题（Windows DWM 沉浸式深色，其他平台空操作）
            from app.core.titlebar import set_dark_titlebar

            set_dark_titlebar(self.config.get("theme") == "dark")
            self._bind_drop(window)
            self._bind_size_memory(window)
            return window

    def focus_first(self) -> bool:
        try:
            import webview

            wins = list(webview.windows)
            if not wins:
                return False
            win = wins[0]
            win.restore()  # 最小化时还原
            win.show()     # 置前激活
            return True
        except Exception:  # noqa: BLE001
            return False

    def _bind_drop(self, window: Any) -> None:
        def _on_drop(event: dict) -> None:
            """拖入兜底通道：Vditor 面板 drop 首行 stopPropagation，本监听实际收不到事件；
            主通道是前端捕获阶段转发 api.open_dropped_files。

            本注册必须保留：pywebview 仅在存在 DOM drop 监听时，原生层才回传 FilesDropped 路径，
            前端捕获转发同样依赖该通道。"""
            try:
                data = (event or {}).get("dataTransfer") or {}
                files = data.get("files") or []
                for f in files:
                    full = (f or {}).get("pywebviewFullPath")
                    if not full:
                        continue
                    p = Path(full)
                    if p.is_dir() or (p.is_file() and p.suffix.lower() in MD_EXTS):
                        safe = full.replace("\\", "\\\\").replace("'", "\\'")
                        window.evaluate_js(
                            f"window.__openDroppedPath && window.__openDroppedPath('{safe}')"
                        )
                        return
            except Exception:
                pass

        def _bind() -> None:
            """必须经 pywebview 的 DOM 事件通道注册，原生层才会回传本地完整路径（见 _on_drop 注释）。"""
            try:
                from webview.dom import DOMEventHandler

                window.dom.document.events.drop += DOMEventHandler(
                    _on_drop, prevent_default=True
                )
            except Exception:
                pass

        window.events.loaded += _bind

    def _bind_size_memory(self, window: Any) -> None:
        def _remember() -> None:
            try:
                w, h = int(window.width), int(window.height)
                if w >= 880 and h >= 600:
                    self.config.update({"window_width": w, "window_height": h})
            except Exception:
                pass

        window.events.closing += _remember


def main() -> None:
    from app.core.adv import dispatch_argv, is_adv_argv

    if is_adv_argv(sys.argv[1:]):
        raise SystemExit(dispatch_argv(sys.argv[1:]))

    initial = _initial_path_from_argv()
    config = Config()

    if try_forward(config, initial):
        return

    manager = WindowManager(config)

    # 空路径（再次启动程序）且用户设为「激活当前窗口」时只置前已有窗口；
    # 带路径（双击 md 文件）是明确打开意图，始终开新窗口。
    def _on_open(path: str) -> None:
        if not path and config.get("second_launch", "new") == "focus":
            if manager.focus_first():
                return
        manager.create(path)

    server = InstanceServer(config, on_open=_on_open)
    server.start()

    manager.create(initial)

    # 应用图标（标题栏 / 任务栏 / Dock）。Windows 读 .ico；macOS 打包后
    # Dock 图标主要来自 .app 的 icns，此处再传一份给 pywebview。
    # 持久 WebView 用户目录：字体缓存与 localStorage（ryuumd-chrome）跨启动保留。
    purge_stale_web_cache(config.data_dir / "webview")
    try:
        start_webview(
            debug=False,
            icon=_app_icon(),
            private_mode=False,
            storage_path=str(config.data_dir / "webview"),
        )
    finally:
        server.stop()


if __name__ == "__main__":
    main()
