"""RyuuMD —— 轻量、全本地、极速的 Markdown 编辑/阅读工具。

技术栈：pywebview（Windows WebView2 / macOS WKWebView）+ Vditor 即时渲染编辑器。
- 全本地，无需联网
- 拖入 md 文件或文件夹即可打开
- 以仓库形式管理笔记目录，首页快速切换，支持多窗口
- 单实例：双击 md 文件复用已运行实例，在新窗口中秒开
- 五套色板（霜靛 / 藤色 / 柳染 / 水浅葱 / 樱花，各带明暗），可换正文/等宽字体
"""

from __future__ import annotations

import os
import sys
import threading
from pathlib import Path
from typing import Any

from app.core.api import MD_EXTS, Api
from app.core.config import APP_NAME, Config
from app.core.singleton import InstanceServer, try_forward


def resource_path(*parts: str) -> str:
    """兼容 PyInstaller 打包后的资源路径。"""
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, *parts)


def webview_gui() -> str | None:
    """Windows 使用 Edge WebView2；其它平台由 pywebview 选默认后端（macOS 为 Cocoa）。

    start() 的 gui 合法值为 cef/qt/gtk/mshtml/edgechromium。
    """
    if sys.platform == "win32":
        return "edgechromium"
    return None


def start_webview(*args, **kwargs):
    """webview.start 的平台包装：补 GUI 后端，其余参数原样透传。"""
    import webview

    gui = webview_gui()
    if gui is not None:
        kwargs.setdefault("gui", gui)
    return webview.start(*args, **kwargs)


def _app_icon() -> str | None:
    """pywebview：Windows 用 .ico，macOS 用 .icns（源码态若尚未生成则省略）。"""
    name = "icon.icns" if sys.platform == "darwin" else "icon.ico"
    path = resource_path("assets", name)
    return path if os.path.isfile(path) else None


def _initial_path_from_argv() -> str:
    """支持「右键用 RyuuMD 打开」「拖到 exe 图标上」传入路径。"""
    for arg in sys.argv[1:]:
        p = Path(arg)
        if p.is_dir() or (p.is_file() and p.suffix.lower() in MD_EXTS):
            return str(p)
    return ""


class WindowManager:
    """应用窗口管理：每个窗口一个独立 Api 实例（各自初始路径），共享同一 Config。

    pywebview 支持在 GUI 主循环启动后从任意线程动态 create_window ——
    「新窗口打开仓库」「单实例转发」都走同一个 create()。
    """

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
                background_color="#f4faff",
                text_select=True,
            )
            api.bind_window(window)
            self._bind_drop(window)
            self._bind_size_memory(window)
            return window

    def focus_first(self) -> bool:
        """激活已有窗口（「再次启动程序 = 激活当前窗口」时由转发触发）。"""
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

    # ------------------------------------------------------------------
    # 拖放（每个窗口独立注册）
    # ------------------------------------------------------------------
    def _bind_drop(self, window: Any) -> None:
        def _on_drop(event: dict) -> None:
            """拖入文件 / 文件夹（兜底通道）。

            注意（2026-07-28 起）：Vditor 编辑器面板的 drop 处理器首行即
            stopPropagation，事件冒泡被截断，本 document 级监听实际收不到 drop。
            主通道已改为前端捕获阶段接管（app.js bindDragDrop）→ 转发
            api.open_dropped_files 打开。本处理器仅作前端 JS 失效时的最后防线。

            本注册必须保留：pywebview 仅在「通过 DOM 事件注册的 drop 监听」存在
            （_dnd_state['num_listeners']>0）时，原生层才会捕获拖入路径（FilesDropped
            消息 → _dnd_state['paths']），前端捕获转发同样依赖该通道。
            """
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
                # 拖放解析失败不应影响主流程
                pass

        def _bind() -> None:
            """注册原生 drop 监听。

            必须经 pywebview 的 DOM 事件通道注册，原生层才会回传本地完整路径
            （作为 FilesDropped 通道的开关，见 _on_drop 注释）；在 events.loaded
            时注册，确保 document 已就绪。prevent_default 阻止 WebView2 默认
            「导航到被拖入文件」的行为。
            """
            try:
                from webview.dom import DOMEventHandler

                window.dom.document.events.drop += DOMEventHandler(
                    _on_drop, prevent_default=True
                )
            except Exception:
                pass

        window.events.loaded += _bind

    # ------------------------------------------------------------------
    # 窗口尺寸记忆（最后关闭的窗口生效）
    # ------------------------------------------------------------------
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

    # 单实例：已有实例在运行时，把路径转发给它开新窗口，本进程退出。
    if try_forward(config, initial):
        return

    manager = WindowManager(config)

    # 接管为主实例：监听后续进程的转发请求。
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
    try:
        start_webview(debug=False, icon=_app_icon())
    finally:
        server.stop()


if __name__ == "__main__":
    main()
