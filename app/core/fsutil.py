"""文件系统共享常量与工具（api / projects 共用，独立成模块避免循环导入）。"""

from __future__ import annotations

import os

# 视为 markdown 的扩展名
MD_EXTS = {".md", ".markdown", ".mdown", ".mkd", ".mdx"}

# 扫描忽略项：隐藏目录（以 . 开头）一律跳过，这里列出的是
# 不带点前缀的常见依赖/构建产物目录（可能包含数万文件，扫描即卡死）
IGNORE_DIRS = {
    ".git", ".obsidian", ".idea", ".vscode", ".venv",
    "node_modules", "__pycache__", "venv", "dist", "build", "target",
}


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
                        if entry.name.startswith(".") or entry.name in IGNORE_DIRS:
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
