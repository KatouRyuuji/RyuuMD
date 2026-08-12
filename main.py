"""RyuuMD —— 轻量、全本地、极速的 Markdown 编辑/阅读工具。

技术栈：pywebview（系统 WebView2）+ Vditor 即时渲染编辑器。
- 全本地，无需联网
- 拖入 md 文件或文件夹即可打开
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


def main() -> None:
    config = Config()
    api = Api(config)

    index = resource_path("app", "web", "index.html")

    window = webview.create_window(
        title=APP_NAME,
        url=index,
        js_api=api,
        width=int(config.get("window_width", 1280)),
        height=int(config.get("window_height", 820)),
        min_size=(880, 600),
        background_color="#f4faff",
        text_select=True,
    )
    api.bind_window(window)

    # 命令行传入的初始路径（拖到 exe / 右键打开）注入到前端
    initial = _initial_path_from_argv()

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

    def _bind_drop() -> None:
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

    # 页面 DOM 就绪后再绑定拖放（start 回调可能早于页面加载完成）
    window.events.loaded += _bind_drop

    def _on_start() -> None:
        if initial:
            safe = initial.replace("\\", "\\\\").replace("'", "\\'")
            window.evaluate_js(f"window.__INITIAL_PATH__='{safe}';")

    # 应用图标（标题栏 / 任务栏）。winforms(edgechromium) 后端会读取
    # _state['icon'] 设置窗口 Icon —— 文档虽写 GTK/QT，但 Windows 实际生效。
    icon_path = resource_path("assets", "icon.ico")
    icon = icon_path if os.path.isfile(icon_path) else None

    webview.start(_on_start, gui="edgechromium", debug=False, icon=icon)


if __name__ == "__main__":
    main()
