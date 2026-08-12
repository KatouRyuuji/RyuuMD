"""RyuuMD —— 轻量、全本地、极速的 Markdown 编辑/阅读工具。

技术栈：pywebview（系统 WebView2）+ Vditor 即时渲染编辑器。
- 全本地，无需联网
- 拖入 md 文件或文件夹即可打开
- 以仓库形式管理笔记目录，首页快速切换，支持多窗口
- 单实例：双击 md 文件复用已运行实例，在新窗口中秒开
- phycat sky（亮）/ vampire（暗）配色
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import webview
from webview.dom import DOMEventHandler

from app.core.api import MD_EXTS, Api
from app.core.config import APP_NAME, Config
from app.core.singleton import InstanceServer, try_forward


def resource_path(*parts: str) -> str:
    """兼容 PyInstaller 打包后的资源路径。"""
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, *parts)


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

    def create(self, initial_path: str = "") -> "webview.Window":
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

    # ------------------------------------------------------------------
    # 拖放（每个窗口独立注册）
    # ------------------------------------------------------------------
    def _bind_drop(self, window: "webview.Window") -> None:
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
                window.dom.document.events.drop += DOMEventHandler(
                    _on_drop, prevent_default=True
                )
            except Exception:
                pass

        window.events.loaded += _bind

    # ------------------------------------------------------------------
    # 窗口尺寸记忆（最后关闭的窗口生效）
    # ------------------------------------------------------------------
    def _bind_size_memory(self, window: "webview.Window") -> None:
        def _remember() -> None:
            try:
                w, h = int(window.width), int(window.height)
                if w >= 880 and h >= 600:
                    self.config.update({"window_width": w, "window_height": h})
            except Exception:
                pass

        window.events.closing += _remember


def main() -> None:
    initial = _initial_path_from_argv()
    config = Config()

    # 单实例：已有实例在运行时，把路径转发给它（在新窗口打开），本进程直接退出。
    # 双击 md 文件不再冷启动整套 WebView2，秒开。
    if try_forward(config, initial):
        return

    manager = WindowManager(config)

    # 接管为主实例：监听后续进程的转发请求 → 开新窗口
    server = InstanceServer(config, on_open=lambda path: manager.create(path))
    server.start()

    manager.create(initial)

    # 应用图标（标题栏 / 任务栏）。winforms(edgechromium) 后端会读取
    # _state['icon'] 设置窗口 Icon —— 文档虽写 GTK/QT，但 Windows 实际生效。
    icon_path = resource_path("assets", "icon.ico")
    icon = icon_path if os.path.isfile(icon_path) else None

    try:
        webview.start(gui="edgechromium", debug=False, icon=icon)
    finally:
        server.stop()


if __name__ == "__main__":
    main()
