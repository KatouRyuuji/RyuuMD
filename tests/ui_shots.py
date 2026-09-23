# -*- coding: utf-8 -*-
"""RyuuMD UI 截图验证工具。

启动真实 pywebview 窗口（隔离 APPDATA，不污染用户配置），用 evaluate_js
依次切换界面状态，PowerShell PrintWindow(PW_RENDERFULLCONTENT) 截图，
输出到 tests/ui-shots/。用于设计系统/主题改动的视觉回归。

运行: python tests/ui_shots.py
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# 隔离配置：必须在 import app.core 之前设置
_TMP_APPDATA = tempfile.TemporaryDirectory(prefix="ryuumd-uishot-appdata-")
os.environ["APPDATA"] = _TMP_APPDATA.name

import webview  # noqa: E402

from main import start_webview  # noqa: E402

from app.core.api import Api  # noqa: E402
from app.core.config import Config  # noqa: E402

OUT_DIR = Path(os.environ.get("RYUUMD_UI_SHOT_OUT", str(ROOT / "tests" / "ui-shots")))
OUT_DIR.mkdir(parents=True, exist_ok=True)
SHOT_THEME = os.environ.get("RYUUMD_UI_SHOT_THEME", "light")
SHOT_PALETTE = os.environ.get("RYUUMD_UI_SHOT_PALETTE", "a1")
SHOT_PREFIX = f"{SHOT_PALETTE}-{SHOT_THEME}"
OTHER_THEME = "dark" if SHOT_THEME == "light" else "light"
PS1 = ROOT / "tests" / "ui_shot.ps1"
TITLE = "RyuuMD UIShot"

# ----------------------------------------------------------------------------
# 夹具：仓库目录 + 对齐参考图的主文档（标题1-6/加粗/代码块/引用/列表/todo）
# ----------------------------------------------------------------------------
_TMP = tempfile.TemporaryDirectory(prefix="ryuumd-uishot-files-")
TMP = Path(_TMP.name)
REPO = TMP / "shot-repo"
(REPO / "notes").mkdir(parents=True)
MAIN_MD = REPO / "样式总览.md"
(REPO / "入门指南.md").write_text("# 入门指南\n\n快速上手。\n", encoding="utf-8")
(REPO / "更新日志.md").write_text("# 更新日志\n\n- v1\n- v2\n", encoding="utf-8")
(REPO / "notes" / "随手记.md").write_text("# 随手记\n\n杂项。\n", encoding="utf-8")
(REPO / "模板").mkdir()
(REPO / "模板" / "日记.md").write_text("# {{date}}\n\n## 今天\n\n- [ ] 待办\n", encoding="utf-8")
(REPO / "模板" / "会议.md").write_text("# {{title}}\n\n## 议题\n\n## 决议\n", encoding="utf-8")

MAIN_MD.write_text(
    r"""# 标题1

## 标题2

### 标题3

#### 标题4

##### 标题5

###### 标题6

普通字体

**加粗字体**

```
这是代码块这是代码块这是代码块这是代码块这是代码块这是代码块
这是代码块这是代码块这是代码块这是代码块这是代码块这是代码块
```

> 这是引用这是引用这是引用这是引用这是引用这是引用
> 这是引用这是引用这是引用这是引用这是引用这是引用

1. 这是有序列表
2. 这是有序列表
3. 这是有序列表

- 这是无序列表
- 这是无序列表
- 这是无序列表

- [ ] 这是todo1
- [x] 这是todo2

## 表格、公式与链接

| 字段 | 示例 |
| --- | --- |
| 状态 | 完成 |

行内公式 $E=mc^2$ 与块公式：

$$
\int_0^1 x^2 \, dx = \frac{1}{3}
$$

