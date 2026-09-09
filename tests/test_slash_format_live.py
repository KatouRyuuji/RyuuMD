# -*- coding: utf-8 -*-
"""真实窗口：空行 / 选格式后再输入，是否还留在该格式。

复现用户路径：空文档 → 斜杠选一级标题/列表/引用 → 再键入内容或回车。
对照路径：已有正文最前面插 / 再转标题。

运行: python tests/test_slash_format_live.py
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

_TMP_APPDATA = tempfile.TemporaryDirectory(prefix="ryuumd-slash-live-appdata-")
os.environ["APPDATA"] = _TMP_APPDATA.name

import webview  # noqa: E402

from app.core.api import Api  # noqa: E402
from app.core.config import Config  # noqa: E402
from main import start_webview  # noqa: E402

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
    return False, str(last)[:800]


def record(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, ok, detail))
    print(("PASS" if ok else "FAIL"), name, ("" if ok else detail), flush=True)


DUMP = r"""
(function(){
  var panel=document.querySelector('#editor .vditor-ir .vditor-reset');
  if(!panel)return {err:'no panel'};
  var tops=[].filter.call(panel.children,function(c){return c.nodeType===1;});
  var sel=window.getSelection();
  var node=sel&&sel.rangeCount?sel.getRangeAt(0).startContainer:null;
  var el=node&&(node.nodeType===1?node:node.parentElement);
  var block=el;
  while(block&&block.parentElement!==panel)block=block.parentElement;
  var marker=block&&block.querySelector&&block.querySelector('.vditor-ir__marker--heading');
  var cs=marker?getComputedStyle(marker):null;
  return {
    val: window.Editor.getValue(),
    tops: tops.map(function(c){return c.tagName+':'+(c.getAttribute('data-type')||'')+':'+JSON.stringify((c.textContent||'').replace(/\u200b/g,'\\u200b').slice(0,40));}),
    caretTag: block?block.tagName:'none',
    caretHtml: block?block.innerHTML.slice(0,240):'',
    markerDisplay: cs?cs.display:'none-marker',
    markerW: cs?cs.width:'',
    inMarker: !!(marker&&node&&marker.contains(node))
  };
})()
"""


def dump(window) -> str:
    raw = ev(window, DUMP)
    if isinstance(raw, str) and raw.startswith("EVAL"):
        return raw
    text = json.dumps(raw, ensure_ascii=True)
    return text[:700]


def boot(window) -> bool:
    time.sleep(4.5)
    ev(
        window,
        "window.confirm=function(){return true;};window.alert=function(){};"
        "if(window.App&&window.App.confirm){"
        "window.App.confirm=function(){return Promise.resolve(true);};}"
        "(function(){var w=document.getElementById('welcome-mask');"
        "if(w&&w.classList.contains('open')){"
        "var b=document.getElementById('welcome-start');if(b)b.click();}})()",
    )
    ev(window, "window.Home&&window.Home.hide&&window.Home.hide();window.App&&window.App.ensureEditor&&window.App.ensureEditor();")
    ok, last = wait(
        window,
        "!!(window.Editor&&window.Editor.isReady()&&window.runCommand&&window.COMMANDS)",
        20,
    )
    record("L0 编辑器就绪", ok, last)
    return ok


def focus_empty(window) -> None:
    ev(
        window,
        "window.Editor.setValue('');"
        "setTimeout(function(){"
        "window.Editor.focus();"
        "var p=document.querySelector('#editor .vditor-ir .vditor-reset > p')||"
        "document.querySelector('#editor .vditor-ir .vditor-reset');"
        "if(!p)return;"
        "var r=document.createRange();r.selectNodeContents(p);r.collapse(true);"
        "var s=window.getSelection();s.removeAllRanges();s.addRange(r);"
        "},80);",
    )
    time.sleep(0.45)


def slash_choose(window, title: str) -> None:
    ev(
        window,
        "(function(){"
        "var p=document.querySelector('#editor .vditor-ir .vditor-reset > p')||"
        "document.querySelector('#editor .vditor-ir [contenteditable=\"true\"]');"
        "if(!p)return;"
        "p.focus();"
        "document.execCommand('insertText',false,'/');"
        "})()",
    )
    time.sleep(0.35)
    ev(
        window,
        "(function(){"
        "var it=[].slice.call(document.querySelectorAll('#slash-menu .slash-item'))"
        ".find(function(el){var t=el.querySelector('.si-title');return t&&t.textContent==="
        + JV(title)
        + ";});"
        "if(!it){window.__slashMiss=" + JV(title) + ";return;}"
        "it.dispatchEvent(new MouseEvent('mousedown',{bubbles:true,cancelable:true}));"
        "})()",
    )
    time.sleep(0.55)


def type_text(window, text: str) -> None:
    ev(
        window,
        "(function(){"
        "var el=document.querySelector('#editor .vditor-ir .vditor-reset');"
        "if(el)el.focus();"
        "document.execCommand('insertText',false," + JV(text) + ");"
        "})()",
    )
    time.sleep(0.45)


def press_enter(window) -> None:
    ev(
        window,
        "(function(){"
        "var el=document.querySelector('#editor .vditor-ir .vditor-reset');"
        "if(el)el.focus();"
        "var opts={key:'Enter',code:'Enter',keyCode:13,which:13,bubbles:true,cancelable:true};"
        "document.dispatchEvent(new KeyboardEvent('keydown',opts));"
        "})()",
    )
    time.sleep(0.45)


def _run(window) -> None:
    try:
        if not boot(window):
            window.destroy()
            return

        # —— 对照：已有内容最前面插 / 转标题 ——
        ev(window, "window.Editor.setValue(" + JV("已有段落 XYZ\n") + ");")
        time.sleep(0.4)
        ev(
            window,
            "(function(){"
            "var p=[].slice.call(document.querySelectorAll('#editor .vditor-ir .vditor-reset > p'))"
            ".find(function(x){return x.textContent.indexOf('XYZ')>=0;});"
            "if(!p)return;"
            "var r=document.createRange();r.selectNodeContents(p);r.collapse(true);"
            "var s=window.getSelection();s.removeAllRanges();s.addRange(r);"
            "document.execCommand('insertText',false,'/');"
            "})()",
        )
        time.sleep(0.35)
        ev(
            window,
            "(function(){"
            "var it=[].slice.call(document.querySelectorAll('#slash-menu .slash-item'))"
            ".find(function(el){var t=el.querySelector('.si-title');return t&&t.textContent==="
            + JV("一级标题")
            + ";});"
            "if(it)it.dispatchEvent(new MouseEvent('mousedown',{bubbles:true,cancelable:true}));"
            "})()",
        )
        time.sleep(0.6)
        ok, last = wait(
            window,
            "(function(){var v=window.Editor.getValue();"
            "var h=document.querySelector('#editor .vditor-ir .vditor-reset > h1');"
            "return (v.indexOf('# 已有段落 XYZ')>=0&&h)?true:'v='+JSON.stringify(v);})()",
            8,
        )
        record("L1 已有内容行首 / 转一级标题", ok, last if not ok else "")

        # —— 空行：/ 选一级标题，看壳 ——
        focus_empty(window)
        slash_choose(window, "一级标题")
        snap = dump(window)
        print("DUMP after empty /h1:", snap, flush=True)
        ok, last = wait(
            window,
            "(function(){var h=document.querySelector('#editor .vditor-ir .vditor-reset > h1');"
            "return h?true:'no h1 val='+JSON.stringify(window.Editor.getValue())+' tops='+"
            "JSON.stringify((function(){var p=document.querySelector('#editor .vditor-ir .vditor-reset');"
            "return p?[].map.call(p.children,function(c){return c.tagName;}).slice(0,6):[];})());})()",
            6,
        )
        record("L2 空行 / 选一级标题后仍是 h1", ok, last if not ok else snap)

        # —— 再输入内容 ——
        type_text(window, "空行标题ABC")
        snap2 = dump(window)
        print("DUMP after type in h1:", snap2, flush=True)
        ok, last = wait(
            window,
            "(function(){var v=window.Editor.getValue();"
            "var h=document.querySelector('#editor .vditor-ir .vditor-reset > h1');"
            "if(!h||h.textContent.indexOf(" + JV("空行标题ABC") + ")<0)return 'dom='+(h?h.tagName:'none')+' v='+JSON.stringify(v);"
            "if(v.indexOf('# 空行标题ABC')<0&&v.indexOf('#空行标题ABC')<0)"
            "return 'src lost heading: '+JSON.stringify(v);"
            "return true;})()",
            6,
        )
        record("L3 空行标题里键入后源码仍是标题", ok, last if not ok else "")

        # —— 空行 / 选标题后立刻回车，再输入 ——
        focus_empty(window)
        slash_choose(window, "一级标题")
        press_enter(window)
        type_text(window, "回车后标题DEF")
        snap3 = dump(window)
        print("DUMP after enter then type:", snap3, flush=True)
        ok, last = wait(
            window,
            "(function(){var v=window.Editor.getValue();"
            "var hs=document.querySelectorAll('#editor .vditor-ir .vditor-reset > h1');"
            "var hit=[].some.call(hs,function(h){return h.textContent.indexOf('回车后标题DEF')>=0;});"
            "return hit||v.indexOf('# 回车后标题DEF')>=0||v.indexOf('#回车后标题DEF')>=0"
            "?true:'v='+JSON.stringify(v);})()",
            6,
        )
        record("L4 空行选标题后回车再输入仍在标题", ok, last if not ok else "")

        # —— 空行无序列表再输入 ——
        focus_empty(window)
        slash_choose(window, "无序列表")
        type_text(window, "列表项GHI")
        snap4 = dump(window)
        print("DUMP after ul type:", snap4, flush=True)
        ok, last = wait(
            window,
            "(function(){var v=window.Editor.getValue();"
            "var ul=document.querySelector('#editor .vditor-ir .vditor-reset > ul');"
            "return (ul&&ul.textContent.indexOf('列表项GHI')>=0)||v.indexOf('- 列表项GHI')>=0"
            "?true:'v='+JSON.stringify(v);})()",
            6,
        )
        record("L5 空行 / 选无序列表后键入仍是列表", ok, last if not ok else "")

        # —— 标题写完后回车应另起正文（按住只作用于空壳） ——
        focus_empty(window)
        slash_choose(window, "一级标题")
        type_text(window, "写完标题JKL")
        time.sleep(0.25)
        # 空壳回车由 holdEmptyEnter 拦下；写完后浏览器会走 insertParagraph
        ev(window, "document.execCommand('insertParagraph');")
        time.sleep(0.35)
        type_text(window, "这是正文MNO")
        snap5 = dump(window)
        print("DUMP after filled heading enter:", snap5, flush=True)
        ok, last = wait(
            window,
            "(function(){var v=window.Editor.getValue();"
            "if(v.indexOf('# 写完标题JKL')<0&&v.indexOf('#写完标题JKL')<0)"
            "return 'heading lost '+JSON.stringify(v);"
            "if(v.indexOf('这是正文MNO')<0)return 'body lost '+JSON.stringify(v);"
            "var p=document.querySelector('#editor .vditor-ir .vditor-reset > p');"
            "if(!p||p.textContent.indexOf('这是正文MNO')<0)return 'no p '+JSON.stringify(v);"
            "return true;})()",
            6,
        )
        record("L6 标题写完后回车另起正文", ok, last if not ok else "")

        focus_empty(window)
        slash_choose(window, "引用")
        type_text(window, "引用内容PQR")
        snap6 = dump(window)
        print("DUMP after quote type:", snap6, flush=True)
        ok, last = wait(
            window,
            "(function(){var v=window.Editor.getValue();"
            "return v.indexOf('> 引用内容PQR')>=0||v.indexOf('>引用内容PQR')>=0"
            "?true:'v='+JSON.stringify(v);})()",
            6,
        )
        record("L7 空行 / 选引用后键入仍是引用", ok, last if not ok else "")

        # —— 空标题壳按住回车不得吞掉查找条 Enter ——
        focus_empty(window)
        slash_choose(window, "一级标题")
        ok, last = wait(
            window,
            "(function(){"
            "var el=document.createElement('input');"
            "el.id='hold-enter-probe';"
            "document.body.appendChild(el);"
            "el.focus();"
            "var ev=new KeyboardEvent('keydown',{key:'Enter',code:'Enter',"
            "keyCode:13,which:13,bubbles:true,cancelable:true});"
            "el.dispatchEvent(ev);"
            "var swallowed=ev.defaultPrevented;"
            "el.remove();"
            "return swallowed?'enter swallowed':true;})()",
            6,
        )
        record("L8 空标题壳时编辑器外 Enter 不被按住", ok, last if not ok else "")
    finally:
        failed = [r for r in RESULTS if not r[1]]
        print("\n================ slash-format live ================")
        print(f"passed {len(RESULTS) - len(failed)}/{len(RESULTS)}")
        for name, ok, detail in failed:
            print(f"  FAIL {name} -> {detail}")
        sys.stdout.flush()
        window.destroy()


def main() -> int:
    config = Config()
    config.update({"welcome_shown": True, "startup_page": "editor"})
    api = Api(config)
    index = str(ROOT / "app" / "web" / "index.html")
    window = webview.create_window(
        title="RyuuMD Slash Format Live", url=index, js_api=api, width=1100, height=760
    )
    api.bind_window(window)
    start_webview(lambda: threading.Thread(target=_run, args=(window,), daemon=True).start(), debug=False)
    return 1 if any(not ok for _, ok, _ in RESULTS) else 0


if __name__ == "__main__":
    sys.exit(main())
