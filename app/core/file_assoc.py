"""Windows 下的 Markdown 文件关联：注册应用 + 一键唤起系统默认应用确认。

Win10/11 合规做法（与 VSCode / Typora 一致）：
1. register()：把 RyuuMD 注册为 HKCU 下的 Markdown 处理程序
   （ProgID + 各扩展名 OpenWithProgids + Capabilities），无需管理员权限，
   注册后即出现在「打开方式」列表与系统「默认应用」设置页；
2. prompt_set_default()：弹出系统「打开方式」对话框（SHOpenWithDialog），
   用户选择 RyuuMD 并勾选「始终」即完成 —— Win10 起 UserChoice 受哈希保护，
   程序不能静默改默认，弹系统对话框让用户确认一次是唯一合规路径。
   对话框以一个本地「验证文档」为目标，确认后立即用默认应用打开它，
   用户在 RyuuMD 中看到该文档即确认设置成功，形成闭环。
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from .config import Config
from .fsutil import MD_EXTS

PROG_ID = "RyuuMD.Document"
APP_REG_KEY = r"Software\RyuuMD"
DOC_FRIENDLY_NAME = "Markdown 文档 (RyuuMD)"

# SHOpenWithDialog 标志位
_OAIF_ALLOW_REGISTRATION = 0x01  # 显示「始终使用此应用」
_OAIF_REGISTER_EXT = 0x02        # 用户确认后写入关联
_OAIF_EXEC = 0x04                # 确认后立即用所选应用打开文件

_VERIFY_DOC = """# 默认应用设置成功

看到这个页面，说明 `.md` 文件已经默认用 **RyuuMD** 打开了。

从现在起：

- 双击任意 Markdown 文件即可直接进入 RyuuMD 阅读/编辑；
- 已打开 RyuuMD 时，再双击其他 md 文件会在**新窗口**中打开，快速又省心。

