"""仓库内 Markdown 索引、全文搜索、双向链接解析。

扫描带深度/文件数预算，跳过隐藏目录、依赖目录与 Typora 式 `*.assets` 配图目录，
保证大仓库上快速打开 / 命令面板不会卡死。
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Iterator

from .fsutil import IGNORE_DIRS, MD_EXTS, skip_dir_name

MAX_INDEX_FILES = 2000
MAX_INDEX_DEPTH = 8
MAX_FILE_READ = 256 * 1024
MAX_HITS = 50
SNIPPET_LEN = 160


def iter_md_files(
    root: str | Path,
    max_files: int = MAX_INDEX_FILES,
    max_depth: int = MAX_INDEX_DEPTH,
) -> Iterator[Path]:
    """深度优先列出仓库内 markdown 文件，超预算即停止。"""
    root_p = Path(root)
    if not root_p.is_dir():
        return
    yielded = 0
    stack: list[tuple[str, int]] = [(str(root_p), 0)]
    while stack:
        directory, depth = stack.pop()
        try:
            with os.scandir(directory) as it:
                entries = list(it)
        except OSError:
            continue
        entries.sort(key=lambda e: (not e.is_dir(follow_symlinks=False), e.name.lower()))
        for entry in entries:
            if yielded >= max_files:
                return
            if entry.is_dir(follow_symlinks=False):
                if skip_dir_name(entry.name):
                    continue
                if depth < max_depth:
                    stack.append((entry.path, depth + 1))
            elif os.path.splitext(entry.name)[1].lower() in MD_EXTS:
                yielded += 1
                yield Path(entry.path)


def _rel(root: Path, path: Path) -> str:
    try:
        return str(path.relative_to(root)).replace("\\", "/")
    except ValueError:
        return path.name


def list_md_files(root: str, query: str = "", limit: int = 80) -> dict[str, Any]:
    """快速打开用的文件名索引。query 匹配文件名或相对路径（大小写不敏感）。"""
    root_p = Path(root)
    if not root_p.is_dir():
        return {"ok": False, "error": "文件夹不存在", "items": []}
    q = (query or "").strip().lower()
    items: list[dict[str, Any]] = []
    scanned = 0
    truncated = False
    for p in iter_md_files(root_p):
        scanned += 1
        rel = _rel(root_p, p)
        hay = (p.name + " " + rel + " " + p.stem).lower()
        if q and q not in hay:
            continue
        items.append(
            {
                "path": str(p),
                "name": p.name,
                "stem": p.stem,
                "rel": rel,
            }
        )
        if len(items) >= limit:
            truncated = True
            break
    if scanned >= MAX_INDEX_FILES:
        truncated = True
    items.sort(key=lambda it: it["name"].lower())
    return {"ok": True, "items": items, "truncated": truncated}


def search_vault(root: str, query: str, max_hits: int = MAX_HITS) -> dict[str, Any]:
    """文件名优先、其次正文首条命中。每文件最多一条正文命中，控制返回体积。"""
    root_p = Path(root)
    if not root_p.is_dir():
        return {"ok": False, "error": "文件夹不存在", "hits": []}
    q = (query or "").strip()
    if not q:
        return {"ok": True, "hits": [], "truncated": False}
    q_l = q.lower()
    hits: list[dict[str, Any]] = []
    seen: set[str] = set()
    truncated = False

    def add(kind: str, p: Path, line: int, snippet: str) -> bool:
        key = str(p)
        if key in seen and kind != "content":
            return False
        if kind == "name":
            seen.add(key)
        hits.append(
            {
                "kind": kind,
                "path": str(p),
                "name": p.name,
                "rel": _rel(root_p, p),
                "line": line,
                "snippet": snippet[:SNIPPET_LEN],
            }
        )
        return len(hits) >= max_hits

    for p in iter_md_files(root_p):
        rel = _rel(root_p, p)
        if q_l in p.name.lower() or q_l in rel.lower():
            if add("name", p, 0, rel):
                return {"ok": True, "hits": hits, "truncated": True}

    for p in iter_md_files(root_p):
        try:
            text = p.read_text(encoding="utf-8", errors="replace")[:MAX_FILE_READ]
        except OSError:
            continue
        for i, line in enumerate(text.splitlines(), 1):
            if q_l in line.lower():
                snippet = line.strip()
                if add("content", p, i, snippet):
                    return {"ok": True, "hits": hits, "truncated": True}
                break
    if len(hits) >= max_hits:
        truncated = True
    return {"ok": True, "hits": hits, "truncated": truncated}


def parse_wikilink(raw: str) -> tuple[str, str]:
    """解析 `Name|别名` / `Name#标题`，返回 (目标名, 标题锚点)。"""
    text = (raw or "").strip()
    if "|" in text:
        text = text.split("|", 1)[0].strip()
    heading = ""
    if "#" in text:
        text, heading = text.split("#", 1)
        text, heading = text.strip(), heading.strip()
    return text, heading


def resolve_wikilink(
    root: str,
    name: str,
    current_file: str = "",
) -> dict[str, Any]:
    """按 Obsidian 习惯解析 [[wikilink]] / 相对 md 链接。

    优先级：当前文件旁相对路径 → 仓库根相对路径 → 全库文件名/词干精确匹配
    （同目录优先）。找不到时 exists=False，suggested 为建议创建路径。
    """
    root_p = Path(root) if root else Path()
    target, heading = parse_wikilink(name)
    if not target:
        return {"ok": False, "error": "链接为空"}

    def as_md(p: Path) -> Path:
        if p.suffix.lower() in MD_EXTS:
            return p
        if p.suffix:
            return p
        return p.with_suffix(".md")

    candidates: list[Path] = []
    if current_file:
        cur = Path(current_file)
        if cur.parent.is_dir():
            candidates.append(as_md(cur.parent / target))
    if root and root_p.is_dir():
        candidates.append(as_md(root_p / target))

    for cand in candidates:
        try:
            if cand.is_file():
                return {
                    "ok": True,
                    "exists": True,
                    "path": str(cand.resolve()),
                    "heading": heading,
                    "name": cand.name,
                }
        except OSError:
            continue

    stem = Path(target).stem if Path(target).suffix.lower() in MD_EXTS else Path(target).name
    stem_l = stem.lower()
    matches: list[Path] = []
    scan_root = root_p if root and root_p.is_dir() else (
        Path(current_file).parent if current_file else None
    )
    if scan_root and scan_root.is_dir():
        for p in iter_md_files(scan_root):
            if p.stem.lower() == stem_l or p.name.lower() == (stem + p.suffix).lower():
                matches.append(p)

    if matches:
        prefer_dir = ""
        if current_file:
            prefer_dir = os.path.normcase(str(Path(current_file).parent))
        matches.sort(
            key=lambda p: (
                os.path.normcase(str(p.parent)) != prefer_dir,
                p.name.lower(),
            )
        )
        chosen = matches[0]
        return {
            "ok": True,
            "exists": True,
            "path": str(chosen.resolve()),
            "heading": heading,
            "name": chosen.name,
            "alts": [str(p) for p in matches[1:6]],
        }

    if current_file:
        suggested = str(as_md(Path(current_file).parent / stem))
    elif root and root_p.is_dir():
        suggested = str(as_md(root_p / stem))
    else:
        suggested = stem + ".md"
    return {
        "ok": True,
        "exists": False,
        "path": "",
        "heading": heading,
        "name": Path(suggested).name,
        "suggested": suggested,
    }


WIKI_RE = re.compile(r"\[\[([^\]|#]+)(?:[#|][^\]]*)?\]\]")
MD_LINK_RE = re.compile(r"\[[^\]]*\]\(([^)]+)\)")


def _link_targets(target_path: str) -> set[str]:
    p = Path(target_path)
    names = {p.stem.lower(), p.name.lower()}
    if p.suffix.lower() in MD_EXTS:
        names.add(p.name.lower().replace(p.suffix.lower(), ""))
    return names


def find_backlinks(root: str, target_path: str, max_hits: int = 40) -> dict[str, Any]:
    """扫描仓库，找出用 [[wikilink]] 或相对 md 链接指向 target 的笔记。"""
    root_p = Path(root)
    if not root_p.is_dir() or not target_path:
        return {"ok": True, "hits": []}
    names = _link_targets(target_path)
    want = os.path.normcase(str(Path(target_path)))
    hits: list[dict[str, Any]] = []
    truncated = False
    for p in iter_md_files(root_p):
        if os.path.normcase(str(p)) == want:
            continue
        try:
            text = p.read_text(encoding="utf-8", errors="replace")[:MAX_FILE_READ]
        except OSError:
            continue
        matched = False
        for m in WIKI_RE.finditer(text):
            raw = m.group(1).strip().replace("\\", "/")
            key = Path(raw).name.lower()
            stem = Path(raw).stem.lower() if Path(raw).suffix.lower() in MD_EXTS else Path(raw).name.lower()
            if key in names or stem in names or raw.lower() in names:
                matched = True
                break
        if not matched:
            for m in MD_LINK_RE.finditer(text):
                href = (m.group(1) or "").split("#", 1)[0].strip()
                if not href or href.startswith(("http://", "https://", "mailto:")):
                    continue
                key = Path(href.replace("\\", "/")).name.lower()
                if key in names or Path(key).stem.lower() in names:
                    matched = True
                    break
        if not matched:
            continue
        hits.append(
            {
                "path": str(p),
                "name": p.name,
                "rel": _rel(root_p, p),
            }
        )
        if len(hits) >= max_hits:
            truncated = True
            break
    return {"ok": True, "hits": hits, "truncated": truncated}