双链 [[入门指南]] 与 [外部链接](https://example.com)。

## 图表

```mermaid
flowchart LR
  A[开始] --> B[完成]
```

#工作流 #设计评审
""",
    encoding="utf-8",
)


def J(s: str) -> str:
    return json.dumps(str(s), ensure_ascii=True)


# ----------------------------------------------------------------------------
# 截图
# ----------------------------------------------------------------------------
def shot(name: str) -> None:
    out = OUT_DIR / f"{SHOT_PREFIX}-{name}.png"
    r = subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(PS1),
         "-Title", TITLE, "-Out", str(out), "-OwnerPid", str(os.getpid())],
        capture_output=True, text=True, timeout=30,
    )
    ok = r.returncode == 0 and out.exists()
    print(("PASS " if ok else "FAIL ") + name + ("  " + r.stdout.strip() if ok else "  " + r.stderr.strip()[:200]))


def step(window, name: str, js: str = "", sleep: float = 0.7) -> None:
    if js:
        window.evaluate_js(js)
    time.sleep(sleep)
    # 截图前回读真实状态，便于核对「截图内容 = 预期状态」
    try:
        st = window.evaluate_js(
            "JSON.stringify({theme:document.documentElement.dataset.theme,"
            "palette:document.documentElement.dataset.palette,"
            "tab:(document.querySelector('.side-tab.active')||{}).dataset&&document.querySelector('.side-tab.active').dataset.panel,"
            "home:!!(window.Home&&window.Home.isOpen()),"
            "tree:document.querySelectorAll('#file-tree .tree-item').length})"
        )
        print("  state", name, st)
    except Exception as e:
        print("  state-read failed:", e)
    shot(name)


def _run(window) -> None:
    # 等待 JS 桥与应用就绪
    for _ in range(60):
        try:
            if window.evaluate_js("!!(window.App && window.App.openPath && window.Home)"):
                break
        except Exception:
            pass
        time.sleep(0.5)

    main_js = J(MAIN_MD)
    repo_js = J(REPO)

    # —— 首页 ——
    step(window, f"01-home-{SHOT_THEME}-card", sleep=1.2)
    step(window, f"02-home-{SHOT_THEME}-list",
         'document.querySelector("#repo-view-toggle [data-view=\\"list\\"]").click()')

    # —— 编辑器：亮色 sky（先开文件夹让文件树有内容，再开主文档） ——
    step(window, f"03-editor-{SHOT_THEME}-files",
         f"window.App.openPath({repo_js});window.App.openPath({main_js})", sleep=3.0)
    step(window, f"04-editor-{SHOT_THEME}-outline",
         'document.querySelector(".side-tab[data-panel=\\"outline\\"]").click()', sleep=1.0)

    # —— 编辑器：深色 vampire ——
    step(window, f"05-editor-{OTHER_THEME}-outline",
         'document.getElementById("btn-theme").click()', sleep=1.5)
    step(window, f"06-editor-{OTHER_THEME}-files",
         'document.querySelector(".side-tab[data-panel=\\"files\\"]").click()')

    # —— 回到亮色，浮层类 ——
    step(window, f"07-ctx-editor-{SHOT_THEME}",
         'document.getElementById("btn-theme").click();', sleep=1.5)
    window.evaluate_js(
        "(function(){var el=document.querySelector('.vditor-reset')||document.querySelector('#editor');"
        "var r=el.getBoundingClientRect();"
        "el.dispatchEvent(new MouseEvent('contextmenu',{bubbles:true,cancelable:true,"
        "clientX:r.x+Math.min(500,r.width/2),clientY:r.y+220,button:2}));return true;})()"
    )
    time.sleep(0.6)
    shot(f"07-ctx-editor-{SHOT_THEME}")

    step(window, f"08-ctx-tree-{SHOT_THEME}",
         "window.SlashMenu.close();"
         "(function(){var el=document.querySelector('#file-tree .tree-item.file');if(!el)return false;"
         "var r=el.getBoundingClientRect();"
         "el.dispatchEvent(new MouseEvent('contextmenu',{bubbles:true,cancelable:true,"
         "clientX:r.x+60,clientY:r.y+8,button:2}));return true;})()")

    step(window, f"09-palette-files-{SHOT_THEME}",
         "document.dispatchEvent(new MouseEvent('click',{bubbles:true}));"
         "window.Palette.openFiles();", sleep=0.8)
    step(window, f"09b-palette-commands-{SHOT_THEME}",
         "window.Palette.close();window.Palette.openCommands();", sleep=0.8)
    step(window, f"09c-search-title-{SHOT_THEME}",
         "window.Palette.close();window.Palette.openSearch();"
         f"var i=document.getElementById('pal-input');i.value={J('指南')};"
         "i.dispatchEvent(new Event('input',{bubbles:true}));", sleep=0.8)
    step(window, f"09d-search-content-{SHOT_THEME}",
         "window.Palette.close();window.Palette.openSearch();"
         "window.Palette.setSearchMode('content');"
         f"var i=document.getElementById('pal-input');i.value={J('代码块')};"
         "i.dispatchEvent(new Event('input',{bubbles:true}));", sleep=1.0)
    step(window, f"09e-search-semantic-{SHOT_THEME}",
         "window.Palette.close();window.Palette.openSearch();"
         "window.Palette.setSearchMode('semantic');"
         f"var i=document.getElementById('pal-input');i.value={J('总结知识')};"
         "i.dispatchEvent(new Event('input',{bubbles:true}));", sleep=0.8)
    for label, method in (("tasks", "openTasks"), ("tags", "openTags"),
                          ("broken", "openBroken"), ("orphans", "openOrphans"),
                          ("mentions", "openMentions")):
        step(window, f"09-index-{label}-{SHOT_THEME}",
             f"window.Palette.close();window.Palette.{method}();", sleep=0.8)

    step(window, f"10-find-bar-{SHOT_THEME}",
         "window.Palette.close();window.FindBar.open();"
         "document.getElementById('find-toggle-replace').click();"
         "document.getElementById('find-input').value='这是';"
         "document.getElementById('find-input').dispatchEvent(new Event('input'))")
    # 探针：find-row 子元素布局
    try:
        probe = window.evaluate_js(
            "JSON.stringify(Array.from(document.querySelectorAll('#find-bar .find-row:first-child > *'))"
            ".map(function(el){var r=el.getBoundingClientRect();var cs=getComputedStyle(el);"
            "return {id:el.id||el.className||el.tagName,x:Math.round(r.x),w:Math.round(r.width),disp:cs.display,vis:cs.visibility,op:cs.opacity}}))"
        )
        print("  find-row probe:", probe)
    except Exception as e:
        print("  probe failed:", e)

    step(window, f"11-settings-general-{SHOT_THEME}",
         'document.getElementById("find-close").click();document.getElementById("btn-settings").click()',
         sleep=0.9)
    step(window, f"11b-settings-appearance-{SHOT_THEME}",
         'document.querySelector(".settings-tab[data-tab=\\"appearance\\"]").click()', sleep=0.7)
    step(window, f"11c-settings-cloud-{SHOT_THEME}",
         'document.querySelector(".settings-tab[data-tab=\\"cloud\\"]").click();'
         'if(!document.getElementById("cloud-panel").classList.contains("show"))'
         'document.getElementById("cloud-enabled").click()', sleep=0.8)
    step(window, f"11d-settings-style-wolai-{SHOT_THEME}",
         'document.querySelector(".settings-tab[data-tab=\\"general\\"]").click();'
         'document.querySelector("#set-style [data-v=wolai]").click()', sleep=0.7)
    step(window, f"11e-settings-style-notion-{SHOT_THEME}",
         'document.querySelector("#set-style [data-v=notion]").click()', sleep=0.7)
    window.evaluate_js("window.Settings.close()")

    step(window, f"12-confirm-modal-{SHOT_THEME}",
         'window.dispatchEvent(new KeyboardEvent("keydown",{key:"Escape",bubbles:true}));'
         'window.App.confirm({title:"移入回收站", message:"确定要把「样式总览.md」移入回收站吗？", okText:"移入回收站", danger:true})',
         sleep=0.8)

    # —— 首页深色 ——
    step(window, f"13-home-{OTHER_THEME}",
         'document.getElementById("cf-cancel").click();'
         'document.getElementById("btn-theme").click();window.Home.show()', sleep=1.4)

    # —— 首页亮色（有最近记录，验证最近区布局） ——
    step(window, f"13b-home-{SHOT_THEME}-recent",
         'document.getElementById("btn-theme").click()', sleep=1.4)

    # —— 欢迎弹窗（亮色） ——
    step(window, f"14-welcome-modal-{SHOT_THEME}",
         'window.App.showWelcome()', sleep=1.2)

    # —— AI：设置 AI 标签页 ——
    step(window, f"15-settings-ai-{SHOT_THEME}",
         'window.dispatchEvent(new KeyboardEvent("keydown",{key:"Escape",bubbles:true}));'
         'document.getElementById("btn-settings").click();'
         'document.querySelector(".settings-tab[data-tab=\\"ai\\"]").click()', sleep=0.9)

    # —— AI：搜索面板 ask 回答框（未配置 Key 时展示错误态，构图参考） ——
    step(window, f"16-palette-ask-{SHOT_THEME}",
         'window.dispatchEvent(new KeyboardEvent("keydown",{key:"Escape",bubbles:true}));'
         'window.Palette.openSearch();'
         'var i=document.getElementById("pal-input");'
         'i.value="ask 什么是 WebDAV";'
         'i.dispatchEvent(new Event("input"))', sleep=1.6)

    # —— AI：右侧 AI 侧栏（编辑器态，未配置 Key 时空态） ——
    step(window, f"17-ai-sidebar-{SHOT_THEME}",
         'window.Palette.close();window.Home.hide();'
         'document.getElementById("btn-ai").click()', sleep=0.8)
    step(window, f"17b-sidebar-recent-{SHOT_THEME}",
         'document.getElementById("btn-ai").click();'
         'document.querySelector(".side-tab[data-panel=\\"recent\\"]").click()', sleep=0.8)
    step(window, f"17c-sidebar-links-{SHOT_THEME}",
         'document.querySelector(".side-tab[data-panel=\\"links\\"]").click()', sleep=0.8)
    step(window, f"17d-editor-source-{SHOT_THEME}",
         'document.querySelector(".side-tab[data-panel=\\"files\\"]").click();'
         'window.Editor.setMode("sv")', sleep=1.2)
    step(window, f"17e-editor-rendered-{SHOT_THEME}",
         'window.Editor.setMode("ir")', sleep=1.2)
    step(window, f"17f-daily-note-{SHOT_THEME}",
         'window.App.openDailyNote().then(function(r){if(r&&r.ok)window.App.openPath(r.path);})', sleep=1.8)
    step(window, f"17g-return-to-main-note-{SHOT_THEME}",
         f'window.App.openPath({main_js})', sleep=1.4)
    step(window, f"17h-context-menu-filter-{SHOT_THEME}",
         "window.SlashMenu.close();"
         "(function(){var el=document.querySelector('.vditor-reset');var r=el.getBoundingClientRect();"
         "el.dispatchEvent(new MouseEvent('contextmenu',{bubbles:true,cancelable:true,"
         "clientX:r.x+Math.min(500,r.width/2),clientY:r.y+180,button:2}));})();"
         "document.dispatchEvent(new KeyboardEvent('keydown',{key:'t',bubbles:true}));", sleep=0.8)

    # —— 结构操作柄（blockui.js）：悬停列表项出柄（先滚入视口，柄按块坐标定位） ——
    step(window, f"18-block-handle-{SHOT_THEME}",
         'document.getElementById("btn-ai").click();'
         'window.SlashMenu.close();'
         '(function(){var li=document.querySelectorAll(".vditor-reset > ul > li")[1];'
         'if(!li)return false;li.scrollIntoView({block:"center"});'
         'var r=li.getBoundingClientRect();'
         'li.dispatchEvent(new MouseEvent("mousemove",{bubbles:true,clientX:r.left+120,clientY:r.top+8}));'
         'return true;})()', sleep=1.0)

    # —— 柄菜单：点击开菜单 + 范围高亮 ——
    step(window, f"19-block-menu-{SHOT_THEME}",
         '(function(){var h=document.querySelector(".block-handle.on");'
         'if(h)h.click();return true;})()', sleep=0.8)

    # —— 拖拽落点：拖到柄下方 120px 的块（插入线 + 归属提示），截图后松手还原 ——
    step(window, f"20-block-drag-{SHOT_THEME}", "")
    window.evaluate_js(
        "window.SlashMenu.close();"
        "(function(){var h=document.querySelector('.block-handle.on');if(!h)return false;"
        "var hr=h.getBoundingClientRect();"
        "h.dispatchEvent(new MouseEvent('mousedown',{bubbles:true,cancelable:true,"
        "clientX:hr.left+6,clientY:hr.top+6}));"
        "document.dispatchEvent(new MouseEvent('mousemove',{bubbles:true,"
        "clientX:hr.left+300,clientY:hr.top+120}));"
        "return true;})()"
    )
    time.sleep(0.7)
    shot(f"20-block-drag-{SHOT_THEME}")
    # 松手完成移动，避免遗留拖拽状态
    window.evaluate_js(
        "(function(){var h=document.querySelector('.block-handle.on');"
        "var hr=h?h.getBoundingClientRect():{left:0,top:0};"
        "document.dispatchEvent(new MouseEvent('mouseup',{bubbles:true,"
        "clientX:hr.left+300,clientY:hr.top+120}));return true;})()"
    )

    print("done ->", OUT_DIR)
    window.destroy()


def main() -> int:
    # 关闭上次运行可能残留的同名窗口（PrintWindow 按标题枚举会命中旧窗口帧）
    subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(PS1),
         "-Title", TITLE, "-Close"],
        capture_output=True, timeout=15,
    )
    time.sleep(1.0)
    config = Config()
    config.update({
        "startup_page": "home",
        "last_folder": str(REPO),
        "theme": SHOT_THEME,
        "palette": SHOT_PALETTE,
        "welcome_shown": True,
    })
    api = Api(config)
    res = api.add_project(str(REPO), "")
    if not res.get("ok"):
        print("warn: add_project ->", res)
    index = str(ROOT / "app" / "web" / "index.html")
    window = webview.create_window(title=TITLE, url=index, js_api=api, width=1360, height=860)
    api.bind_window(window)
    start_webview(lambda: threading.Thread(target=_run, args=(window,), daemon=True).start(),
                  debug=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