> 本文件由 RyuuMD 自动生成，可放心删除。
"""


def supported() -> bool:
    return sys.platform == "win32"


def _launch_command() -> str:
    """构造「打开方式」注册命令行。打包 exe 与源码运行（开发态）均可用。"""
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}" "%1"'
    py = Path(sys.executable)
    pyw = py.with_name("pythonw.exe")  # 无控制台窗口
    interp = pyw if pyw.is_file() else py
    script = Path(__file__).resolve().parents[2] / "main.py"
    return f'"{interp}" "{script}" "%1"'


def _icon_source() -> str:
    if getattr(sys, "frozen", False):
        return f"{sys.executable},0"
    ico = Path(__file__).resolve().parents[2] / "assets" / "icon.ico"
    return str(ico) if ico.is_file() else ""


def register() -> dict[str, Any]:
    """注册 ProgID / OpenWithProgids / Capabilities（全部 HKCU，无需管理员）。"""
    if not supported():
        return {"ok": False, "error": "仅支持 Windows"}
    import winreg

    try:
        classes = r"Software\Classes"
        # ProgID：文档类型 + 图标 + 打开命令
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, rf"{classes}\{PROG_ID}") as k:
            winreg.SetValueEx(k, None, 0, winreg.REG_SZ, DOC_FRIENDLY_NAME)
        icon = _icon_source()
        if icon:
            with winreg.CreateKey(winreg.HKEY_CURRENT_USER, rf"{classes}\{PROG_ID}\DefaultIcon") as k:
                winreg.SetValueEx(k, None, 0, winreg.REG_SZ, icon)
        with winreg.CreateKey(
            winreg.HKEY_CURRENT_USER, rf"{classes}\{PROG_ID}\shell\open\command"
        ) as k:
            winreg.SetValueEx(k, None, 0, winreg.REG_SZ, _launch_command())

        # 各 md 扩展名挂到「打开方式」候选
        for ext in sorted(MD_EXTS):
            with winreg.CreateKey(
                winreg.HKEY_CURRENT_USER, rf"{classes}\{ext}\OpenWithProgids"
            ) as k:
                winreg.SetValueEx(k, PROG_ID, 0, winreg.REG_SZ, "")

        # Capabilities：让 RyuuMD 出现在系统「默认应用」设置页
        with winreg.CreateKey(
            winreg.HKEY_CURRENT_USER, rf"{APP_REG_KEY}\Capabilities"
        ) as k:
            winreg.SetValueEx(k, "ApplicationName", 0, winreg.REG_SZ, "RyuuMD")
            winreg.SetValueEx(
                k, "ApplicationDescription", 0, winreg.REG_SZ,
                "轻量、全本地、极速的 Markdown 编辑与阅读工具",
            )
        with winreg.CreateKey(
            winreg.HKEY_CURRENT_USER, rf"{APP_REG_KEY}\Capabilities\FileAssociations"
        ) as k:
            for ext in sorted(MD_EXTS):
                winreg.SetValueEx(k, ext, 0, winreg.REG_SZ, PROG_ID)
        with winreg.CreateKey(
            winreg.HKEY_CURRENT_USER, r"Software\RegisteredApplications"
        ) as k:
            winreg.SetValueEx(k, "RyuuMD", 0, winreg.REG_SZ, rf"{APP_REG_KEY}\Capabilities")

        _notify_assoc_changed()
        return {"ok": True}
    except OSError as e:
        return {"ok": False, "error": str(e)}


def _notify_assoc_changed() -> None:
    """通知 Shell 关联已变（刷新资源管理器图标/打开方式缓存）。"""
    try:
        import ctypes

        SHCNE_ASSOCCHANGED = 0x08000000
        SHCNF_IDLIST = 0x0
        ctypes.windll.shell32.SHChangeNotify(SHCNE_ASSOCCHANGED, SHCNF_IDLIST, None, None)
    except Exception:  # noqa: BLE001
        pass


def current_default_progid(ext: str = ".md") -> str:
    """查询扩展名当前生效的 ProgID（UserChoice 优先，系统解析）。"""
    if not supported():
        return ""
    try:
        import ctypes
        from ctypes import wintypes

        ASSOCF_NONE = 0
        ASSOCSTR_PROGID = 20
        buf_len = wintypes.DWORD(0)
        # 先取长度，再取值
        ctypes.windll.shlwapi.AssocQueryStringW(
            ASSOCF_NONE, ASSOCSTR_PROGID, ext, None, None, ctypes.byref(buf_len)
        )
        if buf_len.value <= 0:
            return ""
        buf = ctypes.create_unicode_buffer(buf_len.value)
        hr = ctypes.windll.shlwapi.AssocQueryStringW(
            ASSOCF_NONE, ASSOCSTR_PROGID, ext, None, buf, ctypes.byref(buf_len)
        )
        return buf.value if hr == 0 else ""
    except Exception:  # noqa: BLE001
        return ""


def status() -> dict[str, Any]:
    """当前关联状态：是否已注册（打开方式可选）/ 是否已是默认应用。"""
    if not supported():
        return {"ok": True, "supported": False, "registered": False, "is_default": False}
    import winreg

    registered = False
    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, rf"Software\Classes\{PROG_ID}\shell\open\command"
        ):
            registered = True
    except OSError:
        pass
    return {
        "ok": True,
        "supported": True,
        "registered": registered,
        "is_default": current_default_progid(".md") == PROG_ID,
    }


def prompt_set_default(config: Config) -> dict[str, Any]:
    """一键设为默认：注册 + 弹系统「打开方式」对话框（含「始终」选项）。

    确认后系统会立即用所选应用打开验证文档（OAIF_EXEC），RyuuMD 的单实例
    机制会把它接进新窗口，用户看到「设置成功」页面即完成闭环。
    """
    if not supported():
        return {"ok": False, "error": "仅支持 Windows"}
    reg = register()
    if not reg["ok"]:
        return reg

    # 验证文档：对话框的目标文件，确认后被默认应用打开
    verify = config.data_dir / "默认应用验证.md"
    try:
        verify.write_text(_VERIFY_DOC, encoding="utf-8")
    except OSError as e:
        return {"ok": False, "error": str(e)}

    try:
        import ctypes
        from ctypes import wintypes

        class OPENASINFO(ctypes.Structure):
            _fields_ = [
                ("pcszFile", wintypes.LPCWSTR),
                ("pcszClass", wintypes.LPCWSTR),
                ("oaifInFlags", ctypes.c_int),
            ]

        info = OPENASINFO(
            pcszFile=str(verify),
            pcszClass=None,
            oaifInFlags=_OAIF_ALLOW_REGISTRATION | _OAIF_REGISTER_EXT | _OAIF_EXEC,
        )
        hr = ctypes.windll.shell32.SHOpenWithDialog(None, ctypes.byref(info))
        _notify_assoc_changed()
        # 用户取消对话框时返回非 0（HRESULT_FROM_WIN32(ERROR_CANCELLED)）
        return {"ok": True, "confirmed": hr == 0, "status": status()}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)}
