# -*- coding: utf-8 -*-
"""真实窗口：按用户路径点工具栏「搜索」，验证标题 / 内容 / 语义。

不走 Computer Use 桌面键鼠（本会话无该接口），而是启动真实 WebView2，
用 click / input / mousedown 驱动与人手相同的 DOM 事件，并走 pywebview 桥。
隔离 APPDATA，不碰用户配置。

运行: python tests/test_search_live.py
退出码: 0 = 全部通过; 1 = 有失败。
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_TMP_APPDATA = tempfile.TemporaryDirectory(prefix="ryuumd-search-live-appdata-")
os.environ["APPDATA"] = _TMP_APPDATA.name

import webview  # noqa: E402

from app.core.api import Api  # noqa: E402
from app.core.config import Config  # noqa: E402
from main import start_webview  # noqa: E402

_TMP = tempfile.TemporaryDirectory(prefix="ryuumd-search-live-vault-")
VAULT = Path(_TMP.name)
(VAULT / "docs").mkdir()
(VAULT / "WebDAV指南.md").write_text("# 其它\n\n正文里没有那个词\n", encoding="utf-8")
(VAULT / "plain.md").write_text("# 笔记\n\n这里讲解 OmegaContentToken 的用法\n", encoding="utf-8")
(VAULT / "docs" / "架构.md").write_text("# 架构\n\n使用 WebDAV 同步\n", encoding="utf-8")

RESULTS: list[tuple[str, bool, str]] = []


def JV(s: str) -> str:
    return json.dumps(s, ensure_ascii=True)


def ev(window, js: str):
    try:
        return window.evaluate_js(js)
    except Exception as e:  # noqa: BLE001
        return f"EVAL_ERR: {e}"


def wait(window, js: str, timeout: float = 12.0) -> tuple[bool, str]:
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        last = ev(window, js)
        if last is True or last == "true":
            return True, str(last)
        time.sleep(0.3)
    return False, str(last)[:400]


def record(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, ok, detail))
    print(("PASS" if ok else "FAIL"), name, ("" if ok else detail), flush=True)


def type_search(window, query: str) -> None:
    ev(
        window,
        "(function(){var el=document.getElementById('pal-input');"
        "if(!el)return;"
        "el.focus();el.value=" + JV(query) + ";"
        "el.dispatchEvent(new Event('input',{bubbles:true}));})()",
    )


def click_mode(window, mode: str) -> None:
    ev(
        window,
        "(function(){var b=document.querySelector("
        + JV("#pal-modes [data-mode=" + mode + "]")
        + ");"
        "if(!b)return;"
        "b.dispatchEvent(new MouseEvent('mousedown',{bubbles:true,cancelable:true}));})()",
    )


def _run(window) -> None:
    time.sleep(5.0)
    ev(
        window,
        "window.confirm=function(){return true;};window.alert=function(){};"
        "if(window.App&&window.App.confirm){"
        "window.App.confirm=function(){return Promise.resolve(true);};}"
        "(function(){var w=document.getElementById('welcome-mask');"
        "if(w&&w.classList.contains('open')){"
        "var b=document.getElementById('welcome-start');if(b)b.click();}})()",
    )

    ok, last = wait(
        window,
        "(function(){return !!(window.pywebview&&window.pywebview.api&&window.Home&&window.App);})()",
        20,
    )
    if not ok:
        record("L0 桥与首页模块就绪", False, last)
        window.destroy()
        return
    record("L0 桥与首页模块就绪", True, "")

    # 与人手一致：桥就绪后再登记仓库并刷新首页（勿只依赖启动前的 Python add）
    ev(
        window,
        "window.__liveReady=0;window.__liveAdd=null;"
        "window.pywebview.api.add_project(" + JV(str(VAULT)) + "," + JV("搜索测试库") + ")"
        ".then(function(r){window.__liveAdd=r;if(window.Home&&window.Home.show)window.Home.show();"
        "window.__liveReady=1;})"
        ".catch(function(e){window.__liveReady=String(e&&e.message||e);});",
    )
    ok, last = wait(window, "window.__liveReady===1", 15)
    if not ok:
        record("L1 首页出现测试仓库卡片", False, "add not settled:" + last)
        window.destroy()
        return

    ok, last = wait(
        window,
        "(function(){var n=document.querySelectorAll('#repo-container .repo-card,#repo-container .repo-row').length;"
        "return n>=1?true:'repos='+n+' add='+JSON.stringify(window.__liveAdd||null);})()",
        12,
    )
    record("L1 首页出现测试仓库卡片", ok, last)
    if not ok:
        ev(window, "window.App.openPath(" + JV(str(VAULT)) + ")")
        time.sleep(1.2)
    else:
        ev(window, "(function(){var c=document.querySelector('#repo-container .repo-card,#repo-container .repo-row');if(c)c.click();})()")

    ok, last = wait(
        window,
        "(function(){if(window.Home&&window.Home.isOpen())return 'home still open';"
        "return true;})()",
        12,
    )
    record("L2 点击仓库卡片进入编辑视图", ok, last)

    ev(window, "document.getElementById('btn-search').click()")
    ok, last = wait(
        window,
        "(function(){var m=document.getElementById('palette-mask');"
        "var modes=document.getElementById('pal-modes');"
        "if(!m||!m.classList.contains('open'))return 'not open';"
        "if(!modes||modes.hidden)return 'no modes';"
        "return true;})()",
    )
    record("L3 点击工具栏搜索：面板与三模式可见", ok, last)

    type_search(window, "WebDAV")
    time.sleep(0.5)
    ok, last = wait(
        window,
        "(function(){var t=document.getElementById('pal-list').textContent;"
        "if(t.indexOf(" + JV("指南") + ")<0)return 'list='+t.slice(0,120);"
        "if(t.indexOf(" + JV("标题") + ")<0)return 'no title badge:'+t.slice(0,80);"
        "return true;})()",
    )
    record("L4 标题模式输入 WebDAV：命中《WebDAV指南》", ok, last)

    click_mode(window, "content")
    type_search(window, "OmegaContentToken")
    time.sleep(0.5)
    ok, last = wait(
        window,
        "(function(){var t=document.getElementById('pal-list').textContent;"
        "if(t.indexOf('plain.md')<0)return 'list='+t.slice(0,120);"
        "if(t.indexOf(" + JV("内容") + ")<0)return 'no content badge:'+t.slice(0,80);"
        "if(t.indexOf(" + JV("指南") + ")>=0)return 'title leak:'+t.slice(0,80);"
        "return true;})()",
    )
    record("L5 点「内容」并输入 OmegaContentToken：只命中 plain.md", ok, last)

    click_mode(window, "semantic")
    type_search(window, "ideas about distributed consensus")
    time.sleep(0.4)
    ok, last = wait(
        window,
        "(function(){var empty=document.getElementById('pal-empty');"
        "var t=(empty&&empty.style.display!=='none')?empty.textContent:'';"
        "if(t.indexOf('Enter')<0 && t.indexOf(" + JV("语义") + ")<0)"
        "return 'empty='+t+' list='+document.getElementById('pal-list').textContent.slice(0,80);"
        "return true;})()",
    )
    record("L6 点「语义」输入后提示按 Enter（不自动打模型）", ok, last)

    ev(
        window,
        "document.getElementById('pal-input').dispatchEvent("
        "new KeyboardEvent('keydown',{key:'Enter',bubbles:true}));",
    )
    time.sleep(1.2)
    ok, last = wait(
        window,
        "(function(){var empty=document.getElementById('pal-empty');"
        "var t=(empty&&empty.style.display!=='none')?empty.textContent:'';"
        "if(!t)return 'no error text';"
        "if(t.indexOf('API')<0 && t.indexOf('Key')<0 && t.indexOf('模型')<0"
        "&& t.indexOf('配置')<0 && t.indexOf(" + JV("失败") + ")<0)"
        "return 'err='+t;"
        "return true;})()",
        10,
    )
    record("L7 语义按 Enter 且未配置 AI：显示明确错误", ok, last)

    ev(window, "document.dispatchEvent(new KeyboardEvent('keydown',{key:'Escape',bubbles:true}))")
    ok, last = wait(window, "!document.getElementById('palette-mask').classList.contains('open')")
    record("L8 Esc 关闭搜索面板", ok, last)

    failed = [r for r in RESULTS if not r[1]]
    print("\n================ search live summary ================", flush=True)
    print(f"passed {len(RESULTS) - len(failed)}/{len(RESULTS)}", flush=True)
    for name, ok, detail in failed:
        print(f"  FAIL {name} -> {detail}", flush=True)
    window.destroy()


def main() -> int:
    config = Config()
    config.update({
        "startup_page": "home",
        "welcome_shown": True,
        "last_folder": str(VAULT),
    })
    api = Api(config)
    added = api.projects.add(str(VAULT), "搜索测试库")
    if not added.get("ok"):
        print("无法注册测试仓库", added, flush=True)
        return 1
    index = str(ROOT / "app" / "web" / "index.html")
    window = webview.create_window(
        title="RyuuMD Search Live",
        url=index,
        js_api=api,
        width=1200,
        height=800,
    )
    api.bind_window(window)
    start_webview(lambda: threading.Thread(target=_run, args=(window,), daemon=True).start())
    failed = [r for r in RESULTS if not r[1]]
    return 1 if failed or not RESULTS else 0


if __name__ == "__main__":
    sys.exit(main())
