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

OUT_DIR = ROOT / "tests" / "ui-shots"
OUT_DIR.mkdir(exist_ok=True)
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

MAIN_MD.write_text(
    """# 标题1

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
""",
    encoding="utf-8",
)


def J(s: str) -> str:
    return json.dumps(str(s), ensure_ascii=True)


# ----------------------------------------------------------------------------
# 截图
# ----------------------------------------------------------------------------
def shot(name: str) -> None:
    out = OUT_DIR / f"{name}.png"
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
    step(window, "01-home-light-card", sleep=1.2)
    step(window, "02-home-light-list",
         'document.querySelector("#repo-view-toggle [data-view=\\"list\\"]").click()')

    # —— 编辑器：亮色 sky（先开文件夹让文件树有内容，再开主文档） ——
    step(window, "03-editor-light-files",
         f"window.App.openPath({repo_js});window.App.openPath({main_js})", sleep=3.0)
    step(window, "04-editor-light-outline",
         'document.querySelector(".side-tab[data-panel=\\"outline\\"]").click()', sleep=1.0)

    # —— 编辑器：深色 vampire ——
    step(window, "05-editor-dark-outline",
         'document.getElementById("btn-theme").click()', sleep=1.5)
    step(window, "06-editor-dark-files",
         'document.querySelector(".side-tab[data-panel=\\"files\\"]").click()')

    # —— 回到亮色，浮层类 ——
    step(window, "07-ctx-editor",
         'document.getElementById("btn-theme").click();', sleep=1.5)
    window.evaluate_js(
        "(function(){var el=document.querySelector('.vditor-reset')||document.querySelector('#editor');"
        "var r=el.getBoundingClientRect();"
        "el.dispatchEvent(new MouseEvent('contextmenu',{bubbles:true,cancelable:true,"
        "clientX:r.x+Math.min(500,r.width/2),clientY:r.y+220,button:2}));return true;})()"
    )
    time.sleep(0.6)
    shot("07-ctx-editor")

    step(window, "08-ctx-tree",
         "window.SlashMenu.close();"
         "(function(){var el=document.querySelector('#file-tree .tree-item.file');if(!el)return false;"
         "var r=el.getBoundingClientRect();"
         "el.dispatchEvent(new MouseEvent('contextmenu',{bubbles:true,cancelable:true,"
         "clientX:r.x+60,clientY:r.y+8,button:2}));return true;})()")

    step(window, "09-palette",
         "document.dispatchEvent(new MouseEvent('click',{bubbles:true}));"
         "window.Palette.openFiles();", sleep=0.8)

    step(window, "10-find-bar",
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

    step(window, "11-settings-modal",
         'document.getElementById("find-close").click();document.getElementById("btn-settings").click()',
         sleep=0.9)

    step(window, "12-confirm-modal",
         'window.dispatchEvent(new KeyboardEvent("keydown",{key:"Escape",bubbles:true}));'
         'window.App.confirm({title:"删除文件", message:"确定要把「样式总览.md」移到回收站吗？", okText:"删除"})',
         sleep=0.8)

    # —— 首页深色 ——
    step(window, "13-home-dark",
         'document.getElementById("cf-cancel").click();'
         'document.getElementById("btn-theme").click();window.Home.show()', sleep=1.4)

    # —— 首页亮色（有最近记录，验证最近区布局） ——
    step(window, "13b-home-light-recent",
         'document.getElementById("btn-theme").click()', sleep=1.4)

    # —— 欢迎弹窗（亮色） ——
    step(window, "14-welcome-modal",
         'window.App.showWelcome()', sleep=1.2)

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
        "theme": "light",
        "palette_light": "sky",
        "palette_dark": "vampire",
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
