"""文件系统共享常量与工具（api / projects 共用，独立成模块避免循环导入）。"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

# 视为 markdown 的扩展名
MD_EXTS = {".md", ".markdown", ".mdown", ".mkd", ".mdx"}

# 扫描忽略项：隐藏目录（以 . 开头）一律跳过，这里列出的是
# 不带点前缀的常见依赖/构建产物目录（可能包含数万文件，扫描即卡死）
IGNORE_DIRS = {
    ".git", ".obsidian", ".idea", ".vscode", ".venv",
    "node_modules", "__pycache__", "venv", "dist", "build", "target",
}

# 粘贴/拖入图片允许的扩展名（与 Typora 常见配图一致）
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".svg"}


def skip_dir_name(name: str) -> bool:
    """目录扫描是否跳过：隐藏目录、依赖目录、Typora 式 `*.assets` 配图目录。"""
    if not name:
        return True
    if name.startswith(".") or name in IGNORE_DIRS:
        return True
    if name.endswith(".assets"):
        return True
    return False


def count_md_files(root: str, budget: int = 800, max_depth: int = 5) -> tuple[int, bool]:
    """快速统计目录下 markdown 文件数（供首页仓库卡片展示）。

    预算/深度双上限：仓库很大时截断（返回 capped=True，前端显示「N+」），
    保证任意仓库统计耗时可控，首页秒开。
    """
    count = 0
    capped = False
    stack: list[tuple[str, int]] = [(root, 0)]
    while stack:
        directory, depth = stack.pop()
        try:
            with os.scandir(directory) as it:
                for entry in it:
                    if count >= budget:
                        return count, True
                    if entry.is_dir(follow_symlinks=False):
                        if skip_dir_name(entry.name):
                            continue
                        if depth < max_depth:
                            stack.append((entry.path, depth + 1))
                        else:
                            capped = True
                    elif os.path.splitext(entry.name)[1].lower() in MD_EXTS:
                        count += 1
        except OSError:
            continue
    return count, capped


def atomic_write_bytes(path: "str | Path", data: bytes) -> None:
    """原子写：同目录临时文件 + os.replace，进程崩溃/断电不留半截文件。

    临时文件名带随机后缀，两个窗口同时保存同一文件也不会撞名；
    失败时清理残片后原样抛错，由调用方兜底。
    """
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=p.name + ".", suffix=".tmp", dir=str(p.parent))
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        os.replace(tmp, p)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def atomic_write_text(path: "str | Path", text: str) -> None:
    """atomic_write_bytes 的 UTF-8 文本封装。"""
    atomic_write_bytes(path, text.encode("utf-8"))


def recycle_file(path: str) -> None:
    """把文件移入系统回收站（误删可恢复）。失败抛 OSError 由调用方兜底。

    win32 走 SHFileOperationW（FO_DELETE + FOF_ALLOWUNDO，静默无确认框——
    确认交互由前端完成）；非 Windows 无回收站概念，回退 os.remove。
    """
    if sys.platform != "win32":
        os.remove(path)
        return
    import ctypes
    from ctypes import wintypes

    FO_DELETE = 0x3
    FOF_SILENT = 0x4
    FOF_NOCONFIRMATION = 0x10
    FOF_ALLOWUNDO = 0x40
    FOF_NOERRORUI = 0x400

    class SHFILEOPSTRUCTW(ctypes.Structure):
        _fields_ = [
            ("hwnd", wintypes.HWND),
            ("wFunc", wintypes.UINT),
            ("pFrom", wintypes.LPCWSTR),
            ("pTo", wintypes.LPCWSTR),
            ("fFlags", wintypes.USHORT),
            ("fAnyOperationsAborted", wintypes.BOOL),
            ("hNameMappings", wintypes.LPVOID),
            ("lpszProgressTitle", wintypes.LPCWSTR),
        ]

    # pFrom 必须以双 NUL 结尾
    op = SHFILEOPSTRUCTW(
        None,
        FO_DELETE,
        str(path) + "\0\0",
        None,
        FOF_ALLOWUNDO | FOF_SILENT | FOF_NOCONFIRMATION | FOF_NOERRORUI,
        False,
        None,
        None,
    )
    rc = ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op))
    if rc != 0:
        raise OSError(f"SHFileOperationW 失败，错误码 {rc}")
