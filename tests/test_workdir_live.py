# -*- coding: utf-8 -*-
"""工作副本短烟：设置开关 / 打开仓库 / 保存至源 / 合并源。

完整窗口门禁已并入 tests/test_e2e.py（T98、T101–T118）。
本文件可单独快跑，默认 run_tests.py 不跑它。

隔离 APPDATA。运行: python tests/test_workdir_live.py
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

_TMP_APPDATA = tempfile.TemporaryDirectory(prefix="ryuumd-workdir-live-appdata-")
os.environ["APPDATA"] = _TMP_APPDATA.name

import webview  # noqa: E402

from app.core.api import Api  # noqa: E402
from app.core.config import Config  # noqa: E402
from main import start_webview  # noqa: E402

_TMP = tempfile.TemporaryDirectory(prefix="ryuumd-workdir-live-vault-")
VAULT = Path(_TMP.name)
(VAULT / "guide.md").write_text("# 指南\n\n源文件原文 WorkdirSourceToken\n", encoding="utf-8")

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


def _run(window) -> None:
    time.sleep(5.0)
    ev(
        window,
        "window.confirm=function(){return true;};window.alert=function(){};"
        "if(window.App&&window.App.confirm){"
        "window.App.confirm=function(){return Promise.resolve(true);};}"
        "if(window.App&&window.App.choice){"
        "window.App.choice=function(){return Promise.resolve('keep_source');};}"
        "(function(){var w=document.getElementById('welcome-mask');"
        "if(w&&w.classList.contains('open')){"
        "var b=document.getElementById('welcome-start');if(b)b.click();}})()",
    )

    ok, last = wait(
        window,
        "(function(){return !!(window.pywebview&&window.pywebview.api&&window.Home&&window.Settings&&window.App);})()",
        20,
    )
    record("W0 桥与模块就绪", ok, last)
    if not ok:
        window.destroy()
        return

    ev(window, "document.getElementById('btn-settings').click()")
    ok, last = wait(
        window,
        "(function(){var seg=document.getElementById('set-edit-mode');"
        "if(!seg)return 'no set-edit-mode';"
        "if(!seg.querySelector('[data-v=source]')||!seg.querySelector('[data-v=workdir]'))return 'missing opt';"
        "return true;})()",
    )
    record("W1 设置里有编辑方式开关", ok, last)
    ev(window, "(function(){var c=document.getElementById('set-close');if(c)c.click();})()")
    time.sleep(0.3)

    ev(
        window,
        "window.__wReady=0;"
        "window.pywebview.api.add_project(" + JV(str(VAULT)) + "," + JV("副本测试库") + ")"
        ".then(function(){if(window.Home&&window.Home.show)window.Home.show();window.__wReady=1;})"
        ".catch(function(e){window.__wReady=String(e&&e.message||e);});",
    )
    ok, last = wait(window, "window.__wReady===1", 15)
    record("W2 登记测试仓库", ok, last)

    ev(window, "(function(){var c=document.querySelector('#repo-container .repo-card,#repo-container .repo-row');if(c)c.click();})()")
    ok, last = wait(
        window,
        "(function(){if(window.Home&&window.Home.isOpen())return 'home still open';"
        "return true;})()",
        12,
    )
    record("W3 点击仓库进入编辑视图", ok, last)

    ev(
        window,
        "window.__wOpen=0;window.__wOpenPath='';"
        "window.App.openPath(" + JV(str(VAULT / "guide.md")) + ").then(function(){"
        "window.__wOpenPath=(window.App&&document.getElementById('sb-path').textContent)||'';"
        "window.__wOpen=1;});",
    )
    ok, last = wait(window, "window.__wOpen===1", 15)
    record("W4 打开指南文档", ok, last)

    ev(
        window,
        "window.__wSaved=0;"
        "(async function(){"
        "if(!(window.Editor&&window.Editor.isReady&&window.Editor.isReady())){"
        "if(window.App&&window.App.ensureEditor)await window.App.ensureEditor();}"
        "window.Editor.setValue(" + JV("# 指南\n\n只改副本 WorkdirCopyToken\n") + ");"
        "await window.App.save({silent:true});"
        "window.__wSaved=window.App.hasSavedFile()?1:2;})();",
    )
    ok, last = wait(window, "window.__wSaved===1", 12)
    src_after_save = (VAULT / "guide.md").read_text(encoding="utf-8")
    copy_ok = "WorkdirSourceToken" in src_after_save and "WorkdirCopyToken" not in src_after_save
    record("W5 保存只写副本、源文件未变", ok and copy_ok, "" if copy_ok else "saved=" + str(last) + " source=" + src_after_save[:80])

    ev(
        window,
        "window.__wPushed=0;"
        "(window.App.pushToSource?window.App.pushToSource():Promise.resolve())"
        ".then(function(){window.__wPushed=1;})"
        ".catch(function(){window.__wPushed=2;});",
    )
    wait(window, "window.__wPushed===1", 12)
    src_pushed = (VAULT / "guide.md").read_text(encoding="utf-8")
    record("W6 点「保存至源」后源文件含副本内容", "WorkdirCopyToken" in src_pushed, src_pushed[:80])

    (VAULT / "guide.md").write_text("# 指南\n\n源又更新了 WorkdirMergedToken\n", encoding="utf-8")
    ev(
        window,
        "window.__wMerged=0;"
        "(window.App.mergeFromSource?window.App.mergeFromSource():Promise.resolve())"
        ".then(function(){window.__wMerged=1;})"
        ".catch(function(){window.__wMerged=2;});",
    )
    wait(window, "window.__wMerged===1", 12)
    ok, last = wait(
        window,
        "(function(){var v=window.Editor&&window.Editor.getValue?window.Editor.getValue():'';"
        "return v.indexOf('WorkdirMergedToken')>=0?true:'ed='+v.slice(0,80);})()",
        10,
    )
    record("W7 改源后再点「合并源」编辑器跟上", ok, last)

    ev(window, "window.Palette.openCommands();")
    ok, last = wait(
        window,
        "(function(){var el=document.getElementById('pal-input');"
        "if(!el)return 'no input';"
        "el.value=" + JV("工作副本") + ";"
        "el.dispatchEvent(new Event('input',{bubbles:true}));"
        "var t=document.getElementById('pal-list').textContent;"
        "if(t.indexOf(" + JV("保存当前至源文件") + ")<0)return 'list='+t.slice(0,120);"
        "if(t.indexOf(" + JV("合并当前源文件") + ")<0)return 'no merge cmd';"
        "return true;})()",
    )
    record("W8 命令面板能搜到保存至源 / 合并源", ok, last)
    ev(window, "document.dispatchEvent(new KeyboardEvent('keydown',{key:'Escape',bubbles:true}))")

    ev(
        window,
        "window.__wWelcome=0;"
        "window.Welcome.show({style:'notion',edit_mode:'workdir'}).then(function(){window.__wWelcome=1;});",
    )
    time.sleep(0.8)
    ok, last = wait(
        window,
        "(function(){var m=document.getElementById('welcome-mask');"
        "if(!m.classList.contains('open'))return 'not open';"
        "if(m.querySelectorAll('.mode-opt').length!==2)return 'modes';"
        "var sel=m.querySelector('.mode-opt.selected');"
        "if(!sel||sel.dataset.mode!=='workdir')return 'sel='+(sel&&sel.dataset.mode);"
        "document.getElementById('welcome-start').click();"
        "return true;})()",
    )
    record("W9 欢迎页有编辑方式且记住工作副本", ok, last)

    ok, last = wait(
        window,
        "(function(){return document.body.classList.contains('is-workdir')?true:'no is-workdir';})()",
    )
    record("W10 body 保持工作副本态", ok, last)

    failed = [r for r in RESULTS if not r[1]]
    print("\n================ workdir live summary ================", flush=True)
    print(f"passed {len(RESULTS) - len(failed)}/{len(RESULTS)}", flush=True)
    for name, ok, detail in failed:
        print(f"  FAIL {name} -> {detail}", flush=True)
    window.destroy()


def main() -> int:
    config = Config()
    config.update({
        "startup_page": "home",
        "welcome_shown": True,
        "edit_mode": "workdir",
        "last_folder": str(VAULT),
    })
    api = Api(config)
    index = str(ROOT / "app" / "web" / "index.html")
    window = webview.create_window(
        title="RyuuMD Workdir Live",
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
