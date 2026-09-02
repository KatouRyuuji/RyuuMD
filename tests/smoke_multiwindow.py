# -*- coding: utf-8 -*-
"""回归：修复后的多窗口链路。
1) second_launch=new：转发空路径 → 开新窗口，桥可用；
2) second_launch=focus：转发空路径 → 不开新窗（窗口数不变）；
3) 转发带文件路径 → 始终开新窗口。
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_TMP_APPDATA = tempfile.TemporaryDirectory(prefix="ryuumd-regress-appdata-")
os.environ["APPDATA"] = _TMP_APPDATA.name
_TMP = tempfile.TemporaryDirectory(prefix="ryuumd-regress-files-")
MD = Path(_TMP.name) / "note.md"
MD.write_text("# hi\n", encoding="utf-8")

import webview  # noqa: E402

from app.core.config import Config  # noqa: E402
from app.core.singleton import InstanceServer  # noqa: E402
from main import WindowManager, start_webview  # noqa: E402

PASS = True


def check(label, cond):
    global PASS
    print(("PASS " if cond else "FAIL ") + label, flush=True)
    if not cond:
        PASS = False


def forward(path):
    r = subprocess.run(
        [sys.executable, "-c",
         "import sys; sys.path.insert(0, %r);"
         "from app.core.config import Config;"
         "from app.core.singleton import try_forward;"
         "print(try_forward(Config(), %r))" % (str(ROOT), path)],
        capture_output=True, text=True, env=dict(os.environ), timeout=30,
    )
    ok = "True" in r.stdout
    print(f"forward({path!r}) -> {ok}", flush=True)
    return ok


def probe(win, label):
    win.evaluate_js(
        "window.__probe='pending';"
        "window.pywebview&&window.pywebview.api"
        "?window.pywebview.api.get_config().then(function(c){window.__probe='call-ok';})"
        ".catch(function(e){window.__probe='call-err';})"
        ":window.__probe='no-bridge';'started'")
    for _ in range(40):
        time.sleep(0.5)
        v = win.evaluate_js("window.__probe")
        if v and v != "pending":
            check(f"{label} bridge ({v})", v == "call-ok")
            return
    check(f"{label} bridge TIMEOUT", False)


def work(config):
    time.sleep(6)
    check("forward new", forward(""))
    time.sleep(7)
    check("2 windows after new", len(webview.windows) == 2)
    probe(webview.windows[1], "win2")

    config.set("second_launch", "focus")
    check("forward focus", forward(""))
    time.sleep(4)
    check("still 2 windows after focus", len(webview.windows) == 2)

    check("forward file in focus mode", forward(str(MD)))
    time.sleep(7)
    check("3 windows after file forward", len(webview.windows) == 3)
    probe(webview.windows[2], "win3")

    os._exit(0 if PASS else 1)


def main():
    config = Config()
    config.update({"startup_page": "home", "welcome_shown": True})
    mgr = WindowManager(config)

    def _on_open(path):
        if not path and config.get("second_launch", "new") == "focus":
            if mgr.focus_first():
                return
        mgr.create(path)

    server = InstanceServer(config, on_open=_on_open)
    server.start()
    mgr.create("")
    threading.Thread(target=work, args=(config,), daemon=True).start()
    try:
        start_webview(debug=False)
    finally:
        server.stop()


if __name__ == "__main__":
    main()
