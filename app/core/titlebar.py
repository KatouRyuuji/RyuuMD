"""原生标题栏明暗同步（Windows DWM 沉浸式深色）。

pywebview 不暴露标题栏配色；Windows 10 20H1+ 支持
DWMWA_USE_IMMERSIVE_DARK_MODE(=20) 让原生标题栏随应用主题变深。
对本进程全部可见顶层窗口应用；其他平台为空操作。
"""

from __future__ import annotations

import os
import sys

_DWMWA_USE_IMMERSIVE_DARK_MODE = 20
_GWL_STYLE = -16
_WS_CAPTION = 0x00C00000


def set_dark_titlebar(dark: bool) -> None:
    """把本进程所有顶层窗口的原生标题栏设为深色/默认色。"""
    if sys.platform != "win32":
        return
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        dwmapi = ctypes.windll.dwmapi
        pid = os.getpid()
        hwnds: list[int] = []
        enum_proc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

        def _cb(hwnd: int, _lparam: int) -> bool:
            if user32.IsWindowVisible(hwnd):
                cpid = wintypes.DWORD()
                user32.GetWindowThreadProcessId(hwnd, ctypes.byref(cpid))
                if cpid.value == pid and (user32.GetWindowLongW(hwnd, _GWL_STYLE) & _WS_CAPTION):
                    hwnds.append(hwnd)
            return True

        user32.EnumWindows(enum_proc(_cb), 0)
        value = wintypes.DWORD(1 if dark else 0)
        for hwnd in hwnds:
            dwmapi.DwmSetWindowAttribute(
                hwnd, _DWMWA_USE_IMMERSIVE_DARK_MODE, ctypes.byref(value), ctypes.sizeof(value)
            )
    except Exception:  # noqa: BLE001 —— 老系统/无 DWM 时保持默认标题栏
        pass
