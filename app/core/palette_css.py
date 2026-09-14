"""从运行时色板 CSS 解析导出用色值。

页面装入的 palettes.css 是色值真相；HTML 导出与测试都读这份文件，
不另维护一份手写色表。
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path
from typing import Optional

_VENDOR_PALETTES = (
    Path(__file__).resolve().parent.parent / "web" / "vendor" / "ryuuji" / "styles" / "palettes.css"
)

_BLOCK_RE = re.compile(
    r"html\[data-lang=\"([ab])\"\]\[data-palette=\"([ab]\d)\"\]\[data-theme=\"(light|dark)\"\][^{]*\{([^}]+)\}",
    re.MULTILINE,
)
_VAR_RE = re.compile(r"--sys-([a-z0-9-]+)\s*:\s*(#[0-9a-fA-F]{3,8})\s*;")

_EXPORT_KEYS = {
    "primary": "primary",
    "primary-deep": "deep",
    "bg": "bg",
    "text": "text",
    "text-2": "muted",
    "border": "border",
}


def _parse(css: str) -> dict[str, dict[str, dict[str, str]]]:
    out: dict[str, dict[str, dict[str, str]]] = {}
    for m in _BLOCK_RE.finditer(css or ""):
        pal, theme, body = m.group(2), m.group(3), m.group(4)
        vars_map = {k: v.lower() for k, v in _VAR_RE.findall(body)}
        mapped = {}
        for src, dst in _EXPORT_KEYS.items():
            if src in vars_map:
                mapped[dst] = vars_map[src]
        if mapped:
            out.setdefault(pal, {})[theme] = mapped
    return out


@lru_cache(maxsize=4)
def load_export_palettes(path: Optional[str] = None) -> dict[str, dict[str, dict[str, str]]]:
    """读取 palettes.css，得到 {palette: {theme: {primary,deep,bg,text,muted,border}}}。"""
    p = Path(path) if path else _VENDOR_PALETTES
    try:
        text = p.read_text(encoding="utf-8")
    except OSError:
        return {}
    return _parse(text)


def chrome_background(palette: str, theme: str) -> str:
    """窗口尚未绘制 HTML 时的底色，与当前色板 --sys-bg 一致。"""
    pals = load_export_palettes()
    mode = "dark" if theme == "dark" else "light"
    pal = palette if palette in pals else "a1"
    bg = ((pals.get(pal) or {}).get(mode) or {}).get("bg")
    if bg:
        return bg
    return "#111318" if mode == "dark" else "#eef2fc"


def duration_map(css: str) -> dict[str, str]:
    """从 tokens CSS 抽出 --sys-dur-1..5。"""
    found = re.findall(r"--sys-dur-([1-5])\s*:\s*([^;]+);", css or "")
    return {f"--sys-dur-{k}": v.strip() for k, v in found}


def token_value(css: str, name: str) -> str:
    m = re.search(rf"{re.escape(name)}\s*:\s*([^;]+);", css or "")
    return (m.group(1).strip() if m else "")
