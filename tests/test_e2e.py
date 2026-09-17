# -*- coding: utf-8 -*-
"""RyuuMD 真实窗口端到端测试(E2E)。

原理:启动真实 pywebview 窗口加载 app/web/index.html,用 evaluate_js 注入
操作与断言(探针模式),逐用例收集 PASS/FAIL,全部完成后输出汇总并销毁窗口。

隔离与防阻塞:
- 启动前将 APPDATA 指到临时目录(配置不污染用户数据);
- hook window.confirm 与 App.confirm 恒 true(未保存确认框不阻塞自动化);
- 大文档/小文档等文件全部落在临时目录。

运行: python tests/test_e2e.py   (或根目录 run_tests.py)
退出码: 0 = 全部通过; 1 = 有失败或环境错误。
"""

from __future__ import annotations

import os
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# 隔离配置:必须在 import app.core 之前设置
_TMP_APPDATA = tempfile.TemporaryDirectory(prefix="ryuumd-e2e-appdata-")
os.environ["APPDATA"] = _TMP_APPDATA.name

import webview  # noqa: E402

from app.core.api import Api  # noqa: E402
from app.core.config import Config  # noqa: E402
from main import start_webview  # noqa: E402

# 控制台为 GBK 时避免打印中文乱码:测试名/结果统一用 ASCII
def A(s: str) -> str:
    return s.encode("ascii", "replace").decode("ascii")


def fail_all(msg: str) -> "NoReturn":  # type: ignore[name-defined]
    print("E2E 环境错误:", A(msg), flush=True)
    sys.exit(1)

# ----------------------------------------------------------------------------
# 测试夹具:临时工作目录与大/小文档
# ----------------------------------------------------------------------------
_TMP = tempfile.TemporaryDirectory(prefix="ryuumd-e2e-files-")
TMP = Path(_TMP.name)
SMALL_MD = TMP / "small.md"
BIG_MD = TMP / "big.md"
HOME_MD = TMP / "home-open.md"
SMALL_MD.write_text("# 小文档\n\n普通正文。\n", encoding="utf-8")
HOME_MD.write_text("# 首页打开\n\nensureEditor 探测。\n", encoding="utf-8")
# 大文档夹具:真实笔记形态(多行短行、结构多样),体积 > 512KB 以触发大文档策略。
# 不用「单段 56 万字重复」这类病态输入——它对 Vditor sv 全量高亮是硬性能边界,
# 会卡死 WebView2 主线程,属已知边界(见 TEST_PLAN 手动项),不代表真实使用。
_BIG_LINES = []
for i in range(9000):
    _BIG_LINES.append(f"## 章节 {i}\n\n这是第 {i} 段的正文内容,包含**加粗**、`代码` 与 [链接](https://example.com)。\n\n- 列表项一\n- 列表项二\n")
BIG_MD.write_text("# 大文档\n\n" + "\n".join(_BIG_LINES), encoding="utf-8")
assert BIG_MD.stat().st_size > 512 * 1024, "夹具大文档必须超过 512KB 阈值"

SMALL_JS = str(SMALL_MD).replace("\\", "\\\\")
BIG_JS = str(BIG_MD).replace("\\", "\\\\")

# 首页用例夹具：临时仓库目录（含 md 文件）
REPO_DIR = TMP / "e2e-repo"
(REPO_DIR / "sub").mkdir(parents=True, exist_ok=True)
(REPO_DIR / "readme.md").write_text("# 仓库文档\n", encoding="utf-8")
(REPO_DIR / "sub" / "note.md").write_text("# 子目录\n", encoding="utf-8")
REPO_JS = str(REPO_DIR).replace("\\", "\\\\")

# 文件管理用例夹具：独立于仓库目录（避免影响 T24 的仓库计数断言）
OPS_DIR = TMP / "e2e-ops"
OPS_DIR.mkdir(parents=True, exist_ok=True)
(OPS_DIR / "rename-me.md").write_text("# 待重命名\n", encoding="utf-8")
(OPS_DIR / "delete-me.md").write_text("# 待删除\n", encoding="utf-8")
OPS_RENAME_JS = str(OPS_DIR / "rename-me.md").replace("\\", "\\\\")
OPS_RENAMED_JS = str(OPS_DIR / "renamed-e2e.md").replace("\\", "\\\\")
OPS_DELETE_JS = str(OPS_DIR / "delete-me.md").replace("\\", "\\\\")

# 工作副本夹具：与 e2e-repo 分开，避免 T24 计数/T45 搜索被副本树干扰
WORKDIR_DIR = TMP / "e2e-workdir"
(WORKDIR_DIR / "docs").mkdir(parents=True, exist_ok=True)
(WORKDIR_DIR / "guide.md").write_text("# 指南\n\n源文件原文 WorkdirSourceToken\n", encoding="utf-8")
(WORKDIR_DIR / "docs" / "note.md").write_text("# 笔记\n\n子页正文\n", encoding="utf-8")
WORKDIR_SOLO = TMP / "e2e-workdir-solo"
WORKDIR_SOLO.mkdir(parents=True, exist_ok=True)
(WORKDIR_SOLO / "hello.md").write_text("# 单文件\n\nSoloSource\n", encoding="utf-8")
(WORKDIR_SOLO / "other.md").write_text("# 其它\n\nSibling\n", encoding="utf-8")
WORKDIR_EDGE = TMP / "e2e-workdir-edge"
WORKDIR_EDGE.mkdir(parents=True, exist_ok=True)
(WORKDIR_EDGE / "keep.md").write_text("# 保留\n\nKeepSrc\n", encoding="utf-8")
WORKDIR_ORPHAN = TMP / "e2e-workdir-orphan"
WORKDIR_ORPHAN.mkdir(parents=True, exist_ok=True)
(WORKDIR_ORPHAN / "hello.md").write_text("# 孤儿\n\nOrphanSrc\n", encoding="utf-8")
_W116_KEY = {"key": ""}


def _src_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _assert_source_lacks(path: Path, token: str):
    text = _src_text(path)
    if token in text:
        return f"source leaked {token}: {text[:120]}"
    return True


def _assert_source_has(path: Path, token: str):
    text = _src_text(path)
    if token not in text:
        return f"source missing {token}: {text[:120]}"
    return True


def _workdir_t101(api, ev):
    leaked = _assert_source_lacks(WORKDIR_SOLO / "hello.md", "KeepFileSession")
    if leaked is not True:
        return leaked
    work = str(ev("window.__w101work||''") or "")
    if not work or not Path(work).is_file():
        return f"file session absorbed work={work}"
    return True


def _workdir_t102(api, ev):
    leaked = _assert_source_lacks(WORKDIR_SOLO / "hello.md", "KeepFileSession")
    if leaked is not True:
        return leaked
    listed = api.list_folder(str(WORKDIR_SOLO))
    if not listed.get("ok"):
        return listed
    copy = Path(listed["root"]) / "hello.md"
    if not copy.is_file():
        return f"folder copy missing {copy}"
    if "KeepFileSession" not in copy.read_text(encoding="utf-8"):
        return f"folder copy lost edits: {copy.read_text(encoding='utf-8')[:80]}"
    return True


def _workdir_t103(api, ev):
    src = WORKDIR_DIR / "guide.md"
    has = _assert_source_has(src, "WorkdirSourceToken")
    if has is not True:
        return has
    return _assert_source_lacks(src, "WorkdirCopyToken")


def _workdir_t104(api, ev):
    leaked = _assert_source_lacks(WORKDIR_DIR / "guide.md", "WorkdirCopyToken")
    if leaked is not True:
        return leaked
    opened = api.read_file(str(WORKDIR_DIR / "guide.md"))
    if not opened.get("ok"):
        return opened
    path = str(opened.get("path") or "")
    if "workcopies" not in path.replace("\\", "/"):
        return f"not copy path {path}"
    if "WorkdirCopyToken" not in (opened.get("content") or ""):
        return "copy missing token"
    return True


def _workdir_t107(api, ev):
    src = WORKDIR_DIR / "仅副本笔记.md"
    if src.exists():
        return f"source has copy-only file {src}"
    items = api.get_recent().get("items") or []
    hit = next((it for it in items if str(it.get("path") or "").endswith("仅副本笔记.md")), None)
    if not hit:
        return f"recent missing {items[:4]}"
    if not hit.get("exists"):
        return f"recent greyed {hit}"
    return True


def _workdir_t112(api, ev):
    from datetime import datetime

    name = datetime.now().strftime("%Y-%m-%d") + ".md"
    src = WORKDIR_DIR / "日记" / name
    if src.exists():
        return f"daily wrote source {src}"
    listed = api.list_folder(str(WORKDIR_DIR))
    if not listed.get("ok"):
        return listed
    copy = Path(listed["root"]) / "日记" / name
    if not copy.is_file():
        return f"daily copy missing {copy}"
    return True


def _workdir_t115(api, ev):
    listed = api.list_folder(str(WORKDIR_EDGE))
    if not listed.get("ok"):
        return listed
    work = Path(listed["root"])
    if not (work / "renamed-keep.md").is_file():
        return f"copy missing renamed-keep.md {[p.name for p in work.iterdir()]}"
    if (work / "keep.md").exists():
        return "old name resurrected on copy"
    if not (WORKDIR_EDGE / "keep.md").is_file():
        return "source keep.md missing"
    if (WORKDIR_EDGE / "renamed-keep.md").exists():
        return "rename leaked to source"
    return True


def _workdir_t116_before(api):
    listed = api.list_folder(str(WORKDIR_EDGE))
    key = api._knowledge_key(listed["root"])
    api._knowledge_cache[key] = {"ok": True, "text": "旧谱系"}
    _W116_KEY["key"] = key


def _workdir_t116(api, ev):
    if not _W116_KEY["key"]:
        return "no cache key"
    if _W116_KEY["key"] in api._knowledge_cache:
        return "knowledge cache not invalidated"
    return True


def _workdir_t117(api, ev):
    if "OrphanPushE2E" not in _src_text(WORKDIR_ORPHAN / "hello.md"):
        return "source missing OrphanPushE2E after orphan push"
    old = str(ev("window.__w117old||''") or "")
    if old and Path(old).exists():
        return f"orphan path still exists {old}"
    listed = api.list_folder(str(WORKDIR_ORPHAN))
    if not listed.get("ok"):
        return listed
    copy = Path(listed["root"]) / "hello.md"
    if not copy.is_file() or "OrphanPushE2E" not in copy.read_text(encoding="utf-8"):
        return "folder copy missing OrphanPushE2E"
    return True


def _workdir_t118(api, ev):
    leaked = _assert_source_lacks(WORKDIR_DIR / "guide.md", "DirtySwitchToken")
    if leaked is not True:
        return leaked
    prev = api.config.get("edit_mode")
    api.config.set("edit_mode", "workdir")
    try:
        opened = api.read_file(str(WORKDIR_DIR / "guide.md"))
        if not opened.get("ok"):
            return opened
        if "DirtySwitchToken" not in (opened.get("content") or ""):
            return "copy missing DirtySwitchToken"
        path = str(opened.get("path") or "").replace("\\", "/")
        if "workcopies" not in path:
            return f"not copy path {path}"
    finally:
        api.config.set("edit_mode", prev)
    return True

# T08 用 Markdown(含代码围栏,验证 sv 大纲忽略 ``` 内标题)
T08_MD = "# 模式测试\n\n```\n# 注释不是标题\n```\n\n## 真标题\n"


import json


def J(s: str) -> str:
    """把文本转为可安全内联进 JS 单引号字符串的形式(\\uXXXX 转义非 ASCII)。"""
    return "".join(c if ord(c) < 128 else "\\u%04x" % ord(c) for c in s).replace("\\", "\\\\").replace("'", "\\'")


def JV(s: str) -> str:
    """把文本转为完整 JS 字符串字面量(带引号),用 JSON 序列化保证任何字符(含反引号、
    换行、引号)都安全。evaluate_js 注入时优于手工转义——手工转义遇反引号/特殊序列
    易生成非法 JS(SyntaxError)导致整句不执行。"""
    return json.dumps(s, ensure_ascii=True)


# ----------------------------------------------------------------------------
# 用例定义(按状态依赖顺序执行)
#   setup: 先执行的 JS(可选)  sleep: 断言前等待秒数
#   js: 断言表达式,返回布尔真即通过(在超时窗口内轮询)
# ----------------------------------------------------------------------------
CASES = [
    dict(name="T00 启动页 home:有 last_file 仍显示首页",
         # startup_page=home 且 config 预置了 last_file：不得误走恢复会话。
         # welcome 关闭后应落到首页;验毕隐藏首页继续后续用例。
         # 空仓库以卡片数为准；空态节点用 inline style（getComputedStyle 在
         # 祖先刚从 display:none 切出时，WebView2 可能仍报 none）。
         setup="window.Home.show()",
         sleep=0.8,
         js=("(function(){if(!window.Home||!window.Home.isOpen())return 'home not open';"
             "if(document.querySelectorAll('#home .quick-card').length!==2)return 'quick cards';"
             "if(!document.getElementById('hq-tutorial'))return 'no tutorial card';"
             "if(!document.getElementById('hq-create-repo')||!document.getElementById('hq-create-folder'))return 'no create cards';"
             "if(!document.getElementById('home-repo-plus'))return 'no repo plus';"
             "var n=document.querySelectorAll('#repo-container .repo-card,#repo-container .repo-row').length;"
             "if(n)return 'repos='+n;"
             "var empty=document.getElementById('repo-empty');"
             "if(!empty||empty.style.display==='none')return 'repo empty hidden';"
             "return true;})()"),
         timeout=12,
         setup2="window.Home.hide()", sleep2=0.3,
         js2="!window.Home.isOpen()"),

    dict(name="T00b 首页打开文档:ensureEditor 后可读回内容",
         # 用独立夹具，避免 currentPath 指向 small.md 后被后续用例自动保存污染 T18/T19
         setup="window.Home.show();window.App.openPath(" + JV(str(HOME_MD)) + ")",
         sleep=0.4,
         timeout=20,
         js=("(function(){if(window.Home&&window.Home.isOpen())return 'home still open';"
             "if(!window.Editor||!window.Editor.isReady())return 'editor not ready';"
             "var v=window.Editor.getValue();"
             "return v.indexOf(" + JV("首页打开") + ")>=0?true:'content='+v.slice(0,40);})()")),

    dict(name="T01 启动:全局对象就绪、编辑器 ready",
         setup="window.App.ensureEditor()", sleep=0,
         timeout=20,
         js="!!(window.App&&window.Editor&&window.Sidebar&&window.Welcome&&window.Settings&&window.SlashMenu&&window.Home&&window.Palette&&window.FindBar&&window.Editor.isReady())"),

    dict(name="T02 工具栏按钮均有中文文字", sleep=0,
         js=("(function(){var ids=['btn-home','btn-sidebar','btn-open-folder','btn-open-file',"
             "'btn-new','btn-save','btn-push-source','btn-merge-source','btn-search','btn-ai','btn-new-window','btn-reveal','btn-theme','btn-settings'];"
             "var bs=document.querySelectorAll('#toolbar .icon-btn');"
             "if(bs.length!==ids.length)return 'count='+bs.length;"
             "for(var i=0;i<ids.length;i++){"
             "var b=document.getElementById(ids[i]);"
             "if(!b)return 'missing '+ids[i];"
             "var l=b.querySelector('.ib-label');"
             "if(!l||!l.textContent.trim())return 'label '+ids[i];}"
             "return true;})()")),

    dict(name="T03 图标注入为 svg 且文字未被覆盖", sleep=0,
         js="Array.from(document.querySelectorAll('#toolbar [data-icon]')).every(function(el){return el.innerHTML.indexOf('<svg')>=0;})"),

    dict(name="T04 setValue/getValue 往返",
         setup="window.Editor.setValue(" + JV('# 装载测试\n\n正文 ABC123') + ")", sleep=1.0,
         js="window.Editor.getValue().indexOf('ABC123')>=0"),

    dict(name="T05 大纲渲染:两项、无 # 前缀、空态隐藏",
         setup="window.Editor.setValue(" + JV('# 标题一\n\n正文\n\n## 标题二\n\n更多') + ")", sleep=1.2,
         js="(function(){var ol=document.getElementById('outline-list'),oe=document.getElementById('outline-empty');"
            "return ol.children.length===2&&getComputedStyle(oe).display==='none'&&ol.children[0].textContent.indexOf('#')!==0;})()"),

    dict(name="T06 大纲点击跳转:滚动离开顶部",
         setup="var s='';for(var i=0;i<80;i++){s+='## '+(" + JV('章节') + ")+i+'\\n\\n'+(" + JV('内容') + ")+'\\n';}window.Editor.setValue(s);",
         sleep=1.0,
         # 第一阶段:等 80 章大纲渲染完成(点击的前提)
         js="document.querySelectorAll('#outline-list .outline-item').length>=61",
         timeout=15,
         # 第二阶段:点击第 60 项后应滚动离开顶部
         setup2="(function(){var it=document.querySelectorAll('#outline-list .outline-item');if(it.length>60)it[60].click();})()",
         sleep2=1.0,
         js2=("(function(){var sc=document.querySelector('#editor .vditor-ir .vditor-reset');"
              "return sc&&sc.scrollTop>1000?true:'scrollTop='+(sc?sc.scrollTop:'null');})()")),

    dict(name="T07 切换文件回到文首:scrollTop 归零",
         setup=("var sc=document.querySelector('#editor .vditor-ir .vditor-reset');sc.scrollTop=999999;"
                "window.Editor.setValue(" + JV('# 新文档\n\nhello') + ")"),
         sleep=1.2,
         js="(function(){var sc=document.querySelector('#editor .vditor-ir .vditor-reset');return sc&&sc.scrollTop===0;})()"),

    dict(name="T08 切源码模式:内容保留、大纲忽略代码块内 #、无渲染预览分屏",
         setup="window.Editor.setValue(" + JV(T08_MD) + ");window.Editor.setMode('sv');",
         sleep=1.0,
         # 第一阶段:等 sv 模式就绪
         js="window.Editor.getMode()==='sv'&&window.Editor.isReady()",
         timeout=15,
         # 第二阶段:内容与大纲断言
         js2=("(function(){var v=window.Editor.getValue();if(v.indexOf("+JV('模式测试')+")<0)return 'lost len='+v.length+' mode='+window.Editor.getMode();"
              "var n=document.querySelectorAll('#outline-list .outline-item').length;"
              "if(n!==2)return 'outline='+n;"
              # 纯源码:右侧渲染预览面板必须不可见(preview.mode:'editor')
              "var pv=document.querySelector('#editor .vditor-preview');"
              "if(pv&&getComputedStyle(pv).display!=='none')return 'preview visible';"
              "return true;})()")),

    dict(name="T09 切回渲染模式:内容保留、大纲两项",
         setup="window.Editor.setMode('ir')",
         sleep=1.0,
         js="window.Editor.getMode()==='ir'&&window.Editor.isReady()",
         timeout=15,
         js2=("(function(){var v=window.Editor.getValue();if(v.indexOf("+JV('模式测试')+")<0)return 'lost len='+v.length;"
              "var n=document.querySelectorAll('#outline-list .outline-item').length;"
              "return n===2?true:'outline='+n;})()")),

    dict(name="T10 待办勾选:原生勾选生效且源码同步 [x]",
         setup="window.Editor.setValue(" + JV('- [ ] 任务甲\n- [x] 任务乙') + ")",
         sleep=1.0,
         # 第一阶段:等 checkbox 渲染出来
         js="document.querySelectorAll(\"#editor .vditor-ir input[type='checkbox']\").length>=2",
         timeout=15,
         setup2="document.querySelector(\"#editor .vditor-ir input[type='checkbox']\").click()",
         sleep2=1.0,
         # 第二阶段:勾选生效 + markdown 源码同步为 [x](守卫已移除,走 Vditor 原生)
         js2=("(function(){var cb=document.querySelector(\"#editor .vditor-ir input[type='checkbox']\");"
              "if(!cb||cb.checked!==true)return 'not checked';"
              "var v=window.Editor.getValue();"
              "return (v.indexOf('[x]')>=0||v.indexOf('[X]')>=0)?true:'src not synced:'+JSON.stringify(v.slice(0,30));})()")),

    dict(name="T11 主题切换:dark 生效、图标换 sun、文字保留",
         setup="document.getElementById('btn-theme').click()", sleep=0.8,
         js=("document.documentElement.getAttribute('data-theme')==='dark'"
             # 新色板(a1-a6)每板自带明暗双态,明暗对切不再换 palette id
             "&&document.documentElement.getAttribute('data-palette')==='a1'"
             "&&document.querySelector('#btn-theme .ib-icon').innerHTML.indexOf('<svg')>=0"
             "&&document.querySelector('#btn-theme .ib-label').textContent.trim().length>0")),

    dict(name="T11b 主题切回 light",
         setup="document.getElementById('btn-theme').click()", sleep=0.8,
         js=("document.documentElement.getAttribute('data-theme')==='light'"
             "&&document.documentElement.getAttribute('data-palette')==='a1'")),

    dict(name="T12 设置弹窗:四个分组 tab、外观配色与字体、云同步默认收起、AI 字段",
         setup="document.getElementById('btn-settings').click()", sleep=0.5,
         js=("(function(){var m=document.getElementById('settings-mask');"
             "if(!m.classList.contains('open'))return 'not open';"
             "if(m.querySelectorAll('.settings-tab').length!==4)return 'tabs='+m.querySelectorAll('.settings-tab').length;"
             "if(!document.getElementById('set-autosave')||!document.getElementById('set-daily-folder'))return 'missing general';"
             "if(!document.getElementById('set-edit-mode'))return 'missing edit mode';"
             "if(!document.getElementById('set-math-engine'))return 'missing math engine';"
             "if(!document.getElementById('set-tutorial'))return 'missing tutorial';"
             "var sw=m.querySelectorAll('#swatch-palette .palette-swatch').length;"
             "if(sw!==5)return 'swatch '+sw;"
             "if(!document.getElementById('set-font-ui')||!document.getElementById('set-font-mono'))return 'missing font';"
             "if(!document.getElementById('set-reduced-motion'))return 'missing reduced motion';"
             "if(!document.getElementById('cloud-enabled')||document.getElementById('cloud-panel').classList.contains('show'))return 'cloud';"
             "if(!document.getElementById('ai-base-url')||!document.getElementById('ai-api-key')||!document.getElementById('ai-model'))return 'missing ai';"
             "return true;})()"),
         setup2="document.getElementById('set-close').click()", sleep2=0.4,
         js2="!document.getElementById('settings-mask').classList.contains('open')"),

    dict(name="T12c 色板切换后根节点 data-palette 与可见表面一致",
         setup="document.getElementById('btn-settings').click()", sleep=0.5,
         js="document.getElementById('settings-mask').classList.contains('open')",
         setup2=("(function(){var t=document.querySelector('.settings-tab[data-tab=\"appearance\"]');"
                 "if(t)t.click();"
                 "var s=document.querySelector('#swatch-palette .palette-swatch[data-v=\"a3\"]');"
                 "if(s)s.click();})()"),
         sleep2=0.5,
         js2=("document.documentElement.getAttribute('data-palette')==='a3'"
              "&&document.documentElement.getAttribute('data-theme')==='light'"),
         setup3=("(function(){var s=document.querySelector('#swatch-palette .palette-swatch[data-v=\"a1\"]');"
                 "if(s)s.click();"
                 "document.getElementById('set-close').click();})()"),
         sleep3=0.5,
         js3=("document.documentElement.getAttribute('data-palette')==='a1'"
              "&&!document.getElementById('settings-mask').classList.contains('open')")),

    dict(name="T42 云同步面板:默认关闭,勾选后展开表单",
         setup="document.getElementById('btn-settings').click()", sleep=0.5,
         js=("document.getElementById('settings-mask').classList.contains('open')"
             "&&!document.getElementById('cloud-panel').classList.contains('show')"),
         setup2="document.getElementById('cloud-enabled').click()", sleep2=0.4,
         js2=("(function(){var p=document.getElementById('cloud-panel');"
              "if(!p.classList.contains('show'))return 'panel hidden';"
              "if(!document.getElementById('cloud-url')||!document.getElementById('cloud-sync-now'))return 'missing fields';"
              "if(!document.getElementById('cloud-auto-save')||!document.getElementById('cloud-sync-all'))return 'missing checks';"
              "document.getElementById('set-close').click();return true;})()")),

    dict(name="T13 欢迎页重开:两个选择、打赏二维码、可关闭",
         setup="window.App.showWelcome()", sleep=1.2,
         js=("(function(){var m=document.getElementById('welcome-mask');"
             "var qr=document.getElementById('welcome-qr');"
             "return m.classList.contains('open')"
             "&&m.querySelectorAll('.feature-card').length===0"
             "&&m.querySelectorAll('.style-opt').length===2"
             "&&m.querySelectorAll('.mode-opt').length===2"
             "&&document.getElementById('welcome-edit-mode')"
             "&&qr&&qr.classList.contains('has-img');})()"),
         setup2="document.getElementById('welcome-start').click()", sleep2=0.5,
         js2="!document.getElementById('welcome-mask').classList.contains('open')"),

    dict(name="T14 斜杠菜单:右键唤起全部命令、Esc 关闭",
         setup=("(function(){var el=document.querySelector('#editor .vditor-ir .vditor-reset');"
                "el.dispatchEvent(new MouseEvent('contextmenu',{bubbles:true,cancelable:true}));})()"),
         sleep=0.6,
         js=("(function(){var m=document.getElementById('slash-menu');"
             "return m.classList.contains('open')&&m.querySelectorAll('.slash-item').length>=15;})()"),
         setup2="document.dispatchEvent(new KeyboardEvent('keydown',{key:'Escape',bubbles:true}))", sleep2=0.4,
         js2="!document.getElementById('slash-menu').classList.contains('open')"),

    dict(name="T15 文件树渲染与目录折叠",
         setup=("window.Sidebar.renderTree([{type:'dir',name:'docs',path:'/d/docs',children:[{type:'file',name:'a.md',path:'/d/docs/a.md'}]},"
                "{type:'file',name:'b.md',path:'/d/b.md'}],'root');"),
         sleep=0.4,
         js="document.querySelectorAll('#file-tree .tree-item').length===3",
         setup2="document.querySelector('#file-tree .tree-item.dir').click()", sleep2=0.3,
         js2=("(function(){var d=document.querySelector('#file-tree .tree-item.dir');"
              "var c=document.querySelector('#file-tree .tree-children');"
              "return d.classList.contains('collapsed')&&c.style.display==='none';})()")),

    dict(name="T16 侧栏页签:目录/大纲切换",
         setup="document.querySelector('.side-tab[data-panel=\"outline\"]').click()", sleep=0.3,
         js="document.getElementById('panel-outline').classList.contains('active')",
         setup2="document.querySelector('.side-tab[data-panel=\"files\"]').click()", sleep2=0.3,
         js2="document.getElementById('panel-files').classList.contains('active')"),

    dict(name="T17 侧栏折叠/展开",
         setup="document.getElementById('btn-sidebar').click()", sleep=0.3,
         js="document.getElementById('sidebar').classList.contains('collapsed')",
         setup2="document.getElementById('btn-sidebar').click()", sleep2=0.3,
         js2="!document.getElementById('sidebar').classList.contains('collapsed')"),

    dict(name="T18 大文档自动源码模式 + 小文档恢复渲染",
         setup=("window.Sidebar.renderTree([{type:'file',name:'big.md',path:'" + BIG_JS + "'},{type:'file',name:'small.md',path:'" + SMALL_JS + "'}],'tmp');"
                "document.querySelectorAll('#file-tree .tree-item.file')[0].click();"),
         sleep=1.0,
         # 第一阶段:大文档触发自动 sv 且内容加载完成
         js=("(function(){if(!window.Editor.isReady())return 'not ready';"
             "if(window.Editor.getMode()!=='sv')return 'mode='+window.Editor.getMode();"
             "return window.Editor.getValue().length>400000?true:'len='+window.Editor.getValue().length;})()"),
         timeout=25,
         # 第二阶段:点开小文档恢复 ir（须等 isReady，避免大文档 sv 重建未完成就断言）
         setup2="document.querySelectorAll('#file-tree .tree-item.file')[1].click()",
         sleep2=1.2,
         js2=("(function(){if(!window.Editor.isReady())return 'not ready';"
              "if(window.Editor.getMode()!=='ir')return 'mode='+window.Editor.getMode();"
              "var v=window.Editor.getValue();"
              "return v.indexOf("+JV('小文档')+")>=0?true:'content';})()"),
         timeout2=20),

    dict(name="T19 字数统计:经 loadDoc 路径统计正确",
         # 字数统计由 app.js 在 loadDoc / 编辑变化时调用 updateCount,去空白字符计数。
         # 合成 InputEvent 无法驱动 Vditor 内部 input 回调(真实键入才可,见手动项),改走 loadDoc 可达路径:
         # 点开 T18 渲染的小文档「# 小文档\n\n普通正文。\n」,去空白=「#小文档普通正文。」= 9 字
         # (# 与句号非空白字符,计入)。
         setup="document.querySelectorAll('#file-tree .tree-item.file')[1].click()",
         sleep=1.5,
         js=("(function(){if(!window.Editor.isReady())return 'not ready';"
             "if(window.Editor.getValue().indexOf("+JV('小文档')+")<0)return 'content';"
             "var sc=document.getElementById('sb-count').textContent.trim();"
             "return sc==='9 '+(" + JV('字') + ")?true:'count='+sc;})()"),
         timeout=20),

    dict(name="T20 保存后无脏标记:点开文件本身不脏",
         # loadDoc 后 lastSaved=内容,dirty=false,文档名不应有 ●
         sleep=0.3,
         js=("(function(){var dn=document.getElementById('doc-name').textContent;"
             "return dn.indexOf('\\u25cf')<0?true:'unexpected dirty:'+dn;})()"),
         timeout=8),

    # —— 拖放修复(捕获阶段接管)回归 ——
    # 背景:Vditor 面板 drop 处理器首行 stopPropagation,曾致冒泡链全断(文件打不开、
    # hint 常驻似卡死)。修复后 drop 由 window 捕获 handler 统一接管。
    # 注:合成 File 非磁盘文件,postMessageWithAdditionalObjects 会被 WebView2 拒绝
    # (handler 内已 try-catch 包容),故 Python 落地段走单元测试覆盖,这里只验 JS 层。
    dict(name="T21 文件drop:捕获接管、hint隐藏、不冒泡、转发api",
         setup=("window.__dropCalls=[];"
                "window.__origOdf=window.pywebview.api.open_dropped_files;"
                "window.pywebview.api.open_dropped_files=function(n){window.__dropCalls.push(n);return Promise.resolve({ok:true});};"
                "document.__probeBubble=0;"
                "document.addEventListener('drop',function(){document.__probeBubble++;});"
                "window.dispatchEvent(new DragEvent('dragenter',{bubbles:true,cancelable:true}));"
                "var dt=new DataTransfer();"
                "dt.items.add(new File(['# t'],'drag.md',{type:'text/markdown'}));"
                "var ed=document.querySelector('#editor .vditor-ir .vditor-reset')||document.getElementById('editor');"
                "ed.dispatchEvent(new DragEvent('drop',{bubbles:true,cancelable:true,dataTransfer:dt}));"),
         sleep=0.8,
         js=("(function(){"
             "var hintHidden=!document.getElementById('drop-hint').classList.contains('show');"
             "var called=window.__dropCalls.length===1&&window.__dropCalls[0][0]==='drag.md';"
             "var noBubble=document.__probeBubble===0;"
             "window.pywebview.api.open_dropped_files=window.__origOdf;"
             "if(!hintHidden)return 'hint not hidden';"
             "if(!called)return 'api not called:'+JSON.stringify(window.__dropCalls);"
             "if(!noBubble)return 'bubbled to document';"
             "return true;})()"),
         timeout=10),

    dict(name="T22 编辑器内部拖拽(无Files):放行冒泡给Vditor",
         setup=("document.__probeBubble2=0;"
                "document.addEventListener('drop',function(){document.__probeBubble2++;});"
                "var dt=new DataTransfer();"
                "dt.setData('text/plain','x');"
                "var ed=document.querySelector('#editor .vditor-ir .vditor-reset')||document.getElementById('editor');"
                "ed.dispatchEvent(new DragEvent('drop',{bubbles:true,cancelable:true,dataTransfer:dt}));"),
         sleep=0.5,
         js="document.__probeBubble2===1?true:'bubble='+document.__probeBubble2",
         timeout=8),

    dict(name="T23 最近打开:列表渲染、最新在前、点击打开",
         # T18/T19 已先后经文件树打开 big.md / small.md(read_file 记录),
         # 最近列表应含两项且 small.md 居首
         setup="document.querySelector('.side-tab[data-panel=\"recent\"]').click()",
         sleep=0.8,
         js=("(function(){"
             "if(!document.getElementById('panel-recent').classList.contains('active'))return 'panel not active';"
             "var items=document.querySelectorAll('#recent-list .recent');"
             "if(items.length<2)return 'items='+items.length;"
             "var first=items[0].querySelector('.tw-name').textContent;"
             "if(first!=='small.md')return 'first='+first;"
             "if(getComputedStyle(document.getElementById('recent-foot')).display==='none')return 'foot hidden';"
             "return true;})()"),
         timeout=10,
         # 第二阶段:点击第一项(small.md)应打开该文档
         setup2="document.querySelectorAll('#recent-list .recent')[0].click()",
         sleep2=1.2,
         js2=("(function(){var dn=document.getElementById('doc-name').textContent;"
              "return dn.indexOf('small.md')>=0?true:'doc='+dn;})()")),

    dict(name="T119 首页有最近时骨架仍含随手记",
         setup="window.Home.show()", sleep=1.0,
         js=("(function(){"
             "if(!window.Home.isOpen())return 'home closed';"
             "if(document.getElementById('home-glance-sec')||document.getElementById('home-qe-sec'))return 'glance still';"
             "if(!document.getElementById('home-scratch-sec'))return 'no scratch';"
             "var recent=document.getElementById('home-recent');"
             "if(!recent||!recent.children.length)return 'no recent';"
             "return true;})()"),
         timeout=12),

    dict(name="T120 随手记写入后再读回",
         setup=("window.__t120=0;"
                "window.pywebview.api.write_scratch(" + JV("SCRATCH_TOKEN1") + ").then(function(r){"
                "window.__t120=r&&r.ok?1:-1;window.Home.show();});"),
         sleep=1.0,
         js=("(function(){if(window.__t120!==1)return 'write='+window.__t120;"
             "var el=document.getElementById('home-scratch-input');"
             "if(!el)return 'no textarea';"
             "if((el.value||'').indexOf('SCRATCH_TOKEN1')<0)return 'val='+el.value;"
             "return true;})()"),
         timeout=12,
         setup2=("(function(){var el=document.getElementById('home-scratch-input');"
                 "el.value='SCRATCH_TOKEN2';"
                 "document.getElementById('home-scratch-save').click();})()"),
         sleep2=0.8,
         js2="true",
         py=lambda api, ev: (
             True if "SCRATCH_TOKEN2" in Path(api.scratch_path()).read_text(encoding="utf-8")
             else "scratch=" + Path(api.scratch_path()).read_text(encoding="utf-8")[:80]
         )),

    # —— 首页 / 仓库管理(本次新增功能) ——
    dict(name="T24 添加仓库并在首页渲染卡片(计数/头像)",
         setup=("window.__t24=0;"
                "window.pywebview.api.add_project('" + REPO_JS + "','').then(function(r){window.__t24=r.ok?1:-1;});"),
         sleep=0.5,
         js="window.__t24===1?true:'add='+window.__t24",
         timeout=10,
         setup2="window.Home.show()", sleep2=0.6,
         js2=("(function(){var cards=document.querySelectorAll('#repo-container .repo-card');"
              "if(cards.length!==1)return 'cards='+cards.length;"
              "var c=cards[0];"
              "if(getComputedStyle(document.getElementById('repo-empty')).display!=='none')return 'empty visible';"
              "if(!c.querySelector('.repo-avatar').textContent.trim())return 'avatar empty';"
              "var meta=c.querySelector('.repo-meta').textContent;"
              "if(meta.indexOf('2')<0)return 'meta='+meta;"
              "return true;})()")),

    dict(name="T25 仓库视图切换:列表<->卡片,偏好持久化",
         setup="document.querySelector('#repo-view-toggle button[data-view=\"list\"]').click()", sleep=0.4,
         js=("(function(){var rows=document.querySelectorAll('#repo-container .repo-row');"
             "if(rows.length!==1)return 'rows='+rows.length;"
             "if(!document.getElementById('repo-container').classList.contains('repo-list'))return 'class';"
             "return true;})()"),
         setup2="document.querySelector('#repo-view-toggle button[data-view=\"card\"]').click()", sleep2=0.4,
         js2="document.querySelectorAll('#repo-container .repo-card').length===1"),

    dict(name="T26 重命名仓库:小弹窗改名并刷新",
         setup=("document.querySelector('#repo-container .repo-actions button[data-act=\"rename\"]').click();"
                "document.getElementById('mm-name').value=" + JV("E2E笔记仓") + ";"
                "document.getElementById('mm-ok').click();"),
         sleep=0.8,
         js=("(function(){if(document.getElementById('home-modal-mask').classList.contains('open'))return 'modal open';"
             "var n=document.querySelector('#repo-container .repo-name');"
             "return n&&n.textContent.indexOf(" + JV("E2E笔记仓") + ")>=0?true:'name='+(n?n.textContent:'null');})()"),
         timeout=10),

    dict(name="T27 置顶仓库:pin 标记出现",
         setup="document.querySelector('#repo-container .repo-actions button[data-act=\"pin\"]').click()",
         sleep=0.8,
         js=("(function(){var f=document.querySelector('#repo-container .repo-name .pin-flag');"
             "return f?true:'no pin flag';})()"),
         timeout=10),

    dict(name="T28 点击仓库卡片:进入编辑器视图并渲染文件树",
         setup="document.querySelector('#repo-container .repo-card').click()",
         sleep=1.0,
         js=("(function(){if(window.Home.isOpen())return 'home still open';"
             "var files=document.querySelectorAll('#file-tree .tree-item');"
             "if(files.length<2)return 'tree='+files.length;"
             "return true;})()"),
         timeout=12),

    dict(name="T29 首页最近区:渲染并支持存为仓库按钮",
         setup="window.Home.show()", sleep=0.8,
         js=("(function(){var rows=document.querySelectorAll('#home-recent .recent-row');"
             "if(rows.length<1)return 'rows='+rows.length;"
             "var save=document.querySelectorAll('#home-recent .rr-save');"
             "if(save.length<1)return 'no save btn';"  # 最近含文件夹(e2e-repo),应有存为仓库按钮
             "return true;})()"),
         timeout=10,
         # 第二阶段:移除仓库(confirm 已恒真),列表回到空态
         setup2="document.querySelector('#repo-container .repo-actions button[data-act=\"remove\"]').click()",
         sleep2=0.8,
         js2=("(function(){var cards=document.querySelectorAll('#repo-container .repo-card,#repo-container .repo-row');"
              "if(cards.length!==0)return 'cards='+cards.length;"
              "if(getComputedStyle(document.getElementById('repo-empty')).display==='none')return 'empty hidden';"
              "window.Home.hide();return true;})()")),

    # —— 文件管理（右键菜单操作真实磁盘文件） ——
    dict(name="T30 文件右键菜单:四项弹出、末项 danger、点击外部关闭",
         setup=("window.Sidebar.renderTree([{type:'file',name:'a.md',path:'/d/a.md'}],'root');"
                "var el=document.querySelector('#file-tree .tree-item.file');"
                "el.dispatchEvent(new MouseEvent('contextmenu',{bubbles:true,cancelable:true,clientX:200,clientY:200}));"),
         sleep=0.4,
         js=("(function(){var m=document.getElementById('tree-menu');"
             "if(!m.classList.contains('open'))return 'not open';"
             "var items=m.querySelectorAll('.ctx-item');"
             "if(items.length!==4)return 'items='+items.length;"
             "if(!items[3].classList.contains('danger'))return 'delete not danger';"
             "return true;})()"),
         setup2="document.body.dispatchEvent(new MouseEvent('click',{bubbles:true}))", sleep2=0.3,
         js2="!document.getElementById('tree-menu').classList.contains('open')"),

    dict(name="T31 重命名真实文件:弹窗改名、磁盘生效",
         setup=("window.Sidebar.renderTree([{type:'file',name:'rename-me.md',path:'" + OPS_RENAME_JS + "'}],'ops');"
                "var el=document.querySelector('#file-tree .tree-item.file');"
                "el.dispatchEvent(new MouseEvent('contextmenu',{bubbles:true,cancelable:true,clientX:200,clientY:200}));"
                "document.querySelector('#tree-menu .ctx-item[data-act=\"rename\"]').click();"),
         sleep=0.5,
         js=("(function(){var m=document.getElementById('input-modal-mask');"
             "return m.classList.contains('open')&&!!document.getElementById('pm-input')?true:'modal not open';})()"),
         # 第二阶段:输入新名并确定;延迟后回读磁盘验证(改名是异步链路)
         setup2=("document.getElementById('pm-input').value='renamed-e2e';"
                 "document.getElementById('pm-ok').click();"
                 "window.__t31=0;"
                 "setTimeout(function(){"
                 "window.pywebview.api.read_file('" + OPS_RENAMED_JS + "').then(function(r){"
                 "if(!r.ok){window.__t31=-1;return;}"
                 "window.pywebview.api.read_file('" + OPS_RENAME_JS + "').then(function(o){"
                 "window.__t31=o.ok?-2:1;});});},700);"),
         sleep2=1.6,
         js2=("(function(){"
              "if(document.getElementById('input-modal-mask').classList.contains('open'))return 'modal open';"
              "return window.__t31===1?true:'t31='+window.__t31;})()"),
         timeout=12),

    dict(name="T32 删除当前打开文件:回收站、内容保留为未保存草稿",
         setup=("window.Sidebar.renderTree([{type:'file',name:'delete-me.md',path:'" + OPS_DELETE_JS + "'}],'ops');"
                "document.querySelector('#file-tree .tree-item.file').click();"),
         sleep=1.2,
         js=("(function(){var dn=document.getElementById('doc-name').textContent;"
             "return dn.indexOf('delete-me.md')>=0?true:'doc='+dn;})()"),
         # 第二阶段:右键删除(confirm 已 hook 恒真);延迟回读磁盘 + 断言草稿态
         setup2=("var el=document.querySelector('#file-tree .tree-item.file');"
                 "el.dispatchEvent(new MouseEvent('contextmenu',{bubbles:true,cancelable:true,clientX:200,clientY:200}));"
                 "document.querySelector('#tree-menu .ctx-item[data-act=\"delete\"]').click();"
                 "window.__t32=0;"
                 "setTimeout(function(){"
                 "window.pywebview.api.read_file('" + OPS_DELETE_JS + "').then(function(o){window.__t32=o.ok?-1:1;});"
                 "},700);"),
         sleep2=1.6,
         js2=("(function(){"
              "if(window.__t32!==1)return 't32='+window.__t32;"
              "var dn=document.getElementById('doc-name').textContent;"
              "if(dn.indexOf('\\u25cf')<0)return 'not dirty:'+dn;"
              "var sp=document.getElementById('sb-path').textContent;"
              "return sp.indexOf(" + JV('未保存文档') + ")>=0?true:'path='+sp;})()"),
         timeout=12),

    # —— 体验优化：模式切换保持阅读位置 / 选区配色 / 右键剪贴板 ——
    dict(name="T33 切源码模式保持阅读位置(滚动比例)",
         setup=("var s='';for(var i=0;i<80;i++){s+='## '+(" + JV('章节') + ")+i+'\\n\\n'+(" + JV('内容') + ")+'\\n';}window.Editor.setValue(s);"),
         sleep=1.2,
         js="document.querySelectorAll('#outline-list .outline-item').length>=61",
         timeout=15,
         setup2=("var sc=document.querySelector('#editor .vditor-ir .vditor-reset');"
                 "sc.scrollTop=(sc.scrollHeight-sc.clientHeight)*0.6;"
                 "window.__r0=sc.scrollTop/(sc.scrollHeight-sc.clientHeight);"
                 "window.Editor.setMode('sv');"),
         sleep2=2.0,
         js2=("(function(){if(window.Editor.getMode()!=='sv'||!window.Editor.isReady())return 'not sv';"
              "var sc=document.querySelector('#editor .vditor-sv');if(!sc)return 'no panel';"
              "var max=sc.scrollHeight-sc.clientHeight;if(max<=0)return 'not scrollable';"
              "var r=sc.scrollTop/max;"
              "return (Math.abs(r-window.__r0)<0.15&&sc.scrollTop>500)?true:'r='+r+' top='+sc.scrollTop+' r0='+window.__r0;})()")),

    dict(name="T34 切回渲染模式保持阅读位置",
         setup="window.Editor.setMode('ir')",
         sleep=1.8,
         js="window.Editor.getMode()==='ir'&&window.Editor.isReady()",
         timeout=15,
         sleep2=1.0,
         js2=("(function(){var sc=document.querySelector('#editor .vditor-ir .vditor-reset');if(!sc)return 'no panel';"
              "var max=sc.scrollHeight-sc.clientHeight;if(max<=0)return 'not scrollable';"
              "var r=sc.scrollTop/max;"
              "return (Math.abs(r-window.__r0)<0.15&&sc.scrollTop>500)?true:'r='+r+' top='+sc.scrollTop;})()")),

    dict(name="T35 选区配色:编辑器选区与底色拉开对比(亮色)",
         js=("(function(){var el=document.querySelector('#editor .vditor-reset')||document.getElementById('editor');"
             "var shallow=getComputedStyle(document.documentElement).getPropertyValue('--sys-primary-shallow').trim();"
             "if(!shallow)return 'no primary-shallow';"
             "var bg=getComputedStyle(el,'::selection').backgroundColor;"
             "if(!bg||bg==='rgba(0, 0, 0, 0)'||bg==='transparent')return 'no selection style:'+bg;"
             "return true;})()")),

    dict(name="T36 右键菜单剪贴板组:有选区可用、无选区禁用复制/剪切",
         setup=("(function(){var el=document.querySelector('#editor .vditor-ir .vditor-reset');"
                "var h=el.querySelector('h2');var r=document.createRange();"
                "r.selectNodeContents(h);var sel=window.getSelection();sel.removeAllRanges();sel.addRange(r);"
                "el.dispatchEvent(new MouseEvent('contextmenu',{bubbles:true,cancelable:true,clientX:300,clientY:300}));})()"),
         sleep=0.5,
         js=("(function(){var m=document.getElementById('slash-menu');"
             "if(!m.classList.contains('open'))return 'not open';"
             "var g=m.querySelector('.slash-group-label');"
             "if(!g||g.textContent!=="+JV('剪贴板')+")return 'group='+(g?g.textContent:'null');"
             "var items=m.querySelectorAll('.slash-item');"
             "if(items.length<4)return 'items='+items.length;"
             "for(var i=0;i<3;i++){if(items[i].classList.contains('disabled'))return 'item'+i+' disabled';}"
             "return true;})()"),
         # 第二阶段:清空选区再右键 → 复制/剪切禁用、粘贴仍可用
         setup2=("window.getSelection().removeAllRanges();"
                 "var el=document.querySelector('#editor .vditor-ir .vditor-reset');"
                 "el.dispatchEvent(new MouseEvent('contextmenu',{bubbles:true,cancelable:true,clientX:300,clientY:300}));"),
         sleep2=0.4,
         js2=("(function(){var items=document.querySelectorAll('#slash-menu .slash-item');"
              "if(items.length<4)return 'items='+items.length;"
              "if(!items[0].classList.contains('disabled')||!items[1].classList.contains('disabled'))return 'copy/cut not disabled';"
              "if(items[2].classList.contains('disabled'))return 'paste disabled';"
              "return true;})()")),

    dict(name="T37 剪贴板接线:复制/剪切走 execCommand、粘贴走 readText 插入",
         # 合成事件无真实用户手势,execCommand 与 OS 剪贴板读取会被 Chromium 拒绝,
         # 故用 spy/stub 验证菜单→处理函数的接线;真实手势效果见手动清单。
         setup=("window.__ec=[];window.__origEC=document.execCommand;"
                "document.execCommand=function(k){window.__ec.push(k);return true;};"
                "(function(){var el=document.querySelector('#editor .vditor-ir .vditor-reset');"
                "var h=el.querySelector('h2');var r=document.createRange();"
                "r.selectNodeContents(h);var sel=window.getSelection();sel.removeAllRanges();sel.addRange(r);"
                "el.dispatchEvent(new MouseEvent('contextmenu',{bubbles:true,cancelable:true,clientX:300,clientY:300}));})();"
                # 菜单项监听的是 mousedown（preventDefault 保选区），click() 不会触发
                "document.querySelectorAll('#slash-menu .slash-item')[0].dispatchEvent(new MouseEvent('mousedown',{bubbles:true,cancelable:true}));"),
         sleep=0.5,
         js="window.__ec.join(',')==='copy'?true:'ec='+window.__ec.join(',')",
         # 第二阶段:stub readText → 点粘贴 → 标记文本进文档;并还原 spy/stub
         setup2=("window.__origRT=navigator.clipboard.readText;"
                 "navigator.clipboard.readText=function(){return Promise.resolve('PASTE-E2E-标记');};"
                 "window.getSelection().removeAllRanges();"
                 "var el=document.querySelector('#editor .vditor-ir .vditor-reset');"
                 "el.dispatchEvent(new MouseEvent('contextmenu',{bubbles:true,cancelable:true,clientX:300,clientY:300}));"
                 "document.querySelectorAll('#slash-menu .slash-item')[2].dispatchEvent(new MouseEvent('mousedown',{bubbles:true,cancelable:true}));"
                 "window.__t37=0;"
                 "setTimeout(function(){"
                 "window.__t37=window.Editor.getValue().indexOf('PASTE-E2E-标记')>=0?1:-1;"
                 "document.execCommand=window.__origEC;"
                 "navigator.clipboard.readText=window.__origRT;},700);"),
         sleep2=1.4,
         js2="window.__t37===1?true:'t37='+window.__t37+' len='+window.Editor.getValue().length",
         timeout=12),

    # —— 健壮性回归：快速连切竞态 / 快捷键 / 真实保存 ——
    dict(name="T38 快速连切模式:重建中再切,最终落到目标模式且内容不丢",
         # 连发 sv→ir→sv:第一次重建中后两次应被队列仲裁(最终=sv),不出现按钮与面板错位
         setup="window.Editor.setMode('sv');window.Editor.setMode('ir');window.Editor.setMode('sv');",
         sleep=3.0,
         js=("(function(){if(window.Editor.getMode()!=='sv')return 'mode='+window.Editor.getMode();"
             "if(!window.Editor.isReady())return 'not ready';"
             "var v=window.Editor.getValue();"
             "if(v.indexOf("+JV('章节')+")<0)return 'content lost len='+v.length;"
             "var p=document.querySelector('#editor .vditor-sv');"
             "return p?true:'no sv panel';})()"),
         timeout=18),

    dict(name="T39 快捷键 Ctrl+Shift+B:折叠/展开侧栏",
         setup="window.dispatchEvent(new KeyboardEvent('keydown',{key:'B',ctrlKey:true,shiftKey:true,bubbles:true}))",
         sleep=0.3,
         js="document.getElementById('sidebar').classList.contains('collapsed')",
         setup2="window.dispatchEvent(new KeyboardEvent('keydown',{key:'B',ctrlKey:true,shiftKey:true,bubbles:true}))",
         sleep2=0.3,
         js2="!document.getElementById('sidebar').classList.contains('collapsed')"),

    dict(name="T40 Ctrl+S 真实写盘:保存后磁盘内容含新文本",
         setup=("window.Sidebar.renderTree([{type:'file',name:'renamed-e2e.md',path:'" + OPS_RENAMED_JS + "'}],'ops');"
                "document.querySelector('#file-tree .tree-item.file').click();"),
         sleep=1.2,
         js=("(function(){var dn=document.getElementById('doc-name').textContent;"
             "return dn.indexOf('renamed-e2e.md')>=0?true:'doc='+dn;})()"),
         setup2=("window.Editor.setValue(window.Editor.getValue()+'\\n\\n'+(" + JV('SAVE-E2E 标记') + "));"
                 "window.dispatchEvent(new KeyboardEvent('keydown',{key:'s',ctrlKey:true,bubbles:true}));"
                 "window.__t40=0;"
                 "setTimeout(function(){"
                 "window.pywebview.api.read_file('" + OPS_RENAMED_JS + "').then(function(r){"
                 "window.__t40=(r.ok&&r.content.indexOf(" + JV('SAVE-E2E 标记') + ")>=0)?1:-1;});},800);"),
         sleep2=1.6,
         js2="window.__t40===1?true:'t40='+window.__t40",
         timeout=12),

    dict(name="T41 首页最近区分组:最近3个文件单独成卡、文件夹归入其他",
         # 此刻最近列表应为 [renamed-e2e.md, small.md, e2e-repo(文件夹), big.md, ...]
         setup="window.Home.show()",
         sleep=0.8,
         js=("(function(){"
             "var cards=document.querySelectorAll('#home-recent .recent-file-card');"
             "if(cards.length!==3)return 'cards='+cards.length;"
             "var first=cards[0].querySelector('.rf-name').textContent;"
             "if(first!=='renamed-e2e.md')return 'first='+first;"
             "var labels=document.querySelectorAll('#home-recent .recent-group-label');"
             "if(labels.length!==2)return 'labels='+labels.length;"
             "var rows=document.querySelectorAll('#home-recent .recent-row');"
             "if(rows.length<1)return 'rows='+rows.length;"
             "if(document.querySelectorAll('#home-recent .rr-save').length<1)return 'no save btn';"
             "return true;})()"),
         timeout=10,
         setup2="window.Home.hide()", sleep2=0.3,
         js2="!window.Home.isOpen()"),

    dict(name="T43 命令面板 Ctrl+P:打开快速打开并可 Esc 关闭",
         setup="window.dispatchEvent(new KeyboardEvent('keydown',{key:'p',ctrlKey:true,bubbles:true}))",
         sleep=0.4,
         js="document.getElementById('palette-mask').classList.contains('open')&&!!document.getElementById('pal-input')",
         setup2="document.dispatchEvent(new KeyboardEvent('keydown',{key:'Escape',bubbles:true}))",
         sleep2=0.3,
         js2="!document.getElementById('palette-mask').classList.contains('open')"),

    dict(name="T44 命令面板 Ctrl+Shift+P:列出保存等应用命令",
         setup="window.dispatchEvent(new KeyboardEvent('keydown',{key:'P',ctrlKey:true,shiftKey:true,bubbles:true}))",
         sleep=0.5,
         js=("(function(){var m=document.getElementById('palette-mask');"
             "if(!m.classList.contains('open'))return 'not open';"
             "var t=document.getElementById('pal-list').textContent;"
             "if(t.indexOf(" + JV("保存") + ")<0)return 'text='+t.slice(0,80);"
             "if(t.indexOf(" + JV("打字机") + ")>=0)return 'typewriter still listed';"
             "return true;})()"),
         setup2="document.dispatchEvent(new KeyboardEvent('keydown',{key:'Escape',bubbles:true}))",
         sleep2=0.3,
         js2="!document.getElementById('palette-mask').classList.contains('open')"),

    dict(name="T45 工具栏搜索按钮:打开仓库搜索面板",
         setup="document.getElementById('btn-search').click()",
         sleep=0.4,
         js=("(function(){var m=document.getElementById('palette-mask');"
             "if(!m.classList.contains('open'))return 'not open';"
             "var h=document.getElementById('pal-hint').textContent;"
             "if(h.indexOf(" + JV("搜索") + ")<0 && h.indexOf(" + JV("语义") + ")<0)return 'hint='+h;"
             "var modes=document.getElementById('pal-modes');"
             "if(!modes||modes.hidden)return 'no modes';"
             "if(!modes.querySelector('[data-mode=title]')||!modes.querySelector('[data-mode=content]')||!modes.querySelector('[data-mode=semantic]'))return 'missing mode';"
             "return true;})()"),
         setup2="document.dispatchEvent(new KeyboardEvent('keydown',{key:'Escape',bubbles:true}))",
         sleep2=0.3,
         js2="!document.getElementById('palette-mask').classList.contains('open')"),

    dict(name="T45a 标题搜索:打开仓库后输入文件名出现命中",
         setup=("window.App.openPath(" + JV(str(REPO_DIR)) + ").then(function(){"
                "document.getElementById('btn-search').click();"
                "var el=document.getElementById('pal-input');"
                "el.value='readme';el.dispatchEvent(new Event('input',{bubbles:true}));});"),
         sleep=1.2,
         js=("(function(){if(!document.getElementById('palette-mask').classList.contains('open'))return 'not open';"
             "var t=document.getElementById('pal-list').textContent;"
             "return t.toLowerCase().indexOf('readme')>=0?true:'list='+t.slice(0,100);})()"),
         setup2="document.dispatchEvent(new KeyboardEvent('keydown',{key:'Escape',bubbles:true}))",
         sleep2=0.3,
         js2="!document.getElementById('palette-mask').classList.contains('open')"),

    dict(name="T45b 内容搜索:切换内容模式命中正文",
         setup=("window.Palette.openSearch();"
                "var btn=document.querySelector('#pal-modes [data-mode=content]');"
                "if(btn)btn.dispatchEvent(new MouseEvent('mousedown',{bubbles:true,cancelable:true}));"
                "var el=document.getElementById('pal-input');"
                "el.value=" + JV("子目录") + ";"
                "el.dispatchEvent(new Event('input',{bubbles:true}));"),
         sleep=1.0,
         js=("(function(){var t=document.getElementById('pal-list').textContent;"
             "if(t.indexOf('note.md')<0)return 'list='+t.slice(0,100);"
             "if(t.indexOf(" + JV("内容") + ")<0)return 'no content badge';"
             "return true;})()"),
         setup2="document.dispatchEvent(new KeyboardEvent('keydown',{key:'Escape',bubbles:true}))",
         sleep2=0.3,
         js2="!document.getElementById('palette-mask').classList.contains('open')"),

    dict(name="T45c 语义搜索:输入后提示 Enter 且不自动请求",
         setup=("window.Palette.openSearch();"
                "var btn=document.querySelector('#pal-modes [data-mode=semantic]');"
                "if(btn)btn.dispatchEvent(new MouseEvent('mousedown',{bubbles:true,cancelable:true}));"
                "var el=document.getElementById('pal-input');"
                "el.value=" + JV("ideas about consensus") + ";"
                "el.dispatchEvent(new Event('input',{bubbles:true}));"),
         sleep=0.6,
         js=("(function(){var empty=document.getElementById('pal-empty');"
             "var t=(empty&&empty.style.display!=='none')?empty.textContent:'';"
             "if(t.indexOf('Enter')<0&&t.indexOf(" + JV("语义") + ")<0)return 'empty='+t;"
             "return true;})()"),
         setup2="document.dispatchEvent(new KeyboardEvent('keydown',{key:'Escape',bubbles:true}))",
         sleep2=0.3,
         js2="!document.getElementById('palette-mask').classList.contains('open')"),

    dict(name="T46 本文查找 Ctrl+F:查找条打开并可关闭",
         setup="window.dispatchEvent(new KeyboardEvent('keydown',{key:'f',ctrlKey:true,bubbles:true}))",
         sleep=0.3,
         js="document.getElementById('find-bar').classList.contains('open')&&!!document.getElementById('find-input')",
         setup2="document.getElementById('find-close').click()",
         sleep2=0.3,
         js2="!document.getElementById('find-bar').classList.contains('open')"),

    dict(name="T47 自动保存开关默认开启",
         setup="document.getElementById('btn-settings').click()", sleep=0.4,
         js="document.getElementById('set-autosave').classList.contains('on')",
         setup2="document.getElementById('set-close').click()", sleep2=0.3,
         js2="!document.getElementById('settings-mask').classList.contains('open')"),

    dict(name="T48 每日笔记 API:创建并打开当日文档",
         setup=("window.__t48=0;"
                "window.pywebview.api.open_daily_note('" + REPO_JS + "'," + JV("日记") + ").then(function(r){"
                "window.__t48=r&&r.ok&&r.content?1:-1;window.__t48path=r&&r.path;});"),
         sleep=0.8,
         js="window.__t48===1?true:'t48='+window.__t48",
         timeout=10),

    dict(name="T49 insertValue 与 getHTML 可用",
         setup="window.Editor.setValue(" + JV("# 导出测试\n\n正文") + ")",
         sleep=0.8,
         js=("(function(){window.Editor.insertValue('\\n\\nINSERT-E2E');"
             "var v=window.Editor.getValue();"
             "if(v.indexOf('INSERT-E2E')<0)return 'no insert:'+v.slice(0,40);"
             "var h=window.Editor.getHTML();"
             "return (typeof h==='string')?true:'html='+typeof h;})()")),

    dict(name="T51 目录右键菜单:新建笔记/文件夹/资源管理器三项",
         setup=("window.Sidebar.renderTree([{type:'dir',name:'docs',path:'/d/docs',children:[]},"
                "{type:'file',name:'a.md',path:'/d/a.md'}],'root');"
                "var el=document.querySelector('#file-tree .tree-item.dir');"
                "el.dispatchEvent(new MouseEvent('contextmenu',{bubbles:true,cancelable:true,clientX:220,clientY:220}));"),
         sleep=0.4,
         js=("(function(){var m=document.getElementById('tree-menu');"
             "if(!m.classList.contains('open'))return 'not open';"
             "var items=m.querySelectorAll('.ctx-item');"
             "if(items.length!==3)return 'items='+items.length;"
             "if(items[0].getAttribute('data-act')!=='new-file')return 'act0='+items[0].getAttribute('data-act');"
             "return true;})()"),
         setup2="document.body.dispatchEvent(new MouseEvent('click',{bubbles:true}))", sleep2=0.3,
         js2="!document.getElementById('tree-menu').classList.contains('open')"),

    dict(name="T52 标题跳转:jumpToHeading 滚离顶部",
         # T38 会把模式留在 sv；源码面板与 IR 面板不是同一个滚动容器。
         # 先切回 ir（重建中 setValue 会进 pendingValue），再装 80 章与 T06 同构。
         setup=("(function(){window.Editor.setMode('ir');"
                "var s='';for(var i=0;i<80;i++){s+='## '+(" + JV('章节') + ")+i+'\\n\\n'+(" + JV('内容') + ")+'\\n';}"
                "window.Editor.setValue(s);})()"),
         sleep=2.2,
         js=("window.Editor.getMode()==='ir'&&window.Editor.isReady()"
             "&&document.querySelectorAll('#outline-list .outline-item').length>=61"),
         timeout=18,
         setup2=("(function(){var it=document.querySelectorAll('#outline-list .outline-item');"
                 "if(it.length>60){window.__t52h=it[60].textContent;window.Editor.jumpToHeading(window.__t52h);}})()"),
         sleep2=1.0,
         js2=("(function(){var sc=document.querySelector('#editor .vditor-ir .vditor-reset');"
              "var mode=window.Editor.getMode();"
              "return sc&&sc.scrollTop>1000?true:'scrollTop='+(sc?sc.scrollTop:'null')+' mode='+mode+' h='+window.__t52h;})()")),

    dict(name="T53 编辑区缩放:adjustZoom 更新状态栏与 zoom 样式",
         setup="window.App.applyZoom(120,true)",
         sleep=0.3,
         js=("(function(){var z=document.getElementById('sb-zoom').textContent;"
             "var ed=document.getElementById('editor');"
             "if(z.indexOf('120')<0)return 'badge='+z;"
             "if(ed.style.zoom!=='1.2')return 'zoom='+ed.style.zoom;"
             "return true;})()"),
         setup2="window.App.applyZoom(100,true)",
         sleep2=0.2,
         js2="document.getElementById('sb-zoom').textContent.indexOf('100')>=0"),

    dict(name="T54 侧栏链接页签存在并可切换",
         setup="document.querySelector('.side-tab[data-panel=\"links\"]').click()",
         sleep=0.3,
         js="document.getElementById('panel-links').classList.contains('active')",
         setup2="document.querySelector('.side-tab[data-panel=\"files\"]').click()",
         sleep2=0.3,
         js2="document.getElementById('panel-files').classList.contains('active')"),

    dict(name="T55 命令面板空查询可列出项、输入可出现新建",
         setup="window.Palette.openFiles();",
         sleep=0.5,
         js="document.getElementById('palette-mask').classList.contains('open')",
         setup2=("document.getElementById('pal-input').value='e2e-create-note';"
                 "document.getElementById('pal-input').dispatchEvent(new Event('input',{bubbles:true}));"),
         sleep2=0.5,
         js2=("(function(){var t=document.getElementById('pal-list').textContent;"
              "var ok=t.indexOf(" + JV("新建") + ")>=0;"
              "document.dispatchEvent(new KeyboardEvent('keydown',{key:'Escape',bubbles:true}));"
              "return ok?true:'text='+t.slice(0,100);})()")),

    dict(name="T56 查找替换:Ctrl+H 展开替换行且全部替换生效",
         setup=("window.Editor.setValue('alpha alpha beta');"
                "window.FindBar.open({replace:true});"
                "document.getElementById('find-input').value='alpha';"
                "document.getElementById('replace-input').value='zz';"),
         sleep=0.5,
         js=("document.getElementById('find-bar').classList.contains('open')"
             "&&document.getElementById('find-bar').classList.contains('replace-open')"
             "&&!!document.getElementById('replace-input')"),
         setup2="document.getElementById('replace-all').click()",
         sleep2=0.5,
         js2=("(function(){var v=window.Editor.getValue();"
              "document.getElementById('find-close').click();"
              "return v.indexOf('zz zz beta')>=0?true:'val='+v.slice(0,40);})()")),

    dict(name="T57 命令面板含查找替换与复制路径",
         setup="window.Palette.openCommands();",
         sleep=0.5,
         js=("(function(){var t=document.getElementById('pal-list').textContent;"
             "if(t.indexOf(" + JV("查找替换") + ")<0)return 'no replace';"
             "if(t.indexOf(" + JV("复制当前路径") + ")<0)return 'no path';"
             "return true;})()"),
         setup2="document.dispatchEvent(new KeyboardEvent('keydown',{key:'Escape',bubbles:true}))",
         sleep2=0.3,
         js2="!document.getElementById('palette-mask').classList.contains('open')"),

    dict(name="T58 仓库待办面板:提示文案并可关闭",
         setup="window.Palette.openTasks();",
         sleep=0.5,
         js=("(function(){var m=document.getElementById('palette-mask');"
             "if(!m.classList.contains('open'))return 'not open';"
             "var h=document.getElementById('pal-hint').textContent;"
             "if(h.indexOf(" + JV("待办") + ")<0)return 'hint='+h;"
             "return true;})()"),
         setup2="document.dispatchEvent(new KeyboardEvent('keydown',{key:'Escape',bubbles:true}))",
         sleep2=0.3,
         js2="!document.getElementById('palette-mask').classList.contains('open')"),

    dict(name="T59 可读宽度:开关后 editor-wrap 带 class",
         setup="window.App.applyReadable(true, true);",
         sleep=0.3,
         js="document.getElementById('editor-wrap').classList.contains('readable-width')",
         setup2="window.App.applyReadable(false, true);",
         sleep2=0.3,
         js2="!document.getElementById('editor-wrap').classList.contains('readable-width')"),

    dict(name="T60 命令面板含转到行与可读宽度",
         setup="window.Palette.openCommands();",
         sleep=0.5,
         js=("(function(){var t=document.getElementById('pal-list').textContent;"
             "if(t.indexOf(" + JV("转到行") + ")<0)return 'no goto';"
             "if(t.indexOf(" + JV("可读宽度") + ")<0)return 'no readable';"
             "if(t.indexOf(" + JV("快速收集") + ")<0)return 'no capture';"
             "if(t.indexOf(" + JV("折叠全部目录") + ")<0)return 'no fold';"
             "return true;})()"),
         setup2="document.dispatchEvent(new KeyboardEvent('keydown',{key:'Escape',bubbles:true}))",
         sleep2=0.3,
         js2="!document.getElementById('palette-mask').classList.contains('open')"),

    dict(name="T62 设置弹窗:Esc 关闭且 dialog 无障碍",
         setup="document.getElementById('btn-settings').click()",
         sleep=0.4,
         js=("(function(){var m=document.getElementById('settings-mask');"
             "if(!m.classList.contains('open'))return 'not open';"
             "var d=m.querySelector('[role=dialog]');"
             "if(!d||d.getAttribute('aria-modal')!=='true')return 'no dialog';"
             "if(!document.getElementById('btn-home').getAttribute('aria-label'))return 'no toolbar aria';"
             "return true;})()"),
         setup2="window.dispatchEvent(new KeyboardEvent('keydown',{key:'Escape',bubbles:true}))",
         sleep2=0.4,
         js2="!document.getElementById('settings-mask').classList.contains('open')"),

    dict(name="T63 状态栏视图开关:可读宽度按钮可切换",
         setup="document.getElementById('sb-readable').click()",
         sleep=0.3,
         js=("document.getElementById('editor-wrap').classList.contains('readable-width')"
             "&&document.getElementById('sb-readable').classList.contains('active')"
             "&&!!!document.getElementById('sb-focus')&&!!!document.getElementById('sb-typewriter')"),
         setup2="document.getElementById('sb-readable').click()",
         sleep2=0.3,
         js2="!document.getElementById('editor-wrap').classList.contains('readable-width')"),

    dict(name="T64 侧栏拖拽手柄与默认宽度",
         js=("(function(){var h=document.getElementById('sidebar-resizer');"
             "if(!h)return 'no handle';"
             "if(h.getAttribute('role')!=='separator')return 'role';"
             "var w=getComputedStyle(document.documentElement).getPropertyValue('--sidebar-width').trim();"
             "return (w==='256px')?true:'w='+w;})()")),

    dict(name="T65 确认框:Esc 取消",
         setup=("(function(){window.App.confirm=window.__ryuuConfirm;"
                "window.__cf=null;"
                "window.App.confirm({title:"+JV("测试确认")+",message:"+JV("是否继续")+","
                "okText:"+JV("确定")+",cancelText:"+JV("取消")+"}).then(function(v){window.__cf=v;});})()"),
         sleep=0.4,
         js=("document.getElementById('confirm-mask').classList.contains('open')"
             "&&!!document.querySelector('#confirm-mask [role=dialog]')"),
         setup2="window.dispatchEvent(new KeyboardEvent('keydown',{key:'Escape',bubbles:true}))",
         sleep2=0.4,
         js2=("(function(){var open=document.getElementById('confirm-mask').classList.contains('open');"
              "var v=window.__cf;"
              "window.App.confirm=function(){return Promise.resolve(true);};"
              "return (!open&&v===false)?true:'open='+open+' cf='+v;})()")),

    dict(name="T61 清空文档:真实编辑清空后 getValue 反映空文档(不回退旧内容)",
         # 行为契约:用户清空文档后,getValue 必须如实返回空(实测 Vditor 清空后
         # 序列化为 "\n"),不得因缓存回退等原因复活装载时的旧内容。
         # 前置:T38 起模式停在 sv,先切回 ir 并装载内容
         setup="window.Editor.setMode('ir');window.Editor.setValue(" + JV('# 待清空\n\n正文 XYZ') + ");",
         sleep=1.5,
         js="window.Editor.getMode()==='ir'&&window.Editor.isReady()&&window.Editor.getValue().indexOf('XYZ')>=0",
         timeout=15,
         # 第二阶段:模拟真实用户清空(聚焦→全选→删除,execCommand 产生可信 input
         # 事件,Vditor 正常处理),防抖落定后内容必须为空白
         setup2=("(function(){var el=document.querySelector('#editor .vditor-ir .vditor-reset');"
                 "if(!el)return;el.focus();document.execCommand('selectAll');document.execCommand('delete');})()"),
         sleep2=1.2,
         js2=("(function(){var v=window.Editor.getValue();"
              "return v.trim()===''?true:'not empty len='+v.length+' head='+JSON.stringify(v.slice(0,20));})()")),

    # ---------------------------------------------------------------
    # Mermaid / 公式 / 段落转换（对标 Typora）
    # ---------------------------------------------------------------
    dict(name="T70 mermaid:流程图代码块渲染为 SVG",
         setup="window.Editor.setMode('ir');window.Editor.setValue("
               + JV('# 图表\n\n```mermaid\nflowchart TD\n    A[甲] --> B[乙]\n```\n') + ");",
         sleep=2.5,
         js="!!document.querySelector('#editor .vditor-ir__preview .language-mermaid svg')",
         timeout=15),

    dict(name="T71 mermaid:切换主题后图按新主题重渲染(svg 产物更新)",
         # 前置:T70 已渲染。记录当前 svg,切暗色,断言 svg 被重新生成
         setup=("(function(){var s=document.querySelector('#editor .vditor-ir__preview .language-mermaid svg');"
                "window.__mmdHtml=s?s.outerHTML:'none';window.Editor.setTheme('dark');})()"),
         sleep=2.0,
         js=("(function(){var s=document.querySelector('#editor .vditor-ir__preview .language-mermaid svg');"
             "if(!s)return 'svg missing';"
             "return s.outerHTML!==window.__mmdHtml?true:'not re-rendered';})()"),
         timeout=12,
         # 恢复亮色,避免影响后续用例的阅读体验一致性
         setup2="window.Editor.setTheme('light');",
         sleep2=1.5,
         js2="!!document.querySelector('#editor .vditor-ir__preview .language-mermaid svg')"),

    dict(name="T72 公式:KaTeX 渲染行内与块级公式",
         setup="window.Editor.setValue("
               + JV('# 公式\n\n行内 $E=mc^2$ 测试\n\n$$\n\\frac{a}{b} = \\sqrt{x}\n$$\n') + ");",
         sleep=2.0,
         js="!!document.querySelector('#editor .katex')",
         timeout=15),

    dict(name="T73 公式:切换 MathJax 引擎后重建并渲染(mjx-container)",
         # setMathEngine 走保内容重建(refresh),T72 的公式内容在重建后以 MathJax 重渲染
         setup="window.Editor.setMathEngine('mathjax');",
         sleep=3.0,
         js=("window.Editor.isReady()"
             "&&!!document.querySelector('#editor mjx-container')"),
         timeout=25,
         # 切回 KaTeX 还原默认,断言 katex 节点回归
         setup2="window.Editor.setMathEngine('katex');",
         sleep2=3.0,
         js2=("window.Editor.isReady()"
              "&&!!document.querySelector('#editor .katex')"),
         timeout2=25),

    dict(name="T74 段落转换:正文→二级标题(右键菜单全链路)",
         setup=("window.Editor.setValue(" + JV('转换源\n\n段落文字 DEF\n') + ");"
                "setTimeout(function(){"
                "var p=[].slice.call(document.querySelectorAll('#editor .vditor-ir .vditor-reset > p'))"
                ".find(function(x){return x.textContent.indexOf('DEF')>=0;});"
                "if(!p)return;"
                "var r=document.createRange();r.selectNodeContents(p);r.collapse(false);"
                "var s=window.getSelection();s.removeAllRanges();s.addRange(r);"
                "p.dispatchEvent(new MouseEvent('contextmenu',{bubbles:true,clientX:120,clientY:120}));"
                "setTimeout(function(){"
                "var it=[].slice.call(document.querySelectorAll('#slash-menu .slash-item'))"
                ".find(function(el){var t=el.querySelector('.si-title');return t&&t.textContent==='二级标题';});"
                "if(it)it.dispatchEvent(new MouseEvent('mousedown',{bubbles:true}));"
                "},300);},400);"),
         sleep=2.5,
         js="window.Editor.getValue().indexOf('## 段落文字 DEF')>=0",
         timeout=12),

    dict(name="T75 段落转换:标题→无序列表",
         # 前置:T74 已得到 ## 段落文字 DEF
         setup=("setTimeout(function(){"
                "var h=[].slice.call(document.querySelectorAll('#editor .vditor-ir .vditor-reset > h2'))"
                ".find(function(x){return x.textContent.indexOf('DEF')>=0;});"
                "if(!h)return;"
                "var r=document.createRange();r.selectNodeContents(h);r.collapse(false);"
                "var s=window.getSelection();s.removeAllRanges();s.addRange(r);"
                "h.dispatchEvent(new MouseEvent('contextmenu',{bubbles:true,clientX:120,clientY:120}));"
                "setTimeout(function(){"
                "var it=[].slice.call(document.querySelectorAll('#slash-menu .slash-item'))"
                ".find(function(el){var t=el.querySelector('.si-title');return t&&t.textContent==='无序列表';});"
                "if(it)it.dispatchEvent(new MouseEvent('mousedown',{bubbles:true}));"
                "},300);},300);"),
         sleep=2.5,
         js="window.Editor.getValue().indexOf('- 段落文字 DEF')>=0",
         timeout=12),

    dict(name="T76 段落转换:框选两段→引用(跨块)",
         setup=("window.Editor.setValue(" + JV('甲 AAA\n\n乙 BBB\n') + ");"
                "setTimeout(function(){"
                "var ps=[].slice.call(document.querySelectorAll('#editor .vditor-ir .vditor-reset > p'));"
                "var p1=ps.find(function(x){return x.textContent.indexOf('AAA')>=0;});"
                "var p2=ps.find(function(x){return x.textContent.indexOf('BBB')>=0;});"
                "if(!p1||!p2)return;"
                "var r=document.createRange();r.selectNodeContents(p1);"
                "var r2=document.createRange();r2.selectNodeContents(p2);"
                "r.setEnd(r2.endContainer,r2.endOffset);"
                "var s=window.getSelection();s.removeAllRanges();s.addRange(r);"
                "p1.dispatchEvent(new MouseEvent('contextmenu',{bubbles:true,clientX:120,clientY:120}));"
                "setTimeout(function(){"
                "var it=[].slice.call(document.querySelectorAll('#slash-menu .slash-item'))"
                ".find(function(el){var t=el.querySelector('.si-title');return t&&t.textContent==='引用';});"
                "if(it)it.dispatchEvent(new MouseEvent('mousedown',{bubbles:true}));"
                "},300);},400);"),
         sleep=2.5,
         js=("(function(){var v=window.Editor.getValue();"
             "return v.indexOf('> 甲 AAA')>=0&&v.indexOf('> 乙 BBB')>=0?true:'got:'+JSON.stringify(v);})()"),
         timeout=12),

    dict(name="T77 段落转换:源码模式当前行→待办",
         # 第一阶段:切 sv 并等重建就绪(setMode 重建实测 ~2s,单阶段 1500ms 临界会败)
         setup="window.Editor.setMode('sv');",
         sleep=1.0,
         js="window.Editor.getMode()==='sv'&&window.Editor.isReady()",
         timeout=20,
         # 第二阶段:装载单行,选中行尾,右键→待办列表
         setup2=("window.Editor.setValue(" + JV('源码行 GHI\n') + ");"
                 "setTimeout(function(){"
                 "var el=document.querySelector('#editor .vditor-sv');"
                 "if(!el)return;"
                 "var r=document.createRange();"
                 "var t=el.querySelector('div')||el.firstChild;"
                 "if(t){r.selectNodeContents(t);r.collapse(false);"
                 "var s=window.getSelection();s.removeAllRanges();s.addRange(r);}"
                 "el.dispatchEvent(new MouseEvent('contextmenu',{bubbles:true,clientX:120,clientY:120}));"
                 "setTimeout(function(){"
                 "var it=[].slice.call(document.querySelectorAll('#slash-menu .slash-item'))"
                 ".find(function(e){var x=e.querySelector('.si-title');return x&&x.textContent==='待办列表';});"
                 "if(it)it.dispatchEvent(new MouseEvent('mousedown',{bubbles:true}));"
                 "},300);},600);"),
         sleep2=3.0,
         js2="window.Editor.getValue().indexOf('- [ ] 源码行 GHI')>=0",
         timeout2=15),

    dict(name="T78 图表命令:/过滤可命中 mermaid 模板(流程图/思维导图)",
         # 顺手把 T77 的 sv 模式还原为 ir,保持收尾环境一致
         setup="window.Editor.setMode('ir');",
         sleep=1.0,
         js=("(function(){"
             "var a=window.filterCommands('flowchart','notion');"
             "var b=window.filterCommands('lct','wolai');"
             "var c=window.filterCommands('思维导图','notion');"
             "var h=window.filterCommands('h1','notion');"
             "var w=window.filterCommands('bt1','wolai');"
             "return (a.length&&a[0].id==='mmd-flow'&&b.length&&b[0].id==='mmd-flow'"
             "&&c.length&&c[0].id==='mmd-mindmap'"
             "&&h.length&&h[0].id==='h1'&&w.length&&w[0].id==='h1')"
             "?true:'a='+JSON.stringify(a[0]&&a[0].id)"
             "+' b='+JSON.stringify(b[0]&&b[0].id)+' c='+JSON.stringify(c[0]&&c[0].id)"
             "+' h='+JSON.stringify(h[0]&&h[0].id)+' w='+JSON.stringify(w[0]&&w[0].id);})()"),
         timeout=8),

    # ---------------------------------------------------------------
    # 表格 / 代码块 / 数学块 右键块操作
    # ---------------------------------------------------------------
    dict(name="T79 表格:向下插入行→删除列(右键全链路)",
         setup=("window.Editor.setMode('ir');window.Editor.setValue("
               + JV('| 名称 | 数量 | 备注 |\n| :--- | :---: | ---: |\n| 苹果 | 3 | 新到 |\n| 香蕉 | 5 | 打折 |\n')
               + ");"
               "window.__ryuuClickMenu=function(cellText,title){"
                "var c=[].slice.call(document.querySelectorAll('#editor .vditor-ir table td,#editor .vditor-ir table th'))"
                ".find(function(x){return x.textContent.indexOf(cellText)>=0;});"
                "if(!c)return 'no-cell';"
                "var r=document.createRange();r.selectNodeContents(c);r.collapse(false);"
                "var s=window.getSelection();s.removeAllRanges();s.addRange(r);"
                "c.dispatchEvent(new MouseEvent('contextmenu',{bubbles:true,clientX:120,clientY:120}));"
                "setTimeout(function(){"
                "var it=[].slice.call(document.querySelectorAll('#slash-menu .slash-item'))"
                ".find(function(el){var t=el.querySelector('.si-title');return t&&t.textContent===title;});"
                "if(it)it.dispatchEvent(new MouseEvent('mousedown',{bubbles:true}));"
                "},300);return 'ok';};"
               "setTimeout(function(){window.__ryuuClickMenu('3','向下插入行');},600);"),
         sleep=3.0,
         js=("(function(){var v=window.Editor.getValue();"
             "var rows=v.split('\\n').filter(function(l){return l.trim().charAt(0)==='|';});"
             "return rows.length===5?true:'rows='+rows.length+' v='+JSON.stringify(v);})()"),
         timeout=15,
         # 第二阶段:光标移到「打折」列并删除该列。
         # 注意 phase1 的块操作走 setValue 全文重载,getValue 先于 DOM 重渲染完成;
         # 轮询等单元格真实出现在 DOM 后再点菜单,避免右键时块还未渲染出来
         setup2=("var __t=0;var __timer=setInterval(function(){"
                 "var c=[].slice.call(document.querySelectorAll('#editor .vditor-ir table td'))"
                 ".find(function(x){return x.textContent.indexOf('打折')>=0;});"
                 "if(c){clearInterval(__timer);window.__ryuuClickMenu('打折','删除列');}"
                 "else if(++__t>20)clearInterval(__timer);"
                 "},300);"),
         sleep2=4.0,
         js2=("(function(){var v=window.Editor.getValue();"
              "return (v.indexOf('备注')<0&&v.indexOf('| 名称 | 数量 |')>=0)?true:'v='+JSON.stringify(v);})()"),
         timeout2=15),

    dict(name="T80 代码块:转换为普通文本",
         setup=("window.Editor.setValue(" + JV('前置段落\n\n```\nline1\nline2\n```\n') + ");"
               "setTimeout(function(){"
               "var pre=document.querySelector('#editor .vditor-ir [data-type=\"code-block\"] pre');"
               "if(!pre)return;"
               "var r=document.createRange();r.selectNodeContents(pre);r.collapse(false);"
               "var s=window.getSelection();s.removeAllRanges();s.addRange(r);"
               "pre.dispatchEvent(new MouseEvent('contextmenu',{bubbles:true,clientX:120,clientY:120}));"
               "setTimeout(function(){"
               "var it=[].slice.call(document.querySelectorAll('#slash-menu .slash-item'))"
               ".find(function(el){var t=el.querySelector('.si-title');return t&&t.textContent==='转换为普通文本';});"
               "if(it)it.dispatchEvent(new MouseEvent('mousedown',{bubbles:true}));"
               "},300);},600);"),
         sleep=3.0,
         js=("(function(){var v=window.Editor.getValue();"
             "return (v.indexOf('```')<0&&v.indexOf('line1')>=0&&v.indexOf('line2')>=0)"
             "?true:'v='+JSON.stringify(v);})()"),
         timeout=15),

    dict(name="T81 代码块:删除块",
         setup=("window.Editor.setValue(" + JV('保留段\n\n```\nlineDel\n```\n') + ");"
               "setTimeout(function(){"
               "var pre=document.querySelector('#editor .vditor-ir [data-type=\"code-block\"] pre');"
               "if(!pre)return;"
               "var r=document.createRange();r.selectNodeContents(pre);r.collapse(false);"
               "var s=window.getSelection();s.removeAllRanges();s.addRange(r);"
               "pre.dispatchEvent(new MouseEvent('contextmenu',{bubbles:true,clientX:120,clientY:120}));"
               "setTimeout(function(){"
               "var it=[].slice.call(document.querySelectorAll('#slash-menu .slash-item'))"
               ".find(function(el){var t=el.querySelector('.si-title');return t&&t.textContent==='删除代码块';});"
               "if(it)it.dispatchEvent(new MouseEvent('mousedown',{bubbles:true}));"
               "},300);},600);"),
         sleep=3.0,
         js=("(function(){var v=window.Editor.getValue();"
             "return (v.indexOf('lineDel')<0&&v.indexOf('保留段')>=0)?true:'v='+JSON.stringify(v);})()"),
         timeout=15),

    dict(name="T82 数学块:转换为普通文本→再删除",
         setup=("window.Editor.setValue(" + JV('数学前置\n\n$$\nE = mc^2\n$$\n') + ");"
               "setTimeout(function(){"
               "var pre=document.querySelector('#editor .vditor-ir [data-type=\"math-block\"] pre');"
               "if(!pre)return;"
               "var r=document.createRange();r.selectNodeContents(pre);r.collapse(false);"
               "var s=window.getSelection();s.removeAllRanges();s.addRange(r);"
               "pre.dispatchEvent(new MouseEvent('contextmenu',{bubbles:true,clientX:120,clientY:120}));"
               "setTimeout(function(){"
               "var it=[].slice.call(document.querySelectorAll('#slash-menu .slash-item'))"
               ".find(function(el){var t=el.querySelector('.si-title');return t&&t.textContent==='转换为普通文本';});"
               "if(it)it.dispatchEvent(new MouseEvent('mousedown',{bubbles:true}));"
               "},300);},600);"),
         sleep=3.0,
         js=("(function(){var v=window.Editor.getValue();"
             "return (v.indexOf('$$')<0&&v.indexOf('E = mc^2')>=0)?true:'v='+JSON.stringify(v);})()"),
         timeout=15,
         # 第二阶段:重新装载数学块,点「删除公式块」
         setup2=("window.Editor.setValue(" + JV('数学前置\n\n$$\nx+y=1\n$$\n') + ");"
                "setTimeout(function(){"
                "var pre=document.querySelector('#editor .vditor-ir [data-type=\"math-block\"] pre');"
                "if(!pre)return;"
                "var r=document.createRange();r.selectNodeContents(pre);r.collapse(false);"
                "var s=window.getSelection();s.removeAllRanges();s.addRange(r);"
                "pre.dispatchEvent(new MouseEvent('contextmenu',{bubbles:true,clientX:120,clientY:120}));"
                "setTimeout(function(){"
                "var it=[].slice.call(document.querySelectorAll('#slash-menu .slash-item'))"
                ".find(function(el){var t=el.querySelector('.si-title');return t&&t.textContent==='删除公式块';});"
                "if(it)it.dispatchEvent(new MouseEvent('mousedown',{bubbles:true}));"
                "},300);},600);"),
         sleep2=3.0,
         js2=("(function(){var v=window.Editor.getValue();"
              "return (v.indexOf('x+y=1')<0&&v.indexOf('数学前置')>=0)?true:'v='+JSON.stringify(v);})()"),
         timeout2=15),

    dict(name="T83 数学块:复制公式源码(execCommand copy 接线)",
         setup=("window.Editor.setValue(" + JV('$$\nE = mc^2\n$$\n') + ");"
               "window.__cpCalls=[];"
               "window.__origExec=document.execCommand;"
               "document.execCommand=function(c){window.__cpCalls.push(c);"
               "return window.__origExec.apply(document,arguments);};"
               "setTimeout(function(){"
               "var pre=document.querySelector('#editor .vditor-ir [data-type=\"math-block\"] pre');"
               "if(!pre)return;"
               "var r=document.createRange();r.selectNodeContents(pre);r.collapse(false);"
               "var s=window.getSelection();s.removeAllRanges();s.addRange(r);"
               "pre.dispatchEvent(new MouseEvent('contextmenu',{bubbles:true,clientX:120,clientY:120}));"
               "setTimeout(function(){"
               "var it=[].slice.call(document.querySelectorAll('#slash-menu .slash-item'))"
               ".find(function(el){var t=el.querySelector('.si-title');return t&&t.textContent==='复制公式源码';});"
               "if(it)it.dispatchEvent(new MouseEvent('mousedown',{bubbles:true}));"
               "},300);},600);"),
         sleep=3.0,
         js=("(function(){var ok=window.__cpCalls.indexOf('copy')>=0;"
             "document.execCommand=window.__origExec;"
             "return ok?true:'no copy call';})()"),
         timeout=15),

    dict(name="T84 双链装饰:IR 文本 [[]] 渲染为 .wiki-link(守卫曾命中 pre 根致全灭)",
         setup="window.Editor.setValue(" + JV('前文\n\n双链 [[样式总览]] 与 [[notes/随手记]]\n') + ")",
         sleep=1.2,
         js=("(function(){var ws=document.querySelectorAll('.vditor-reset .wiki-link');"
             "if(ws.length!==2)return 'count='+ws.length;"
             "if(ws[0].dataset.wiki!=='样式总览')return 'wiki='+ws[0].dataset.wiki;"
             "if(ws[1].dataset.wiki!=='notes/随手记')return 'wiki1='+ws[1].dataset.wiki;"
             "return true;})()"),
         timeout=12),

    # —— 段落转换矩阵（convert.js applyIR）：块提取/变换/替换结果断言 ——
    dict(name="T85 段落转换:正文→二级标题(单块光标)",
         setup=("window.Editor.setValue(" + JV('第一段\n\n第二段\n') + ");"
                "setTimeout(function(){"
                "var b=[].filter.call(document.querySelector('.vditor-ir .vditor-reset').children,"
                "function(c){return c.tagName==='P';})[0];"
                "var r=document.createRange();r.selectNodeContents(b);r.collapse(true);"
                "var s=window.getSelection();s.removeAllRanges();s.addRange(r);"
                "window.Convert.apply('h2', window.Editor);},600);"),
         sleep=2.0,
         js=("(function(){var v=window.Editor.getValue();"
             "return v.indexOf('## 第一段')>=0&&v.indexOf('第二段')>=0&&v.indexOf('## 第二段')<0"
             "?true:'val='+v.slice(0,60);})()"),
         timeout=12),

    dict(name="T86 段落转换:框选两个标题→无序列表",
         setup=("window.Editor.setValue(" + JV('## 甲\n\n## 乙\n') + ");"
                "setTimeout(function(){"
                "var hs=[].filter.call(document.querySelector('.vditor-ir .vditor-reset').children,"
                "function(c){return c.tagName==='H2';});"
                "var r=document.createRange();"
                "r.setStartBefore(hs[0]);r.setEndAfter(hs[hs.length-1]);"
                "var s=window.getSelection();s.removeAllRanges();s.addRange(r);"
                "window.Convert.apply('ul', window.Editor);},600);"),
         sleep=2.0,
         js=("(function(){var v=window.Editor.getValue();"
             "return v.indexOf('- 甲')>=0&&v.indexOf('- 乙')>=0&&v.indexOf('##')<0"
             "?true:'val='+v.slice(0,80);})()"),
         timeout=12),

    dict(name="T87 段落转换:嵌套列表→待办(保留嵌套不展平)",
         setup=("window.Editor.setValue(" + JV('- parentA\n  - childB\n- parentC\n') + ");"
                "setTimeout(function(){"
                "var ul=document.querySelector('.vditor-ir .vditor-reset > ul');"
                "var r=document.createRange();r.selectNodeContents(ul);r.collapse(true);"
                "var s=window.getSelection();s.removeAllRanges();s.addRange(r);"
                "window.Convert.apply('todo', window.Editor);},600);"),
         sleep=2.0,
         js=("(function(){var v=window.Editor.getValue();"
             "var ls=v.split('\\n').filter(function(l){return l.trim()!=='';});"
             "if(ls.length!==3)return 'lines:'+JSON.stringify(v);"
             "if(ls[0].indexOf('- [ ]')!==0||ls[0].indexOf('parentA')<0)return 'l0:'+JSON.stringify(v);"
             "if(!/^\\s+/.test(ls[1])||ls[1].indexOf('- [ ]')<0||ls[1].indexOf('childB')<0)"
             "return 'l1 not nested:'+JSON.stringify(v);"
             "if(ls[2].indexOf('- [ ]')!==0||ls[2].indexOf('parentC')<0)return 'l2:'+JSON.stringify(v);"
             "return true;})()"),
         timeout=12),

    dict(name="T88 段落转换:待办→标题(剥 - [ ] 标记)",
         setup=("window.Editor.setValue(" + JV('- [ ] 任务甲\n- [x] 任务乙\n') + ");"
                "setTimeout(function(){"
                "var ul=document.querySelector('.vditor-ir .vditor-reset > ul');"
                "var r=document.createRange();r.selectNodeContents(ul);r.collapse(true);"
                "var s=window.getSelection();s.removeAllRanges();s.addRange(r);"
                "window.Convert.apply('h2', window.Editor);},600);"),
         sleep=2.0,
         js=("(function(){var v=window.Editor.getValue();"
             "return v.indexOf('## 任务甲')>=0&&v.indexOf('## 任务乙')>=0&&v.indexOf('- [')<0"
             "?true:'val='+v.slice(0,80);})()"),
         timeout=12),

    dict(name="T90 右键文件组:无已保存文件时所在目录项禁用",
         setup=("(function(){window.__hsf=window.App.hasSavedFile;"
                "window.App.hasSavedFile=function(){return false;};"
                "var el=document.querySelector('#editor .vditor-ir .vditor-reset');"
                "if(!el)return;"
                "el.dispatchEvent(new MouseEvent('contextmenu',{bubbles:true,cancelable:true,clientX:280,clientY:280}));})()"),
         sleep=0.5,
         js=("(function(){try{"
             "var items=document.querySelectorAll('#slash-menu .slash-item');"
             "var hit=null;"
             "for(var i=0;i<items.length;i++){"
             "if(items[i].textContent.indexOf(" + JV("所在目录") + ")>=0){hit=items[i];break;}}"
             "if(!hit)return 'no file item';"
             "return hit.classList.contains('disabled')?true:'not disabled';"
             "}finally{if(window.__hsf)window.App.hasSavedFile=window.__hsf;}})()"),
         setup2="document.dispatchEvent(new KeyboardEvent('keydown',{key:'Escape',bubbles:true}))",
         sleep2=0.3,
         js2="!document.getElementById('slash-menu').classList.contains('open')"),

    dict(name="T89 学习仓库:open_tutorial 注册并打开导读",
         setup=("window.__t89=0;window.__t89err='';"
                "window.App.openTutorial().then(function(){window.__t89=1;})"
                ".catch(function(e){window.__t89=-1;window.__t89err=String(e);});"),
         sleep=2.0,
         timeout=20,
         js=("(function(){if(window.__t89!==1)return 'pending='+window.__t89+' err='+window.__t89err;"
             "if(window.Home&&window.Home.isOpen())return 'home still open';"
             "var v=window.Editor.getValue();"
             "return v.indexOf(" + JV("学习仓库") + ")>=0?true:'content='+v.slice(0,60);})()")),

    dict(name="T92 移除全部仓库:首页回空态",
         setup=("window.__t92=0;"
                "window.pywebview.api.list_projects().then(function(r){"
                "var items=r.items||[];"
                "if(!items.length){window.Home.show();window.__t92=1;return;}"
                "var left=items.length;"
                "items.forEach(function(p){"
                "window.pywebview.api.remove_project(p.id).then(function(){"
                "left--;if(left<=0){window.Home.show();window.__t92=1;}"
                "});});});"),
         sleep=1.5,
         timeout=12,
         js="window.__t92===1?true:'pending='+window.__t92",
         setup2="",
         sleep2=0.4,
         js2=("(function(){if(!window.Home||!window.Home.isOpen())return 'home not open';"
              "var cards=document.querySelectorAll('#repo-container .repo-card').length;"
              "var rows=document.querySelectorAll('#repo-container .repo-row').length;"
              "if(cards||rows)return 'cards='+cards+' rows='+rows;"
              "if(getComputedStyle(document.getElementById('repo-empty')).display==='none')return 'empty hidden';"
              "return true;})()")),

    dict(name="T93 AI 侧栏:工具栏按钮开关与未配置空态",
         setup="document.getElementById('btn-ai').click()",
         sleep=0.3,
         js=("(function(){var p=document.getElementById('ai-sidebar');"
             "if(!p||p.hidden)return 'panel hidden';"
             "if(!document.getElementById('ai-sum-doc'))return 'no doc';"
             "if(!document.getElementById('ai-sum-vault'))return 'no vault';"
             "if(!document.getElementById('ai-knowledge'))return 'no knowledge';"
             "if(!document.getElementById('ai-ask-input'))return 'no ask';"
             "var actions=document.getElementById('ai-side-actions');"
             "if(actions&&!actions.hidden)return 'actions shown unconfigured';"
             "var t=p.textContent;"
             "if(t.indexOf(" + JV("去配置 AI") + ")<0)return 'no configure';"
             "return true;})()"),
         setup2="document.getElementById('btn-ai').click()",
         sleep2=0.2,
         js2="document.getElementById('ai-sidebar').hidden"),

    dict(name="T94 搜索 ask 前缀:面板进入 AI 回答态",
         setup=("window.Palette.openSearch();"
                "var el=document.getElementById('pal-input');"
                "el.value=" + JV("ask 什么是 WebDAV") + ";"
                "el.dispatchEvent(new Event('input',{bubbles:true}));"),
         sleep=0.5,
         js=("(function(){var pal=document.getElementById('palette');"
             "var ans=document.getElementById('pal-answer');"
             "if(!document.getElementById('palette-mask').classList.contains('open'))return 'not open';"
             "if(!(pal.classList.contains('is-ask')||(ans&&!ans.hidden)))return 'not ask';"
             "return true;})()"),
         setup2="document.dispatchEvent(new KeyboardEvent('keydown',{key:'Escape',bubbles:true}))",
         sleep2=0.3,
         js2="!document.getElementById('palette-mask').classList.contains('open')"),

    dict(name="T96 AI 侧栏:Esc 关闭",
         setup="document.getElementById('btn-ai').click()",
         sleep=0.3,
         js="document.getElementById('ai-sidebar')&&!document.getElementById('ai-sidebar').hidden",
         setup2="window.dispatchEvent(new KeyboardEvent('keydown',{key:'Escape',bubbles:true}))",
         sleep2=0.3,
         js2="document.getElementById('ai-sidebar').hidden"),

    dict(name="T95 工具栏随状态显隐:首页隐藏文档态按钮",
         setup="window.Home.show()",
         sleep=0.4,
         js=("(function(){"
             "var hid=['btn-sidebar','btn-save','btn-push-source','btn-merge-source','btn-ai','btn-reveal','btn-open-folder','btn-new'];"
             "for(var i=0;i<hid.length;i++){"
             "var b=document.getElementById(hid[i]);"
             "if(getComputedStyle(b).display!=='none')return 'shown on home:'+hid[i];}"
             "if(!document.body.classList.contains('is-home'))return 'no is-home';"
             "return true;})()"),
         setup2="window.Home.hide()",
         sleep2=0.4,
         js2=("(function(){"
              "var ids=['btn-sidebar','btn-ai'];"
              "for(var i=0;i<ids.length;i++){"
              "var b=document.getElementById(ids[i]);"
              "if(getComputedStyle(b).display==='none')return 'hidden on editor:'+ids[i];}"
              "if(!document.getElementById('sb-save'))return 'no sb-save';"
              "if(getComputedStyle(document.getElementById('btn-save')).display!=='none')return 'save still in toolbar';"
              "return !document.body.classList.contains('is-home');})()")),

    dict(name="T98 编辑方式开关:设置切到工作副本后状态栏与 body 同步",
         setup="document.getElementById('btn-settings').click()",
         sleep=0.5,
         js=("(function(){if(!document.getElementById('set-edit-mode'))return 'no edit mode';"
             "var p=document.getElementById('btn-push-source');"
             "if(!p)return 'no push btn';"
             "if(getComputedStyle(p).display!=='none')return 'push visible in source';"
             "document.querySelector('#set-edit-mode [data-v=workdir]').click();"
             "return true;})()"),
         setup2="document.getElementById('set-close').click()",
         sleep2=0.4,
         js2=("(function(){if(document.getElementById('settings-mask').classList.contains('open'))return 'settings open';"
              "if(!document.body.classList.contains('is-workdir'))return 'no is-workdir';"
              "var sb=document.getElementById('sb-workdir');"
              "if(!sb||sb.hidden)return 'no badge';"
              "if(sb.textContent.indexOf(" + JV("工作副本") + ")<0)return 'badge='+sb.textContent;"
              "return true;})()")),

    # 空行 / 选格式再输入：标题标记曾被 display:none，光标落到 # 前，
    # 键入变成「正文# 」并掉回段落；空标题回车会另起正文。
    dict(name="T99 斜杠:空行选一级标题后键入仍是标题",
         setup=("window.Home.hide();window.Editor.setMode('ir');window.Editor.setValue('');"
                "setTimeout(function(){"
                "window.Editor.focus();"
                "var p=document.querySelector('#editor .vditor-ir .vditor-reset > p')"
                "||document.querySelector('#editor .vditor-ir .vditor-reset');"
                "if(p){var r=document.createRange();r.selectNodeContents(p);r.collapse(true);"
                "var s=window.getSelection();s.removeAllRanges();s.addRange(r);}"
                "document.execCommand('insertText',false,'/');"
                "setTimeout(function(){"
                "var it=[].slice.call(document.querySelectorAll('#slash-menu .slash-item'))"
                ".find(function(el){var t=el.querySelector('.si-title');return t&&t.textContent==="
                + JV("一级标题") + ";});"
                "if(it)it.dispatchEvent(new MouseEvent('mousedown',{bubbles:true,cancelable:true}));"
                "setTimeout(function(){document.execCommand('insertText',false,"
                + JV("空行标题ABC") + ");},400);"
                "},300);},400);"),
         sleep=2.2,
         js=("(function(){var v=window.Editor.getValue();"
             "var h=document.querySelector('#editor .vditor-ir .vditor-reset > h1');"
             "if(!h||h.textContent.indexOf(" + JV("空行标题ABC") + ")<0)"
             "return 'dom='+(h?h.innerHTML.slice(0,80):'none')+' v='+JSON.stringify(v);"
             "if(v.indexOf('# 空行标题ABC')<0&&v.indexOf('#空行标题ABC')<0)"
             "return 'src='+JSON.stringify(v);"
             "return true;})()"),
         timeout=12),

    dict(name="T100 斜杠:空行选标题后回车退出为普通段落",
         setup=("window.Editor.setValue('');"
                "setTimeout(function(){"
                "window.Editor.focus();"
                "var p=document.querySelector('#editor .vditor-ir .vditor-reset > p')"
                "||document.querySelector('#editor .vditor-ir .vditor-reset');"
                "if(p){var r=document.createRange();r.selectNodeContents(p);r.collapse(true);"
                "var s=window.getSelection();s.removeAllRanges();s.addRange(r);}"
                "document.execCommand('insertText',false,'/');"
                "setTimeout(function(){"
                "var it=[].slice.call(document.querySelectorAll('#slash-menu .slash-item'))"
                ".find(function(el){var t=el.querySelector('.si-title');return t&&t.textContent==="
                + JV("一级标题") + ";});"
                "if(it)it.dispatchEvent(new MouseEvent('mousedown',{bubbles:true,cancelable:true}));"
                "setTimeout(function(){"
                "var h=document.querySelector('#editor .vditor-ir .vditor-reset > h1');"
                "(h||document.querySelector('#editor .vditor-ir .vditor-reset'))"
                ".dispatchEvent(new KeyboardEvent('keydown',{key:'Enter',code:'Enter',"
                "keyCode:13,which:13,bubbles:true,cancelable:true}));"
                "setTimeout(function(){document.execCommand('insertText',false,"
                + JV("回车后标题DEF") + ");},400);"
                "},400);"
                "},300);},400);"),
         sleep=2.6,
         js=("(function(){var v=window.Editor.getValue();"
             "if(v.indexOf(" + JV("回车后标题DEF") + ")<0)return 'lost='+JSON.stringify(v);"
             "if(v.indexOf('#')>=0)return 'still heading='+JSON.stringify(v);"
             "return true;})()"),
         timeout=12),

    # —— 工作副本全量（T98 已切到 workdir；夹具独立于 e2e-repo）——
    dict(name="T101 工作副本:只开一篇时搜索走副本且不收编会话",
         setup=("window.__w101=0;window.__w101work='';window.__w101err='';"
                "(async function(){"
                "try{"
                "await window.App.openPath(" + JV(str(WORKDIR_SOLO / "hello.md")) + ");"
                "if(window.App.ensureEditor)await window.App.ensureEditor();"
                "window.Editor.setValue(" + JV("# 单文件\n\nKeepFileSession\n") + ");"
                "await window.App.save({silent:true});"
                "var opened=await window.pywebview.api.read_file(" + JV(str(WORKDIR_SOLO / "hello.md")) + ");"
                "window.__w101work=opened&&opened.path||'';"
                "await window.pywebview.api.add_project(" + JV(str(WORKDIR_SOLO)) + "," + JV("单文件库") + ");"
                "await window.pywebview.api.update_config({last_folder:''});"
                "var s=await window.pywebview.api.search_notes('','KeepFileSession','content');"
                "var blob=JSON.stringify(s||{});"
                "window.__w101=blob.indexOf('KeepFileSession')>=0?1:2;"
                "window.__w101err=window.__w101===1?'':blob.slice(0,160);"
                "}catch(e){window.__w101=3;window.__w101err=String(e&&e.message||e);}})();"),
         sleep=0.4,
         timeout=20,
         js=("(function(){if(window.__w101===0)return 'wait';"
             "if(window.__w101!==1)return 'search='+window.__w101+' '+window.__w101err;"
             "if(!window.App.hasSavedFile())return 'no file';"
             "return true;})()"),
         py=_workdir_t101),

    dict(name="T102 工作副本:先开单文件再开文件夹不丢编辑",
         setup=("window.__w102=0;window.__w102v='';"
                "(async function(){"
                "try{"
                "await window.App.openPath(" + JV(str(WORKDIR_SOLO)) + ");"
                "await window.App.openPath(" + JV(str(WORKDIR_SOLO / "hello.md")) + ");"
                "window.__w102v=window.Editor.getValue()||'';"
                "window.__w102=window.__w102v.indexOf('KeepFileSession')>=0?1:2;"
                "}catch(e){window.__w102=3;window.__w102v=String(e&&e.message||e);}})();"),
         sleep=0.4,
         timeout=20,
         js=("(function(){if(window.__w102===0)return 'wait';"
             "if(window.__w102!==1)return 'ed='+window.__w102v.slice(0,80);"
             "return true;})()"),
         py=_workdir_t102),

    dict(name="T103 工作副本:打开仓库后文件树是副本且源未改",
         setup=("window.__w103=0;window.__w103t='';"
                "window.App.openPath(" + JV(str(WORKDIR_DIR)) + ").then(function(){"
                "window.__w103t=document.getElementById('file-tree').textContent||'';"
                "window.__w103=1;});"),
         sleep=0.4,
         timeout=20,
         js=("(function(){if(window.__w103!==1)return 'wait';"
             "if(window.Home&&window.Home.isOpen())return 'home open';"
             "if(!document.body.classList.contains('is-workdir'))return 'no is-workdir';"
             "if(window.__w103t.indexOf('guide.md')<0)return 'tree='+window.__w103t.slice(0,80);"
             "return true;})()"),
         py=_workdir_t103),

    dict(name="T104 工作副本:保存只写副本、源文件未变",
         setup=("window.__w104=0;"
                "(async function(){"
                "try{"
                "await window.App.openPath(" + JV(str(WORKDIR_DIR / "guide.md")) + ");"
                "if(window.App.ensureEditor)await window.App.ensureEditor();"
                "window.Editor.setValue(" + JV("# 指南\n\n只改副本 WorkdirCopyToken\n") + ");"
                "await window.App.save({silent:true});"
                "window.__w104=window.App.hasSavedFile()?1:2;"
                "}catch(e){window.__w104=3;}})();"),
         sleep=0.4,
         timeout=20,
         js=("(function(){if(window.__w104===0)return 'wait';"
             "if(window.__w104!==1)return 'saved='+window.__w104;"
             "var v=window.Editor.getValue();"
             "return v.indexOf('WorkdirCopyToken')>=0?true:'ed='+v.slice(0,60);})()"),
         py=_workdir_t104),

    dict(name="T105 工作副本:保存至源后源文件含副本内容",
         setup=("window.__w105=0;"
                "(window.App.pushToSource?window.App.pushToSource():Promise.resolve())"
                ".then(function(){window.__w105=1;})"
                ".catch(function(e){window.__w105=2;window.__w105e=String(e&&e.message||e);});"),
         sleep=0.4,
         timeout=15,
         js=("(function(){if(window.__w105===0)return 'wait';"
             "if(window.__w105!==1)return 'push='+window.__w105+' '+(window.__w105e||'');"
             "return true;})()"),
         py=lambda api, ev: _assert_source_has(WORKDIR_DIR / "guide.md", "WorkdirCopyToken")),

    dict(name="T106 工作副本:改源后再合并源编辑器跟上",
         before=lambda api: (WORKDIR_DIR / "guide.md").write_text(
             "# 指南\n\n源又更新了 WorkdirMergedToken\n", encoding="utf-8"
         ),
         setup=("window.__w106=0;"
                "(window.App.mergeFromSource?window.App.mergeFromSource():Promise.resolve())"
                ".then(function(){window.__w106=1;})"
                ".catch(function(e){window.__w106=2;window.__w106e=String(e&&e.message||e);});"),
         sleep=0.4,
         timeout=15,
         js=("(function(){if(window.__w106===0)return 'wait';"
             "if(window.__w106!==1)return 'merge='+window.__w106+' '+(window.__w106e||'');"
             "var v=window.Editor.getValue();"
             "return v.indexOf('WorkdirMergedToken')>=0?true:'ed='+v.slice(0,80);})()")),

    dict(name="T107 工作副本:新建笔记只落副本且最近列表算存在",
         setup=("window.__w107=0;window.__w107n='';"
                "(async function(){"
                "try{"
                "await window.App.newNoteInFolder(null," + JV("仅副本笔记") + ");"
                "window.__w107n=(document.getElementById('doc-name').textContent||'');"
                "window.__w107=1;"
                "}catch(e){window.__w107=2;window.__w107n=String(e&&e.message||e);}})();"),
         sleep=0.4,
         timeout=20,
         js=("(function(){if(window.__w107===0)return 'wait';"
             "if(window.__w107!==1)return 'new='+window.__w107n;"
             "if(window.__w107n.indexOf(" + JV("仅副本笔记") + ")<0)return 'doc='+window.__w107n;"
             "return true;})()"),
         py=_workdir_t107),

    dict(name="T108 工作副本:内容搜索命中未回写到源的字",
         setup=("window.__w108=0;window.__w108t='';"
                "(async function(){"
                "try{"
                "await window.App.openPath(" + JV(str(WORKDIR_DIR / "guide.md")) + ");"
                "window.Editor.setValue(" + JV("# 指南\n\nSearchCopyOnlyToken\n") + ");"
                "await window.App.save({silent:true});"
                "window.Palette.openSearch();"
                "var btn=document.querySelector('#pal-modes [data-mode=content]');"
                "if(btn)btn.dispatchEvent(new MouseEvent('mousedown',{bubbles:true,cancelable:true}));"
                "var el=document.getElementById('pal-input');"
                "el.value='SearchCopyOnlyToken';"
                "el.dispatchEvent(new Event('input',{bubbles:true}));"
                "window.__w108=1;"
                "}catch(e){window.__w108=2;window.__w108t=String(e&&e.message||e);}})();"),
         sleep=1.0,
         timeout=20,
         js=("(function(){if(window.__w108===0)return 'wait';"
             "if(window.__w108!==1)return 'setup='+window.__w108t;"
             "if(!document.getElementById('palette-mask').classList.contains('open'))return 'not open';"
             "var t=document.getElementById('pal-list').textContent||'';"
             "window.__w108t=t;"
             "return t.indexOf('SearchCopyOnlyToken')>=0?true:'list='+t.slice(0,120);})()"),
         setup2="document.dispatchEvent(new KeyboardEvent('keydown',{key:'Escape',bubbles:true}))",
         sleep2=0.3,
         js2="!document.getElementById('palette-mask').classList.contains('open')",
         py=lambda api, ev: _assert_source_lacks(WORKDIR_DIR / "guide.md", "SearchCopyOnlyToken")),

    dict(name="T109 工作副本:文档态显示保存至源与合并源",
         setup="window.Home.hide()",
         sleep=0.4,
         js=("(function(){"
             "if(!document.body.classList.contains('is-workdir'))return 'no is-workdir';"
             "if(document.body.classList.contains('is-home'))return 'is-home';"
             "var p=document.getElementById('btn-push-source');"
             "var m=document.getElementById('btn-merge-source');"
             "if(!p||getComputedStyle(p).display==='none')return 'push hidden';"
             "if(!m||getComputedStyle(m).display==='none')return 'merge hidden';"
             "return true;})()")),

    dict(name="T110 工作副本:命令面板能搜到保存至源/合并源",
         setup=("window.Palette.openCommands();"
                "var el=document.getElementById('pal-input');"
                "el.value=" + JV("工作副本") + ";"
                "el.dispatchEvent(new Event('input',{bubbles:true}));"),
         sleep=0.5,
         js=("(function(){var t=document.getElementById('pal-list').textContent;"
             "if(t.indexOf(" + JV("保存当前至源文件") + ")<0)return 'list='+t.slice(0,120);"
             "if(t.indexOf(" + JV("合并当前源文件") + ")<0)return 'no merge cmd';"
             "return true;})()"),
         setup2="document.dispatchEvent(new KeyboardEvent('keydown',{key:'Escape',bubbles:true}))",
         sleep2=0.3,
         js2="!document.getElementById('palette-mask').classList.contains('open')"),

    dict(name="T111 工作副本:欢迎页有编辑方式且记住工作副本",
         setup="window.Welcome.show({style:'notion',edit_mode:'workdir'})",
         sleep=0.8,
         timeout=12,
         js=("(function(){"
             "var m=document.getElementById('welcome-mask');"
             "if(!m.classList.contains('open'))return 'not open';"
             "if(m.querySelectorAll('.mode-opt').length!==2)return 'modes';"
             "var sel=m.querySelector('.mode-opt.selected');"
             "if(!sel||sel.dataset.mode!=='workdir')return 'sel='+(sel&&sel.dataset.mode);"
             "document.getElementById('welcome-start').click();"
             "return true;})()"),
         sleep2=0.4,
         js2="!document.getElementById('welcome-mask').classList.contains('open')"),

    dict(name="T112 工作副本:日记写到副本不写源",
         setup=("window.__w112=0;window.__w112p='';"
                "(async function(){"
                "try{"
                "await window.App.openDailyNote();"
                "window.__w112p=(document.getElementById('doc-name').textContent||'');"
                "window.__w112=1;"
                "}catch(e){window.__w112=2;window.__w112p=String(e&&e.message||e);}})();"),
         sleep=0.4,
         timeout=20,
         js=("(function(){if(window.__w112===0)return 'wait';"
             "if(window.__w112!==1)return 'daily='+window.__w112p;"
             "if(!window.App.hasSavedFile())return 'no file';"
             "return true;})()"),
         py=_workdir_t112),

    dict(name="T113 工作副本:语义模式仍标注回车才搜索",
         setup=("window.Palette.openSearch();"
                "var btn=document.querySelector('#pal-modes [data-mode=semantic]');"
                "if(btn)btn.dispatchEvent(new MouseEvent('mousedown',{bubbles:true,cancelable:true}));"),
         sleep=0.5,
         js=("(function(){"
             "var btn=document.querySelector('#pal-modes [data-mode=semantic]');"
             "if(!btn)return 'no semantic';"
             "if(btn.querySelector('.pal-mode-kbd'))return 'kbd still';"
             "var h=document.getElementById('pal-foot').textContent||'';"
             "if(h.indexOf('Enter')<0&&h.indexOf(" + JV("回车") + ")<0)return 'foot='+h;"
             "return true;})()"),
         setup2="document.dispatchEvent(new KeyboardEvent('keydown',{key:'Escape',bubbles:true}))",
         sleep2=0.3,
         js2="!document.getElementById('palette-mask').classList.contains('open')"),

    dict(name="T115 工作副本:改名后全部合并不复活旧名",
         setup=("window.__w115=0;window.__w115e='';"
                "(async function(){"
                "try{"
                "await window.App.openPath(" + JV(str(WORKDIR_EDGE)) + ");"
                "var it=[].slice.call(document.querySelectorAll('#file-tree .tree-item.file'))"
                ".find(function(el){return (el.textContent||'').indexOf('keep.md')>=0;});"
                "if(!it){window.__w115=2;window.__w115e='no keep.md';return;}"
                "it.dispatchEvent(new MouseEvent('contextmenu',{bubbles:true,cancelable:true,clientX:220,clientY:220}));"
                "var rn=document.querySelector('#tree-menu .ctx-item[data-act=\"rename\"]');"
                "if(!rn){window.__w115=3;window.__w115e='no rename';return;}"
                "rn.click();"
                "document.getElementById('pm-input').value='renamed-keep';"
                "document.getElementById('pm-ok').click();"
                "window.__w115=1;"
                "}catch(e){window.__w115=4;window.__w115e=String(e&&e.message||e);}})();"),
         sleep=0.4,
         timeout=20,
         js=("(function(){if(window.__w115===0)return 'wait';"
             "if(window.__w115!==1)return 'rename='+window.__w115+' '+window.__w115e;"
             "return true;})()"),
         setup2=("window.__w115m=0;"
                 "setTimeout(function(){"
                 "window.Palette.openCommands();"
                 "var el=document.getElementById('pal-input');"
                 "el.value=" + JV("全部合并源文件") + ";"
                 "el.dispatchEvent(new Event('input',{bubbles:true}));"
                 "setTimeout(function(){"
                 "var item=document.querySelector('#pal-list .pal-item');"
                 "if(!item){window.__w115m=2;return;}"
                 "item.dispatchEvent(new MouseEvent('mousedown',{bubbles:true,cancelable:true}));"
                 "window.__w115m=1;"
                 "},250);"
                 "},800);"),
         sleep2=0.4,
         timeout2=20,
         js2=("(function(){if(window.__w115m===0)return 'wait merge';"
              "if(window.__w115m!==1)return 'merge cmd='+window.__w115m;"
              "var t=document.getElementById('file-tree').textContent||'';"
              "if(t.indexOf('keep.md')>=0&&t.indexOf('renamed-keep.md')<0)return 'tree='+t.slice(0,80);"
              "if(t.indexOf('renamed-keep.md')<0)return 'tree no renamed='+t.slice(0,80);"
              "return true;})()"),
         py=_workdir_t115),

    dict(name="T116 工作副本:保存后谱系缓存失效",
         before=_workdir_t116_before,
         setup=("window.__w116=0;"
                "(async function(){"
                "try{"
                "var listed=await window.pywebview.api.list_folder(" + JV(str(WORKDIR_EDGE)) + ");"
                "var root=String(listed.root||'').replace(/\\\\/g,'/');"
                "await window.App.openPath(root+'/renamed-keep.md');"
                "if(window.App.ensureEditor)await window.App.ensureEditor();"
                "window.Editor.setValue(" + JV("# 保留\n\n改谱系 KnowledgeCacheToken\n") + ");"
                "await window.App.save({silent:true});"
                "window.__w116=1;"
                "}catch(e){window.__w116=2;}})();"),
         sleep=0.4,
         timeout=20,
         js=("(function(){if(window.__w116===0)return 'wait';"
             "if(window.__w116!==1)return 'save='+window.__w116;"
             "return true;})()"),
         py=_workdir_t116),

    dict(name="T117 工作副本:收编后孤儿路径仍能保存至源",
         setup=("window.__w117=0;window.__w117old='';window.__w117e='';"
                "(async function(){"
                "try{"
                "await window.App.openPath(" + JV(str(WORKDIR_ORPHAN / "hello.md")) + ");"
                "if(window.App.ensureEditor)await window.App.ensureEditor();"
                "window.Editor.setValue(" + JV("# 孤儿\n\nOrphanPushE2E\n") + ");"
                "await window.App.save({silent:true});"
                "var opened=await window.pywebview.api.read_file(" + JV(str(WORKDIR_ORPHAN / "hello.md")) + ");"
                "window.__w117old=opened&&opened.path||'';"
                "await window.App.openPath(" + JV(str(WORKDIR_ORPHAN)) + ");"
                "var pushed=await window.pywebview.api.workdir_push(window.__w117old);"
                "window.__w117=(pushed&&pushed.ok)?1:2;"
                "window.__w117e=JSON.stringify(pushed||{});"
                "}catch(e){window.__w117=3;window.__w117e=String(e&&e.message||e);}})();"),
         sleep=0.4,
         timeout=25,
         js=("(function(){if(window.__w117===0)return 'wait';"
             "if(window.__w117!==1)return 'push='+window.__w117+' '+window.__w117e;"
             "return true;})()"),
         py=_workdir_t117),

    dict(name="T118 工作副本:切直改前先把未保存缓冲写入副本",
         setup=("window.__w118=0;window.__w118e='';"
                "(async function(){"
                "try{"
                "await window.App.openPath(" + JV(str(WORKDIR_DIR / "guide.md")) + ");"
                "if(window.App.ensureEditor)await window.App.ensureEditor();"
                "window.Editor.setValue(" + JV("# 指南\n\nDirtySwitchToken\n") + ");"
                "document.getElementById('btn-settings').click();"
                "window.__w118=1;"
                "}catch(e){window.__w118=2;window.__w118e=String(e&&e.message||e);}})();"),
         sleep=0.6,
         timeout=20,
         js=("(function(){if(window.__w118===0)return 'wait';"
             "if(window.__w118!==1)return 'open='+window.__w118e;"
             "if(!document.getElementById('set-edit-mode'))return 'no edit mode';"
             "document.querySelector('#set-edit-mode [data-v=source]').click();"
             "return true;})()"),
         setup2="document.getElementById('set-close').click()",
         sleep2=1.2,
         timeout2=20,
         js2=("(function(){"
              "if(document.getElementById('settings-mask').classList.contains('open'))return 'settings open';"
              "if(document.body.classList.contains('is-workdir'))return 'still workdir';"
              "var v=window.Editor.getValue()||'';"
              "if(v.indexOf('DirtySwitchToken')>=0)return 'editor still dirty token';"
              "return true;})()"),
         py=_workdir_t118),

    dict(name="T114 工作副本:切回直改后状态栏恢复且不再显示保存至源",
         setup=("window.__w114=0;"
                "document.getElementById('btn-settings').click();"),
         sleep=0.5,
         js=("(function(){if(!document.getElementById('set-edit-mode'))return 'no edit mode';"
             "document.querySelector('#set-edit-mode [data-v=source]').click();"
             "return true;})()"),
         setup2="document.getElementById('set-close').click()",
         sleep2=0.8,
         timeout2=12,
         js2=("(function(){"
              "if(document.getElementById('settings-mask').classList.contains('open'))return 'settings open';"
              "if(document.body.classList.contains('is-workdir'))return 'still workdir';"
              "var sb=document.getElementById('sb-workdir');"
              "if(sb&&!sb.hidden&&sb.textContent.indexOf(" + JV("直改") + ")<0)return 'badge='+sb.textContent;"
              "var p=document.getElementById('btn-push-source');"
              "if(p&&getComputedStyle(p).display!=='none'&&!document.body.classList.contains('is-home'))"
              "return 'push still visible';"
              "return true;})()")),
]

# —— 空格式键盘行为与基础编辑回归（convert.js handleEmptyEnter/Backspace）——
CASES += [
    dict(name="T121 空格式:空标题按一次Backspace退格式且可继续输入",
         setup=("if(window.Editor.getMode()!=='ir')window.Editor.setMode('ir');"
                "window.Home.hide();window.Editor.setValue(" + JV('## \n') + ");"
                "setTimeout(function(){"
                "window.Editor.focus();"
                "var h=document.querySelector('#editor .vditor-ir .vditor-reset > h2');"
                "if(!h)return;"
                "var r=document.createRange();r.selectNodeContents(h);r.collapse(true);"
                "var s=window.getSelection();s.removeAllRanges();s.addRange(r);"
                "h.dispatchEvent(new KeyboardEvent('keydown',{key:'Backspace',code:'Backspace',"
                "keyCode:8,which:8,bubbles:true,cancelable:true}));"
                "setTimeout(function(){document.execCommand('insertText',false,"
                + JV("退格后文字Q") + ");},500);"
                "},600);"),
         sleep=2.4,
         js=("(function(){var v=window.Editor.getValue();"
             "if(v.indexOf(" + JV("退格后文字Q") + ")<0)return 'lost='+JSON.stringify(v);"
             "if(v.indexOf('#')>=0)return 'still heading='+JSON.stringify(v);"
             "return true;})()"),
         timeout=12),

    dict(name="T122 空格式:空标题转换为正文不再失败",
         setup=("window.Editor.setValue(" + JV('## \n') + ");"
                "setTimeout(function(){"
                "window.Editor.focus();"
                "var h=document.querySelector('#editor .vditor-ir .vditor-reset > h2');"
                "if(!h)return;"
                "var r=document.createRange();r.selectNodeContents(h);r.collapse(true);"
                "var s=window.getSelection();s.removeAllRanges();s.addRange(r);"
                "window.__t122=window.Convert.apply('paragraph',window.Editor);},600);"),
         sleep=2.0,
         js=("(function(){var v=window.Editor.getValue();"
             "if(window.__t122===0)return 'apply returned 0';"
             "if(v.indexOf('##')>=0)return 'still heading='+JSON.stringify(v);"
             "return true;})()"),
         timeout=12),

    dict(name="T123 空格式:空列表项按Enter退出列表层级",
         setup=("window.Editor.setValue(" + JV('- 项目甲\n- \n') + ");"
                "setTimeout(function(){"
                "window.Editor.focus();"
                "var lis=document.querySelectorAll('#editor .vditor-ir .vditor-reset > ul > li');"
                "if(lis.length<2)return;"
                "var li=lis[lis.length-1];"
                "var r=document.createRange();r.selectNodeContents(li);r.collapse(true);"
                "var s=window.getSelection();s.removeAllRanges();s.addRange(r);"
                "li.dispatchEvent(new KeyboardEvent('keydown',{key:'Enter',code:'Enter',"
                "keyCode:13,which:13,bubbles:true,cancelable:true}));"
                "setTimeout(function(){document.execCommand('insertText',false,"
                + JV("出列表W") + ");},400);"
                "},600);"),
         sleep=2.4,
         js=("(function(){var v=window.Editor.getValue();"
             "if(v.indexOf(" + JV("出列表W") + ")<0)return 'lost='+JSON.stringify(v);"
             "if(v.indexOf('- " + JV("出列表W") + "')>=0)return 'still in list='+JSON.stringify(v);"
             "if(v.indexOf(" + JV("项目甲") + ")<0)return 'item lost='+JSON.stringify(v);"
             "return true;})()"),
         timeout=12),

    dict(name="T124 空格式:有子内容的项不误判为空",
         setup=("window.Editor.setValue(" + JV('- \n  - 子内容X\n') + ");"
                "setTimeout(function(){"
                "window.Editor.focus();"
                "var ul=document.querySelector('#editor .vditor-ir .vditor-reset > ul');"
                "if(!ul)return;"
                "var li=ul.querySelector(':scope > li');"
                "var r=document.createRange();r.selectNodeContents(li);r.collapse(true);"
                "var s=window.getSelection();s.removeAllRanges();s.addRange(r);"
                "window.__t124empty=window.Convert.isVisuallyEmpty(ul);"
                "li.dispatchEvent(new KeyboardEvent('keydown',{key:'Enter',code:'Enter',"
                "keyCode:13,which:13,bubbles:true,cancelable:true}));"
                "},600);"),
         sleep=2.0,
         js=("(function(){var v=window.Editor.getValue();"
             "if(window.__t124empty!==false)return 'ul misjudged empty';"
             "if(v.indexOf(" + JV("子内容X") + ")<0)return 'child lost='+JSON.stringify(v);"
             "return true;})()"),
         timeout=12),

    dict(name="T125 空格式:含图片的段落不是空段落",
         setup=("window.Editor.setValue(" + JV('![图](x.png)\n') + ");"
                "setTimeout(function(){"
                "window.Editor.focus();"
                "var p=document.querySelector('#editor .vditor-ir .vditor-reset > p');"
                "if(!p)return;"
                "var r=document.createRange();r.selectNodeContents(p);r.collapse(true);"
                "var s=window.getSelection();s.removeAllRanges();s.addRange(r);"
                "window.__t125empty=window.Convert.isVisuallyEmpty(p);"
                "window.__t125bs=window.Convert.handleEmptyBackspace({key:'Backspace'});"
                "},600);"),
         sleep=1.6,
         js=("(function(){var v=window.Editor.getValue();"
             "if(window.__t125empty!==false)return 'img paragraph misjudged empty';"
             "if(window.__t125bs!==false)return 'backspace path triggered';"
             "if(v.indexOf('![')<0)return 'img lost='+JSON.stringify(v);"
             "return true;})()"),
         timeout=12),

    dict(name="T126 空格式:组词期间Enter不触发结构转换",
         setup=("window.Editor.setValue(" + JV('## \n') + ");"
                "setTimeout(function(){"
                "window.Editor.focus();"
                "var h=document.querySelector('#editor .vditor-ir .vditor-reset > h2');"
                "if(!h)return;"
                "var r=document.createRange();r.selectNodeContents(h);r.collapse(true);"
                "var s=window.getSelection();s.removeAllRanges();s.addRange(r);"
                "h.dispatchEvent(new KeyboardEvent('keydown',{key:'Enter',code:'Enter',"
                "keyCode:13,which:13,bubbles:true,cancelable:true,isComposing:true}));"
                "},600);"),
         sleep=1.6,
         js=("(function(){var v=window.Editor.getValue();"
             "if(v.indexOf('##')<0)return 'converted during composition='+JSON.stringify(v);"
             "return true;})()"),
         setup2=("var h=document.querySelector('#editor .vditor-ir .vditor-reset > h2');"
                 "if(h){var r=document.createRange();r.selectNodeContents(h);r.collapse(true);"
                 "var s=window.getSelection();s.removeAllRanges();s.addRange(r);"
                 "h.dispatchEvent(new KeyboardEvent('keydown',{key:'Enter',code:'Enter',"
                 "keyCode:13,which:13,bubbles:true,cancelable:true}));}"),
         sleep2=1.6,
         js2=("(function(){var v=window.Editor.getValue();"
              "if(v.indexOf('##')>=0)return 'not exited after composition='+JSON.stringify(v);"
              "return true;})()"),
         timeout=12, timeout2=12),

    dict(name="T127 基础编辑:跨段选字删除精确且中文输入正常",
         setup=("window.Editor.setValue(" + JV('第一段ABC\n\n第二段DEF\n') + ");"
                "setTimeout(function(){"
                "window.Editor.focus();"
                "var ps=document.querySelectorAll('#editor .vditor-ir .vditor-reset > p');"
                "if(ps.length<2)return;"
                "var t=ps[0].firstChild;"
                "while(t&&t.nodeType!==3)t=t.firstChild||t.nextSibling;"
                "if(!t)return;"
                "var r=document.createRange();"
                "r.setStart(t,t.nodeValue.length-3);r.setEnd(t,t.nodeValue.length);"
                "var s=window.getSelection();s.removeAllRanges();s.addRange(r);"
                "document.execCommand('delete');"
                "setTimeout(function(){document.execCommand('insertText',false,"
                + JV("中文输入Z") + ");},300);"
                "},600);"),
         sleep=2.2,
         js=("(function(){var v=window.Editor.getValue();"
             "if(v.indexOf('ABC')>=0)return 'selection delete imprecise='+JSON.stringify(v);"
             "if(v.indexOf(" + JV("第一段") + ")<0||v.indexOf(" + JV("第二段DEF") + ")<0)"
             "return 'content damaged='+JSON.stringify(v);"
             "if(v.indexOf(" + JV("中文输入Z") + ")<0)return 'ime insert lost='+JSON.stringify(v);"
             "return true;})()"),
         timeout=12),
]

# —— 操作柄交互层（blockui.js）：柄/菜单/高亮/拖拽/键盘替代 ——
CASES += [
    dict(name="T133 操作柄:悬停只显示一个柄且不推挤正文",
         setup=("window.Home.hide();window.Editor.setValue(" + JV('段落一\n\n段落二\n') + ");"
                "setTimeout(function(){"
                "var p=document.querySelector('.vditor-ir .vditor-reset > p');"
                "var r=p.getBoundingClientRect();"
                "window.__t133w=r.width;"
                "p.dispatchEvent(new MouseEvent('mousemove',{clientX:r.left+20,clientY:r.top+5,bubbles:true}));"
                "setTimeout(function(){"
                "window.__t133w2=p.getBoundingClientRect().width;"
                "},300);},700);"),
         sleep=2.0,
         js=("(function(){"
             "var hs=[].slice.call(document.querySelectorAll('.block-handle.on'));"
             "if(hs.length!==1)return 'handles='+hs.length;"
             "if(window.__t133w!==window.__t133w2)return 'layout shifted';"
             "var r=hs[0].getBoundingClientRect();"
             "if(r.width===0)return 'not visible';"
             "return true;})()"),
         timeout=12),

    dict(name="T134 操作柄:点击开菜单并高亮完整范围",
         setup=("window.Home.hide();window.Editor.setValue(" + JV('段落一\n\n段落二\n') + ");"
                "setTimeout(function(){"
                "var p=document.querySelector('.vditor-ir .vditor-reset > p');"
                "var r=p.getBoundingClientRect();"
                "p.dispatchEvent(new MouseEvent('mousemove',{clientX:r.left+20,clientY:r.top+5,bubbles:true}));"
                "setTimeout(function(){"
                "var h=document.querySelector('.block-handle.on');"
                "if(h)h.click();"
                "},300);},700);"),
         sleep=2.0,
         js=("(function(){"
             "var m=document.getElementById('slash-menu');"
             "if(!m.classList.contains('open'))return 'menu closed';"
             "var t=m.textContent;"
             "if(t.indexOf(" + JV("上移") + ")<0||t.indexOf(" + JV("下移") + ")<0||t.indexOf(" + JV("删除") + ")<0)"
             "return 'items='+t.slice(0,60);"
             "if(!document.querySelector('.ryuu-block-hl'))return 'no highlight';"
             "var v=window.Editor.getValue();"
             "if(v.indexOf('ryuu-block-hl')>=0)return 'hl leaked to md';"
             "return true;})()"),
         timeout=12),

    dict(name="T135 操作柄:菜单下移生效且一次撤销回位",
         setup=("window.Home.hide();window.Editor.setValue(" + JV('段落一\n\n段落二\n') + ");"
                "setTimeout(function(){"
                "var p=document.querySelector('.vditor-ir .vditor-reset > p');"
                "var r=p.getBoundingClientRect();"
                "p.dispatchEvent(new MouseEvent('mousemove',{clientX:r.left+20,clientY:r.top+5,bubbles:true}));"
                "setTimeout(function(){"
                "var h=document.querySelector('.block-handle.on');"
                "if(h)h.click();"
                "setTimeout(function(){"
                "var it=[].slice.call(document.querySelectorAll('#slash-menu .slash-item'))"
                ".find(function(el){var t=el.querySelector('.si-title');return t&&t.textContent==="
                + JV("下移") + ";});"
                "if(it)it.dispatchEvent(new MouseEvent('mousedown',{bubbles:true,cancelable:true}));"
                "setTimeout(function(){"
                "var v0=window.Editor.getValue();"
                "window.__t135m=v0.indexOf('段落二')<v0.indexOf('段落一');"
                "var panel=document.querySelector('.vditor-ir .vditor-reset');"
                "panel.dispatchEvent(new KeyboardEvent('keydown',{key:'z',code:'KeyZ',"
                "ctrlKey:true,bubbles:true,cancelable:true}));"
                "setTimeout(function(){"
                "var v1=window.Editor.getValue();"
                "window.__t135u=v1.indexOf('段落一')<v1.indexOf('段落二');"
                "},400);},400);},300);},300);},700);"),
         sleep=3.6,
         js=("(function(){"
             "if(!window.__t135m)return 'not moved';"
             "if(!window.__t135u)return 'undo failed';"
             "return true;})()"),
         timeout=14),

    dict(name="T136 操作柄:嵌套位置可切换为操作整个列表",
         setup=("window.Home.hide();window.Editor.setValue(" + JV('- 父项A\n  - 子项B\n- 父项C\n') + ");"
                "setTimeout(function(){"
                "var li=document.querySelector('.vditor-ir .vditor-reset > ul > li > ul > li');"
                "var r=li.getBoundingClientRect();"
                "li.dispatchEvent(new MouseEvent('mousemove',{clientX:r.left+20,clientY:r.top+5,bubbles:true}));"
                "setTimeout(function(){"
                "var h=document.querySelector('.block-handle.on');"
                "if(h)h.click();"
                "setTimeout(function(){"
                "var it=[].slice.call(document.querySelectorAll('#slash-menu .slash-item'))"
                ".find(function(el){var t=el.querySelector('.si-title');"
                "return t&&t.textContent.indexOf(" + JV("整个列表") + ")>=0;});"
                "window.__t136has=!!it;"
                "if(it)it.dispatchEvent(new MouseEvent('mousedown',{bubbles:true,cancelable:true}));"
                "},300);},300);},700);"),
         sleep=2.4,
         js=("(function(){"
             "if(!window.__t136has)return 'no scope item in menu';"
             "var hl=document.querySelector('.ryuu-block-hl');"
             "if(!hl||hl.tagName!=='UL')return 'hl not list';"
             "if(!hl.parentElement||!hl.parentElement.classList.contains('vditor-reset'))return 'hl not TOP list';"
             "var m=document.getElementById('slash-menu');"
             "if(!m.classList.contains('open'))return 'menu not reopened';"
             "if(m.textContent.indexOf(" + JV("删除整个列表") + ")<0)return 'menu='+m.textContent.slice(0,80);"
             "return true;})()"),
         timeout=12),

    dict(name="T137 操作柄:拖拽落点显示位置与归属且可撤销",
         setup=("window.SlashMenu.close();window.Home.hide();"
                "window.Editor.setValue(" + JV('段落一\n\n段落二\n\n段落三\n') + ");"
                "setTimeout(function(){"
                "var p=document.querySelector('.vditor-ir .vditor-reset > p');"
                "var r=p.getBoundingClientRect();"
                "p.dispatchEvent(new MouseEvent('mousemove',{clientX:r.left+20,clientY:r.top+5,bubbles:true}));"
                "setTimeout(function(){"
                "var h=document.querySelector('.block-handle.on');"
                "if(!h)return;"
                "var hr=h.getBoundingClientRect();"
                "var ps=document.querySelectorAll('.vditor-ir .vditor-reset > p');"
                "var t=ps[2].getBoundingClientRect();"
                "h.dispatchEvent(new MouseEvent('mousedown',{clientX:hr.left+5,clientY:hr.top+5,bubbles:true,cancelable:true}));"
                "document.dispatchEvent(new MouseEvent('mousemove',{clientX:t.left+30,clientY:t.top+t.height*0.8,bubbles:true}));"
                "setTimeout(function(){"
                "var line=document.querySelector('.block-drop-line.on');"
                "var badge=document.querySelector('.block-drop-badge.on');"
                "window.__t137line=!!line;"
                "window.__t137badge=badge?badge.textContent:'';"
                "document.dispatchEvent(new MouseEvent('mouseup',{clientX:t.left+30,clientY:t.top+t.height*0.8,bubbles:true}));"
                "setTimeout(function(){"
                "var v=window.Editor.getValue();"
                "window.__t137moved=v.indexOf('段落一')>v.indexOf('段落三');"
                "},400);},300);},300);},700);"),
         sleep=3.2,
         js=("(function(){"
             "if(!window.__t137line)return 'no drop line';"
             "if(window.__t137badge.indexOf(" + JV("放在") + ")<0||window.__t137badge.indexOf(" + JV("后") + ")<0)"
             "return 'badge='+window.__t137badge;"
             "if(!window.__t137moved)return 'not moved';"
             "return true;})()"),
         timeout=12),

    dict(name="T138 操作柄:正文拖动不进入结构移动",
         setup=("window.Home.hide();window.Editor.setValue(" + JV('段落一\n\n段落二\n') + ");"
                "setTimeout(function(){"
                "var p=document.querySelector('.vditor-ir .vditor-reset > p');"
                "var r=p.getBoundingClientRect();"
                "p.dispatchEvent(new MouseEvent('mousedown',{clientX:r.left+10,clientY:r.top+5,bubbles:true}));"
                "var ps=document.querySelectorAll('.vditor-ir .vditor-reset > p');"
                "var t=ps[1].getBoundingClientRect();"
                "document.dispatchEvent(new MouseEvent('mousemove',{clientX:t.left+30,clientY:t.top+5,bubbles:true}));"
                "document.dispatchEvent(new MouseEvent('mouseup',{clientX:t.left+30,clientY:t.top+5,bubbles:true}));"
                "},700);"),
         sleep=1.6,
         js=("(function(){"
             "if(document.querySelector('.block-drop-line.on'))return 'drop line shown';"
             "if(window.BlockUI._debug().drag)return 'drag started';"
             "var v=window.Editor.getValue();"
             "if(v.indexOf('段落一')>v.indexOf('段落二'))return 'order changed='+JSON.stringify(v);"
             "return true;})()"),
         timeout=12),

    dict(name="T139 操作柄:键盘可达(柄焦点Enter开菜单,Ctrl+Alt+↓移动)",
         setup=("window.Home.hide();window.Editor.setValue(" + JV('段落一\n\n段落二\n') + ");"
                "setTimeout(function(){"
                "var p=document.querySelector('.vditor-ir .vditor-reset > p');"
                "var r=p.getBoundingClientRect();"
                "p.dispatchEvent(new MouseEvent('mousemove',{clientX:r.left+20,clientY:r.top+5,bubbles:true}));"
                "setTimeout(function(){"
                "var h=document.querySelector('.block-handle.on');"
                "if(!h)return;"
                "h.focus();"
                "h.dispatchEvent(new KeyboardEvent('keydown',{key:'Enter',code:'Enter',"
                "keyCode:13,which:13,bubbles:true,cancelable:true}));"
                "window.__t139menu=document.getElementById('slash-menu').classList.contains('open');"
                "window.SlashMenu.close();"
                "var sel=window.getSelection();"
                "var rr=document.createRange();rr.selectNodeContents(p);rr.collapse(true);"
                "sel.removeAllRanges();sel.addRange(rr);"
                "p.dispatchEvent(new KeyboardEvent('keydown',{key:'ArrowDown',code:'ArrowDown',"
                "ctrlKey:true,altKey:true,bubbles:true,cancelable:true}));"
                "setTimeout(function(){"
                "var v=window.Editor.getValue();"
                "window.__t139moved=v.indexOf('段落二')<v.indexOf('段落一');"
                "},400);"
                "},300);},700);"),
         sleep=2.6,
         js=("(function(){"
             "if(!window.__t139menu)return 'menu not opened by Enter;handle='+!!document.querySelector('.block-handle.on')"
             "+',home='+(window.Home&&window.Home.isOpen?window.Home.isOpen():'?')"
             "+',cur='+JSON.stringify(window.BlockUI._debug().cur?1:0);"
             "if(!window.__t139moved)return 'not moved by Ctrl+Alt+Down';"
             "return true;})()"),
         timeout=12),

    dict(name="T140 操作柄:拖拽中Esc取消移动",
         setup=("window.Home.hide();window.Editor.setValue(" + JV('段落一\n\n段落二\n\n段落三\n') + ");"
                "setTimeout(function(){"
                "var p=document.querySelector('.vditor-ir .vditor-reset > p');"
                "var r=p.getBoundingClientRect();"
                "p.dispatchEvent(new MouseEvent('mousemove',{clientX:r.left+20,clientY:r.top+5,bubbles:true}));"
                "setTimeout(function(){"
                "var h=document.querySelector('.block-handle.on');"
                "if(!h)return;"
                "var hr=h.getBoundingClientRect();"
                "var ps=document.querySelectorAll('.vditor-ir .vditor-reset > p');"
                "var t=ps[2].getBoundingClientRect();"
                "h.dispatchEvent(new MouseEvent('mousedown',{clientX:hr.left+5,clientY:hr.top+5,bubbles:true,cancelable:true}));"
                "document.dispatchEvent(new MouseEvent('mousemove',{clientX:t.left+30,clientY:t.top+t.height*0.8,bubbles:true}));"
                "setTimeout(function(){"
                "document.dispatchEvent(new KeyboardEvent('keydown',{key:'Escape',code:'Escape',"
                "keyCode:27,which:27,bubbles:true,cancelable:true}));"
                "document.dispatchEvent(new MouseEvent('mouseup',{clientX:t.left+30,clientY:t.top+t.height*0.8,bubbles:true}));"
                "setTimeout(function(){"
                "var v=window.Editor.getValue();"
                "window.__t140ok=v.indexOf('段落一')<v.indexOf('段落三')"
                "&&!document.querySelector('.block-drop-line.on');"
                "},300);},300);},300);},700);"),
         sleep=2.8,
         js=("(function(){return window.__t140ok?true:'not cancelled';})()"),
         timeout=12),

    dict(name="T141 操作柄:图片段落有柄且菜单可删除",
         setup=("window.Home.hide();window.Editor.setValue(" + JV('![图](x.png)\n\n段落二\n') + ");"
                "setTimeout(function(){"
                "var p=document.querySelector('.vditor-ir .vditor-reset > p');"
                "var r=p.getBoundingClientRect();"
                "p.dispatchEvent(new MouseEvent('mousemove',{clientX:r.left+20,clientY:r.top+5,bubbles:true}));"
                "setTimeout(function(){"
                "var h=document.querySelector('.block-handle.on');"
                "window.__t141handle=!!h;"
                "if(h)h.click();"
                "},300);},700);"),
         sleep=2.0,
         js=("(function(){"
             "if(!window.__t141handle)return 'no handle on image paragraph';"
             "var m=document.getElementById('slash-menu');"
             "if(!m.classList.contains('open'))return 'menu closed';"
             "if(m.textContent.indexOf(" + JV("删除") + ")<0)return 'no delete';"
             "return true;})()"),
         timeout=12),
]

# —— 结构操作事务（Blocks.moveBlock/deleteBlock）：嵌套转换、独立撤销边界 ——
CASES += [
    dict(name="T128 段落转换:整列表→标题按深度优先输出",
         setup=("window.Editor.setValue(" + JV('- parentA\n  - childB\n- parentC\n') + ");"
                "setTimeout(function(){"
                "var ul=document.querySelector('.vditor-ir .vditor-reset > ul');"
                "var r=document.createRange();r.selectNodeContents(ul);r.collapse(true);"
                "var s=window.getSelection();s.removeAllRanges();s.addRange(r);"
                "window.Convert.apply('h2', window.Editor);},600);"),
         sleep=2.0,
         js=("(function(){var v=window.Editor.getValue();"
             "var ls=v.split('\\n').filter(function(l){return l.trim()!=='';});"
             "if(ls.length!==3)return 'lines:'+JSON.stringify(v);"
             "if(ls[0]!=='## parentA'||ls[1]!=='## childB'||ls[2]!=='## parentC')"
             "return 'bad:'+JSON.stringify(v);"
             "return true;})()"),
         timeout=12),

    dict(name="T129 段落转换:光标在嵌套项内只转该项",
         setup=("window.Editor.setValue(" + JV('- 父项A\n  - 子项B\n- 父项C\n') + ");"
                "setTimeout(function(){"
                "var li=document.querySelector('.vditor-ir .vditor-reset > ul > li > ul > li');"
                "if(!li)return;"
                "var r=document.createRange();r.selectNodeContents(li);r.collapse(true);"
                "var s=window.getSelection();s.removeAllRanges();s.addRange(r);"
                "window.Convert.apply('h2', window.Editor);},600);"),
         sleep=2.0,
         js=("(function(){var v=window.Editor.getValue();"
             "if(v.indexOf('## 子项B')<0)return 'not converted='+JSON.stringify(v);"
             "if(v.indexOf('- 父项A')<0||v.indexOf('- 父项C')<0)return 'siblings damaged='+JSON.stringify(v);"
             "if(v.indexOf('- 子项B')>=0)return 'still item='+JSON.stringify(v);"
             "return true;})()"),
         timeout=12),

    dict(name="T130 结构事务:移动与输入各有独立撤销边界",
         setup=("window.Editor.setValue(" + JV('段一\n\n段二\n') + ");"
                "setTimeout(function(){"
                "window.Editor.focus();"
                "var p=document.querySelector('.vditor-ir .vditor-reset > p');"
                "var r=document.createRange();r.selectNodeContents(p);r.collapse(false);"
                "var s=window.getSelection();s.removeAllRanges();s.addRange(r);"
                "document.execCommand('insertText',false," + JV('输入X') + ");"
                "setTimeout(function(){"
                "var loc=window.Blocks.locateLine(2);"
                "window.__t130m=window.Blocks.moveBlock(loc,-1);"
                "setTimeout(function(){"
                "var v0=window.Editor.getValue();"
                "window.__t130after=v0.indexOf('段二')<v0.indexOf('段一');"
                "var panel=document.querySelector('#editor .vditor-ir .vditor-reset');"
                "panel.dispatchEvent(new KeyboardEvent('keydown',{key:'z',code:'KeyZ',"
                "ctrlKey:true,bubbles:true,cancelable:true}));"
                "setTimeout(function(){"
                "var v1=window.Editor.getValue();"
                "window.__t130u1=v1.indexOf('段二')>v1.indexOf('段一')&&v1.indexOf('输入X')>=0;"
                "panel.dispatchEvent(new KeyboardEvent('keydown',{key:'z',code:'KeyZ',"
                "ctrlKey:true,bubbles:true,cancelable:true}));"
                "setTimeout(function(){"
                "var v2=window.Editor.getValue();"
                "window.__t130u2=v2.indexOf('输入X')<0&&v2.indexOf('段一')>=0&&v2.indexOf('段二')>=0;"
                "},400);},400);},300);},150);},600);"),
         sleep=3.4,
         js=("(function(){"
             "if(!window.__t130m)return 'move failed';"
             "if(!window.__t130after)return 'order not swapped';"
             "if(!window.__t130u1)return 'undo1 not move-only';"
             "if(!window.__t130u2)return 'undo2 not typing';"
             "return true;})()"),
         timeout=14),

    dict(name="T131 结构事务:800ms内两次移动各自可撤销",
         setup=("window.Editor.setValue(" + JV('段一\n\n段二\n\n段三\n') + ");"
                "setTimeout(function(){"
                "window.Editor.focus();"
                "var m1=window.Blocks.moveBlock(window.Blocks.locateLine(2),1);"
                "setTimeout(function(){"
                "var m2=window.Blocks.moveBlock(window.Blocks.locateLine(0),1);"
                "window.__t131m=m1&&m2;"
                "var panel=document.querySelector('#editor .vditor-ir .vditor-reset');"
                "panel.dispatchEvent(new KeyboardEvent('keydown',{key:'z',code:'KeyZ',"
                "ctrlKey:true,bubbles:true,cancelable:true}));"
                "setTimeout(function(){"
                "var v1=window.Editor.getValue();"
                "window.__t131u1=v1.indexOf('段一')<v1.indexOf('段三')&&v1.indexOf('段三')<v1.indexOf('段二');"
                "panel.dispatchEvent(new KeyboardEvent('keydown',{key:'z',code:'KeyZ',"
                "ctrlKey:true,bubbles:true,cancelable:true}));"
                "setTimeout(function(){"
                "var v2=window.Editor.getValue();"
                "window.__t131u2=v2.indexOf('段一')<v2.indexOf('段二')&&v2.indexOf('段二')<v2.indexOf('段三');"
                "},400);},400);},250);},600);"),
         sleep=3.4,
         js=("(function(){"
             "if(!window.__t131m)return 'moves failed';"
             "if(!window.__t131u1)return 'undo1 wrong order';"
             "if(!window.__t131u2)return 'undo2 wrong order';"
             "return true;})()"),
         timeout=14),

    dict(name="T132 结构事务:删除段落置脏标记且滚动保持",
         setup=("window.Editor.setValue(" + JV('# 大纲甲\n\n段一\n\n段二\n\n段三\n') + ");"
                "window.App.save({silent:true});"
                "setTimeout(function(){"
                "window.Editor.setScrollRatio(0);"
                "window.__t132=window.Blocks.deleteBlock(window.Blocks.locateLine(4));"
                "},800);"),
         sleep=2.2,
         js=("(function(){var v=window.Editor.getValue();"
             "if(!window.__t132)return 'delete failed';"
             "if(v.indexOf('段二')>=0)return 'not deleted='+JSON.stringify(v);"
             "if(v.indexOf('段一')<0||v.indexOf('段三')<0)return 'others damaged='+JSON.stringify(v);"
             "if(!document.querySelector('#doc-name .dirty'))return 'no dirty mark';"
             "var ol=document.querySelectorAll('#outline-list .outline-item');"
             "if(ol.length!==1)return 'outline='+ol.length;"
             "return true;})()"),
         timeout=12),
]

# —— 章节操作与大纲拖拽 ——
CASES += [
    dict(name="T148 章节:整节范围不被容器内标题截断",
         setup=("window.Home.hide();"
                "window.Editor.setValue(" + JV('# A\n\n```\n# 注释\n```\n\n> ## 引用内\n\n# B\n\n尾\n') + ");"
                "setTimeout(function(){"
                "var lines=window.Editor.getValue().split('\\n');"
                "window.__t148b=lines.findIndex(function(l){return /^# B/.test(l);});"
                "window.__t148s=window.Blocks.sectionAt(0);"
                "},700);"),
         sleep=2.0,
         js=("(function(){"
             "var s=window.__t148s;"
             "if(!s)return 'no section';"
             "if(s.start!==0||s.end!==window.__t148b)return 'range='+JSON.stringify(s)+' want end='+window.__t148b;"
             "return true;})()"),
         timeout=12),

    dict(name="T149 章节:标题柄选择整节后删除只清该节",
         setup=("window.Home.hide();window.SlashMenu.close();"
                "window.Editor.setValue(" + JV('# 甲\n\n正文甲\n\n## 甲一\n\n子文\n\n# 乙\n\n正文乙\n') + ");"
                "setTimeout(function(){"
                "var h=document.querySelector('.vditor-ir .vditor-reset > h1');"
                "var r=h.getBoundingClientRect();"
                "h.dispatchEvent(new MouseEvent('mousemove',{clientX:r.left+20,clientY:r.top+5,bubbles:true}));"
                "setTimeout(function(){"
                "var hd=document.querySelector('.block-handle.on');"
                "if(!hd)return;"
                "hd.click();"
                "setTimeout(function(){"
                "var it=[].slice.call(document.querySelectorAll('#slash-menu .slash-item'))"
                ".find(function(el){var t=el.querySelector('.si-title');return t&&t.textContent==="
                + JV("选择整节") + ";});"
                "window.__t149has=!!it;"
                "if(it)it.dispatchEvent(new MouseEvent('mousedown',{bubbles:true,cancelable:true}));"
                "setTimeout(function(){"
                "window.__t149hl=document.querySelectorAll('.ryuu-block-hl').length;"
                "var del=[].slice.call(document.querySelectorAll('#slash-menu .slash-item'))"
                ".find(function(el){var t=el.querySelector('.si-title');return t&&t.textContent==="
                + JV("删除整节") + ";});"
                "window.__t149hasdel=!!del;"
                "if(del)del.dispatchEvent(new MouseEvent('mousedown',{bubbles:true,cancelable:true}));"
                "},400);},300);},300);},700);"),
         sleep=3.2,
         js=("(function(){"
             "if(!window.__t149has)return 'no 选择整节';"
             "if(window.__t149hl<2)return 'hl='+window.__t149hl;"
             "if(!window.__t149hasdel)return 'no 删除整节';"
             "var v=window.Editor.getValue();"
             "if(v.indexOf('正文甲')>=0||v.indexOf('甲一')>=0||v.indexOf('子文')>=0)"
             "return 'section remains='+JSON.stringify(v);"
             "if(v.indexOf('# 乙')<0||v.indexOf('正文乙')<0)return 'B damaged='+JSON.stringify(v);"
             "return true;})()"),
         timeout=12),

    dict(name="T150 章节:整节降级保持相对等级且越界阻止",
         setup=("window.Home.hide();"
                "window.Editor.setValue(" + JV('## 甲\n\n### 甲一\n\n正文\n') + ");"
                "setTimeout(function(){"
                "var r1=window.Blocks.shiftSectionLevel(window.Blocks.sectionAt(0),1);"
                "window.__t150a=r1.ok;"
                "setTimeout(function(){"
                "window.Editor.setValue(" + JV('##### 甲\n\n###### 甲一\n') + ");"
                "setTimeout(function(){"
                "var r2=window.Blocks.shiftSectionLevel(window.Blocks.sectionAt(0),1);"
                "window.__t150b=r2;"
                "},600);},800);},700);"),
         sleep=2.8,
         js=("(function(){"
             "if(!window.__t150a)return 'shift failed';"
             "var v0=window.Editor.getValue();"
             "var r2=window.__t150b||{};"
             "if(r2.ok!==false)return 'overflow not blocked';"
             "if(!r2.reason||r2.reason.indexOf('H1')<0)return 'no reason='+r2.reason;"
             "if(v0.indexOf('##### 甲')<0||v0.indexOf('###### 甲一')<0)return 'doc changed='+JSON.stringify(v0);"
             "return true;})()"),
         timeout=12),

    dict(name="T151 章节:大纲拖动整节且大纲一致",
         setup=("window.Home.hide();"
                "window.Editor.setValue(" + JV('# 甲\n\n正文甲\n\n# 乙\n\n正文乙\n') + ");"
                "setTimeout(function(){"
                "document.querySelector('.side-tab[data-panel=outline]').click();"
                "setTimeout(function(){"
                "var items=document.querySelectorAll('#outline-list .outline-item');"
                "if(items.length<2)return;"
                "var r0=items[0].getBoundingClientRect();"
                "var r1=items[1].getBoundingClientRect();"
                "items[0].dispatchEvent(new MouseEvent('mousedown',{clientX:r0.left+20,clientY:r0.top+r0.height/2,bubbles:true,cancelable:true}));"
                "document.dispatchEvent(new MouseEvent('mousemove',{clientX:r1.left+20,clientY:r1.top+r1.height*0.8,bubbles:true}));"
                "setTimeout(function(){"
                "var badge=document.querySelector('.block-drop-badge.on');"
                "window.__t151badge=badge?badge.textContent:'';"
                "document.dispatchEvent(new MouseEvent('mouseup',{clientX:r1.left+20,clientY:r1.top+r1.height*0.8,bubbles:true}));"
                "},300);},400);},700);"),
         sleep=2.6,
         js=("(function(){"
             "if(window.__t151badge.indexOf(" + JV("放在") + ")<0)return 'badge='+window.__t151badge;"
             "var v=window.Editor.getValue();"
             "var ia=v.indexOf('# 甲'),ib=v.indexOf('# 乙');"
             "if(!(ib<ia))return 'not moved='+JSON.stringify(v);"
             "if(v.indexOf('正文甲')<0||v.indexOf('正文乙')<0)return 'content lost='+JSON.stringify(v);"
             "if(v.indexOf('正文乙')>v.indexOf('# 甲'))return 'body not with section='+JSON.stringify(v);"
             "var items=document.querySelectorAll('#outline-list .outline-item');"
             "if(items.length!==2||items[0].textContent!=='乙')return 'outline stale';"
             "return true;})()"),
         timeout=12),

    dict(name="T152 章节:大纲拖为子节且越H6阻止",
         setup=("window.Home.hide();"
                "window.Editor.setValue(" + JV('# 甲\n\n正文\n\n# 乙\n\n## 乙一\n') + ");"
                "setTimeout(function(){"
                "document.querySelector('.side-tab[data-panel=outline]').click();"
                "setTimeout(function(){"
                "var items=document.querySelectorAll('#outline-list .outline-item');"
                "if(items.length<3)return;"
                "var r0=items[1].getBoundingClientRect();"
                "var r1=items[0].getBoundingClientRect();"
                "items[1].dispatchEvent(new MouseEvent('mousedown',{clientX:r0.left+20,clientY:r0.top+r0.height/2,bubbles:true,cancelable:true}));"
                "document.dispatchEvent(new MouseEvent('mousemove',{clientX:r1.left+20,clientY:r1.top+r1.height*0.5,bubbles:true}));"
                "setTimeout(function(){"
                "var badge=document.querySelector('.block-drop-badge.on');"
                "window.__t152badge=badge?badge.textContent:'';"
                "document.dispatchEvent(new MouseEvent('mouseup',{clientX:r1.left+20,clientY:r1.top+r1.height*0.5,bubbles:true}));"
                "},300);},400);},700);"),
         sleep=2.6,
         js=("(function(){"
             "if(window.__t152badge.indexOf(" + JV("子节") + ")<0)return 'badge='+window.__t152badge;"
             "var v=window.Editor.getValue();"
             "if(v.indexOf('## 乙')<0||v.indexOf('### 乙一')<0)return 'not subsection='+JSON.stringify(v);"
             "if(v.indexOf('## 乙')<v.indexOf('正文'))return 'moved out of section='+JSON.stringify(v);"
             "return true;})()"),
         setup2=("window.Editor.setValue(" + JV('###### 顶\n\n# 丙\n\n## 丙一\n') + ");"
                 "setTimeout(function(){"
                 "var items=document.querySelectorAll('#outline-list .outline-item');"
                 "var r0=items[1].getBoundingClientRect();"
                 "var r1=items[0].getBoundingClientRect();"
                 "items[1].dispatchEvent(new MouseEvent('mousedown',{clientX:r0.left+20,clientY:r0.top+r0.height/2,bubbles:true,cancelable:true}));"
                 "document.dispatchEvent(new MouseEvent('mousemove',{clientX:r1.left+20,clientY:r1.top+r1.height*0.5,bubbles:true}));"
                 "setTimeout(function(){"
                 "document.dispatchEvent(new MouseEvent('mouseup',{clientX:r1.left+20,clientY:r1.top+r1.height*0.5,bubbles:true}));"
                 "},300);},700);"),
         sleep2=2.0,
         js2=("(function(){var v=window.Editor.getValue();"
              "if(v.indexOf('# 丙')<0||v.indexOf('## 丙一')<0)return 'overflow applied='+JSON.stringify(v);"
              "return true;})()"),
         timeout=12, timeout2=12),
]

# —— sv 补全与完整环境验证 ——
CASES += [
    dict(name="T153 sv:光标跟踪柄与菜单移动",
         setup=("window.Home.hide();window.SlashMenu.close();"
                "window.Editor.setMode('sv');"
                "setTimeout(function(){"
                "window.Editor.setValue(" + JV('段落一\n\n段落二\n') + ");"
                "setTimeout(function(){"
                "var p=document.querySelector('#editor .vditor-sv');"
                "var w=document.createTreeWalker(p,NodeFilter.SHOW_TEXT);"
                "var n=w.nextNode();"
                "if(n){var r=document.createRange();r.setStart(n,1);r.collapse(true);"
                "var s=window.getSelection();s.removeAllRanges();s.addRange(r);"
                "document.dispatchEvent(new Event('selectionchange'));"
                "p.dispatchEvent(new MouseEvent('mousedown',{bubbles:true}));}"
                "setTimeout(function(){"
                "var h=document.querySelector('.block-handle.on');"
                "window.__t153handle=!!h;"
                "if(h)h.click();"
                "setTimeout(function(){"
                "var it=[].slice.call(document.querySelectorAll('#slash-menu .slash-item'))"
                ".find(function(el){var t=el.querySelector('.si-title');return t&&t.textContent==="
                + JV("下移") + ";});"
                "if(it)it.dispatchEvent(new MouseEvent('mousedown',{bubbles:true,cancelable:true}));"
                "},400);},400);},800);},1200);"),
         sleep=4.0,
         js=("(function(){"
             "if(!window.__t153handle)return 'no sv handle';"
             "var v=window.Editor.getValue();"
             "if(v.indexOf('段落二')>v.indexOf('段落一'))return 'not moved='+JSON.stringify(v);"
             "return true;})()"),
         timeout=16),

    dict(name="T154 大文档:>512KB自动sv下结构移动正确",
         setup=("window.Sidebar.renderTree([{type:'file',name:'big.md',path:'" + BIG_JS + "'}],'tmp');"
                "document.querySelectorAll('#file-tree .tree-item.file')[0].click();"),
         sleep=1.0,
         js=("(function(){if(!window.Editor.isReady())return 'not ready';"
             "if(window.Editor.getMode()!=='sv')return 'mode='+window.Editor.getMode();"
             "if(window.Editor.getValue().length<400000)return 'len='+window.Editor.getValue().length;"
             "return true;})()"),
         timeout=25,
         setup2=("var t0=performance.now();"
                 "window.__t154=window.Blocks.moveBlock(window.Blocks.locateLine(4),1);"
                 "window.__t154ms=Math.round(performance.now()-t0);"),
         sleep2=2.0,
         js2=("(function(){"
              "if(!window.__t154)return 'move failed';"
              "var v=window.Editor.getValue();"
              "var ia=v.indexOf('这是第 0 段'),ib=v.indexOf('- 列表项二');"
              "if(!(ib>=0&&ia>ib))return 'order='+ia+'/'+ib;"
              "return true;})()"),
         timeout2=25),

    dict(name="T155 模式切换:ir→sv→ir后柄恢复且内容不丢",
         setup=("window.Home.hide();window.SlashMenu.close();"
                "window.Editor.setMode('ir');"
                "setTimeout(function(){"
                "window.Editor.setValue(" + JV('段落一\n\n段落二\n') + ");"
                "setTimeout(function(){"
                "window.Editor.setMode('sv');"
                "setTimeout(function(){"
                "window.Editor.setMode('ir');"
                "setTimeout(function(){"
                "var p=document.querySelector('.vditor-ir .vditor-reset > p');"
                "if(!p)return;"
                "var r=p.getBoundingClientRect();"
                "p.dispatchEvent(new MouseEvent('mousemove',{clientX:r.left+20,clientY:r.top+5,bubbles:true}));"
                "setTimeout(function(){"
                "var h=document.querySelector('.block-handle.on');"
                "window.__t155handle=!!h;"
                "if(h)h.click();"
                "setTimeout(function(){"
                "var it=[].slice.call(document.querySelectorAll('#slash-menu .slash-item'))"
                ".find(function(el){var t=el.querySelector('.si-title');return t&&t.textContent==="
                + JV("下移") + ";});"
                "if(it)it.dispatchEvent(new MouseEvent('mousedown',{bubbles:true,cancelable:true}));"
                "},400);},400);},1400);},1200);},800);},700);"),
         sleep=6.0,
         js=("(function(){"
             "if(!window.__t155handle)return 'no handle after mode switch';"
             "var v=window.Editor.getValue();"
             "if(v.indexOf('段落一')<0||v.indexOf('段落二')<0)return 'content lost='+JSON.stringify(v);"
             "if(v.indexOf('段落二')>v.indexOf('段落一'))return 'not moved='+JSON.stringify(v);"
             "return true;})()"),
         timeout=20),

    dict(name="T156 扩展语法:[toc]/mermaid/$$/脚注对齐与移动",
         setup=("window.Home.hide();"
                "window.Editor.setMode('ir');"
                "setTimeout(function(){"
                "window.Editor.setValue(" + JV('[toc]\n\n# A\n\n```mermaid\ngraph TD;\n  X-->Y;\n```\n\n$$\nx^2\n$$\n\n脚注[^1]\n\n[^1]: 注\n') + ");"
                "setTimeout(function(){"
                "window.__t156=window.Blocks.moveBlock(window.Blocks.locateLine(4),1);"
                "},700);},700);"),
         sleep=2.6,
         js=("(function(){var v=window.Editor.getValue();"
             "if(!window.__t156)return 'move failed(alignment aborted)';"
             "if(v.indexOf('```mermaid')<0||v.indexOf('X-->Y;')<0)return 'mermaid damaged='+JSON.stringify(v);"
             "if(v.indexOf('$$')<0||v.indexOf('x^2')<0)return 'math damaged='+JSON.stringify(v);"
             "if(v.indexOf('x^2')>v.indexOf('mermaid'))return 'not moved='+JSON.stringify(v);"
             "if(v.indexOf('[^1]: 注')<0)return 'footnote lost='+JSON.stringify(v);"
             "return true;})()"),
         timeout=12),

    dict(name="T157 模式切换后快速连续操作各自可撤销",
         setup=("window.Home.hide();"
                "window.Editor.setMode('ir');"
                "setTimeout(function(){"
                "window.Editor.setValue(" + JV('段一\n\n段二\n\n段三\n') + ");"
                "setTimeout(function(){"
                "window.Editor.setMode('sv');"
                "setTimeout(function(){"
                "window.Editor.setMode('ir');"
                "setTimeout(function(){"
                "var m1=window.Blocks.moveBlock(window.Blocks.locateLine(2),1);"
                "setTimeout(function(){"
                "var m2=window.Blocks.moveBlock(window.Blocks.locateLine(0),1);"
                "window.__t157m=m1&&m2;"
                "var panel=document.querySelector('.vditor-ir .vditor-reset');"
                "panel.dispatchEvent(new KeyboardEvent('keydown',{key:'z',code:'KeyZ',"
                "ctrlKey:true,bubbles:true,cancelable:true}));"
                "setTimeout(function(){"
                "var v1=window.Editor.getValue();"
                "window.__t157u1=v1.indexOf('段一')<v1.indexOf('段三')&&v1.indexOf('段三')<v1.indexOf('段二');"
                "panel.dispatchEvent(new KeyboardEvent('keydown',{key:'z',code:'KeyZ',"
                "ctrlKey:true,bubbles:true,cancelable:true}));"
                "setTimeout(function(){"
                "var v2=window.Editor.getValue();"
                "window.__t157u2=v2.indexOf('段一')<v2.indexOf('段二')&&v2.indexOf('段二')<v2.indexOf('段三');"
                "},400);},400);},250);},1400);},1200);},800);},700);"),
         sleep=6.2,
         js=("(function(){"
             "if(!window.__t157m)return 'moves failed';"
             "if(!window.__t157u1)return 'undo1 wrong';"
             "if(!window.__t157u2)return 'undo2 wrong';"
             "return true;})()"),
         timeout=20),

    dict(name="T158 终态回归:中文输入与跨段选字精确删除",
         setup=("window.Home.hide();"
                "window.Editor.setMode('ir');"
                "setTimeout(function(){"
                "window.Editor.setValue(" + JV('第一段ABC\n\n第二段DEF\n') + ");"
                "setTimeout(function(){"
                "window.Editor.focus();"
                "var ps=document.querySelectorAll('#editor .vditor-ir .vditor-reset > p');"
                "if(ps.length<2)return;"
                "var t=ps[0].firstChild;"
                "while(t&&t.nodeType!==3)t=t.firstChild||t.nextSibling;"
                "if(!t)return;"
                "var r=document.createRange();"
                "r.setStart(t,t.nodeValue.length-3);r.setEnd(t,t.nodeValue.length);"
                "var s=window.getSelection();s.removeAllRanges();s.addRange(r);"
                "document.execCommand('delete');"
                "setTimeout(function(){document.execCommand('insertText',false,"
                + JV("终态中文Y") + ");},300);"
                "},700);},700);"),
         sleep=2.6,
         js=("(function(){var v=window.Editor.getValue();"
             "if(v.indexOf('ABC')>=0)return 'selection delete imprecise='+JSON.stringify(v);"
             "if(v.indexOf(" + JV("第一段") + ")<0||v.indexOf(" + JV("第二段DEF") + ")<0)"
             "return 'content damaged='+JSON.stringify(v);"
             "if(v.indexOf(" + JV("终态中文Y") + ")<0)return 'ime insert lost='+JSON.stringify(v);"
             "return true;})()"),
         timeout=12),
]

# —— 嵌套结构操作：携子树移动、成为子项、移入/移出引用 ——
CASES += [
    dict(name="T142 嵌套:列表项携续段与子列表整体移动",
         setup=("window.Home.hide();"
                "window.Editor.setValue(" + JV('- 项A\n  续段A\n  - 子1\n- 项B\n') + ");"
                "setTimeout(function(){"
                "var li=document.querySelector('.vditor-ir .vditor-reset > ul > li');"
                "window.__t142=window.Blocks.moveItem(window.Blocks.locate(li),1);"
                "},700);"),
         sleep=2.0,
         js=("(function(){var v=window.Editor.getValue();"
             "if(!window.__t142)return 'move failed';"
             "var ia=v.indexOf('项A'),ib=v.indexOf('项B'),ic=v.indexOf('续段A'),id=v.indexOf('子1');"
             "if(!(ib<ia&&ia<ic&&ic<id))return 'order='+JSON.stringify(v);"
             "var sub=v.split('\\n').find(function(l){return l.indexOf('子1')>=0;});"
             "if(!/^ +[-*+]/.test(sub))return 'child flattened='+JSON.stringify(v);"
             "return true;})()"),
         timeout=12),

    dict(name="T143 嵌套:拖到列表项中段成为其子项",
         setup=("window.Home.hide();window.SlashMenu.close();"
                "window.Editor.setValue(" + JV('- 项A\n- 项B\n- 项C\n') + ");"
                "setTimeout(function(){"
                "var lis=document.querySelectorAll('.vditor-ir .vditor-reset > ul > li');"
                "var li=lis[2];"
                "var r=li.getBoundingClientRect();"
                "li.dispatchEvent(new MouseEvent('mousemove',{clientX:r.left+20,clientY:r.top+5,bubbles:true}));"
                "setTimeout(function(){"
                "var h=document.querySelector('.block-handle.on');"
                "if(!h)return;"
                "var hr=h.getBoundingClientRect();"
                "var t=lis[0].getBoundingClientRect();"
                "h.dispatchEvent(new MouseEvent('mousedown',{clientX:hr.left+5,clientY:hr.top+5,bubbles:true,cancelable:true}));"
                "document.dispatchEvent(new MouseEvent('mousemove',{clientX:t.left+30,clientY:t.top+t.height*0.5,bubbles:true}));"
                "setTimeout(function(){"
                "var badge=document.querySelector('.block-drop-badge.on');"
                "window.__t143badge=badge?badge.textContent:'';"
                "document.dispatchEvent(new MouseEvent('mouseup',{clientX:t.left+30,clientY:t.top+t.height*0.5,bubbles:true}));"
                "setTimeout(function(){"
                "var v=window.Editor.getValue();"
                "var ls=v.split('\\n').filter(function(l){return l.trim()!=='';});"
                "window.__t143lines=ls;"
                "},400);},300);},300);},700);"),
         sleep=3.2,
         js=("(function(){"
             "if(window.__t143badge.indexOf(" + JV("成为") + ")<0||window.__t143badge.indexOf(" + JV("子项") + ")<0)"
             "return 'badge='+window.__t143badge;"
             "var ls=window.__t143lines||[];"
             "if(ls.length!==3)return 'lines='+JSON.stringify(ls);"
             "if(ls[0].indexOf('项A')<0)return 'l0='+JSON.stringify(ls);"
             "if(!/^ +[-*+]/.test(ls[1])||ls[1].indexOf('项C')<0)return 'not child='+JSON.stringify(ls);"
             "if(ls[2].indexOf('项B')<0||/^ +/.test(ls[2]))return 'l2='+JSON.stringify(ls);"
             "return true;})()"),
         timeout=12),

    dict(name="T144 嵌套:移出提升一级且子项随动",
         setup=("window.Home.hide();"
                "window.Editor.setValue(" + JV('- 项A\n  - 子B\n    - 孙D\n- 项C\n') + ");"
                "setTimeout(function(){"
                "var li=document.querySelector('.vditor-ir .vditor-reset > ul > li > ul > li');"
                "window.__t144=window.Blocks.outdentItem(window.Blocks.locate(li));"
                "},700);"),
         sleep=2.0,
         js=("(function(){var v=window.Editor.getValue();"
             "if(!window.__t144)return 'outdent failed';"
             "var ls=v.split('\\n').filter(function(l){return l.trim()!=='';});"
             "var sub=ls.find(function(l){return l.indexOf('子B')>=0;});"
             "if(!sub||/^ +/.test(sub))return 'not promoted='+JSON.stringify(v);"
             "var gr=ls.find(function(l){return l.indexOf('孙D')>=0;});"
             "if(!gr||!/^ +[-*+]/.test(gr))return 'grandchild lost='+JSON.stringify(v);"
             "return true;})()"),
         timeout=12),

    dict(name="T145 嵌套:段落移入引用再移出",
         setup=("window.Home.hide();"
                "window.Editor.setValue(" + JV('段落X\n\n> 引用内容\n') + ");"
                "setTimeout(function(){"
                "window.__t145in=window.Blocks.moveIntoQuote(window.Blocks.locateLine(0),window.Blocks.locateLine(2));"
                "setTimeout(function(){"
                "var v0=window.Editor.getValue();"
                "window.__t145v0=v0.split('\\n').filter(function(l){return l.indexOf('段落X')>=0;})[0];"
                "var qp=document.querySelector('.vditor-ir .vditor-reset > blockquote');"
                "var inner=qp&&qp.querySelectorAll('p')[1];"
                "if(!inner)return;"
                "window.__t145out=window.Blocks.moveOutOfQuote(window.Blocks.locate(inner),window.Blocks.locate(qp));"
                "},700);},700);"),
         sleep=2.6,
         js=("(function(){"
             "if(!window.__t145in)return 'move in failed';"
             "if(!window.__t145v0||window.__t145v0.indexOf('>')!==0)return 'not in quote='+window.__t145v0;"
             "if(!window.__t145out)return 'move out failed';"
             "var v=window.Editor.getValue();"
             "var lx=v.split('\\n').filter(function(l){return l.indexOf('段落X')>=0;})[0];"
             "if(!lx||lx.indexOf('>')===0)return 'still in quote='+JSON.stringify(v);"
             "if(v.indexOf('> 引用内容')<0)return 'quote damaged='+JSON.stringify(v);"
             "return true;})()"),
         timeout=12),

    dict(name="T146 嵌套:引用整体移动后内部结构原样",
         setup=("window.Home.hide();"
                "window.Editor.setValue(" + JV('> ## 内标题\n>\n> - 内项\n\n段落P\n') + ");"
                "setTimeout(function(){"
                "window.__t146=window.Blocks.moveBlock(window.Blocks.locateLine(0),1);"
                "},700);"),
         sleep=2.0,
         js=("(function(){var v=window.Editor.getValue();"
             "if(!window.__t146)return 'move failed';"
             "if(v.indexOf('段落P')>v.indexOf('## 内标题'))return 'not moved='+JSON.stringify(v);"
             "if(v.indexOf('> ## 内标题')<0||v.indexOf('> - 内项')<0)return 'inner damaged='+JSON.stringify(v);"
             "return true;})()"),
         timeout=12),

    dict(name="T147 嵌套:代码块不接收成为子项落点",
         setup=("window.Home.hide();window.SlashMenu.close();"
                "window.Editor.setValue(" + JV('段落一\n\n```js\ncode\n```\n') + ");"
                "setTimeout(function(){"
                "var p=document.querySelector('.vditor-ir .vditor-reset > p');"
                "var r=p.getBoundingClientRect();"
                "p.dispatchEvent(new MouseEvent('mousemove',{clientX:r.left+20,clientY:r.top+5,bubbles:true}));"
                "setTimeout(function(){"
                "var h=document.querySelector('.block-handle.on');"
                "if(!h)return;"
                "var hr=h.getBoundingClientRect();"
                "var cb=document.querySelector('.vditor-ir .vditor-reset div[data-type=code-block]');"
                "var t=cb.getBoundingClientRect();"
                "h.dispatchEvent(new MouseEvent('mousedown',{clientX:hr.left+5,clientY:hr.top+5,bubbles:true,cancelable:true}));"
                "document.dispatchEvent(new MouseEvent('mousemove',{clientX:t.left+40,clientY:t.top+t.height*0.5,bubbles:true}));"
                "setTimeout(function(){"
                "var badge=document.querySelector('.block-drop-badge.on');"
                "window.__t147badge=badge?badge.textContent:'';"
                "document.dispatchEvent(new MouseEvent('mouseup',{clientX:t.left+40,clientY:t.top+t.height*0.5,bubbles:true}));"
                "},300);},300);},700);"),
         sleep=2.6,
         js=("(function(){"
             "var b=window.__t147badge;"
             "if(b.indexOf(" + JV("成为") + ")>=0)return 'child-of offered on code block: '+b;"
             "if(b.indexOf(" + JV("放在") + ")<0)return 'badge='+b;"
             "return true;})()"),
         timeout=12),
]


# ----------------------------------------------------------------------------
# 执行器
# ----------------------------------------------------------------------------
results: list[tuple[str, bool, str]] = []


def _make_ev(window):
    def ev(js: str):
        try:
            return window.evaluate_js(js)
        except Exception as e:  # noqa: BLE001
            return f"EVAL_ERR: {e}"
    return ev


def _check(ev, js: str, timeout: float) -> tuple[bool, str]:
    """在超时窗口内轮询断言表达式,真值即通过。返回 (ok, 最后一次值)。"""
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        last = ev(js)
        if last is True or last == "true":
            return True, str(last)
        time.sleep(0.35)
    return False, str(last)[:300]


def _run(window, api):
    ev = _make_ev(window)
    time.sleep(4.5)  # 等 boot 完成(含会话恢复/首启欢迎流程)
    # 防阻塞:未保存确认框恒「确认」;合并/切编辑方式的 choice 取 keep_source（合并用源 / 切直改则等同直接切换）
    ev("window.confirm=function(){return true;};window.alert=function(){};"
       "if(window.App&&window.App.confirm){window.__ryuuConfirm=window.App.confirm;"
       "window.App.confirm=function(){return Promise.resolve(true);};}"
       "if(window.App&&window.App.choice){"
       "window.App.choice=function(){return Promise.resolve('keep_source');};}"
       "(function(){var w=document.getElementById('welcome-mask');"
       "if(w.classList.contains('open')){var b=document.getElementById('welcome-start');if(b)b.click();}})()")
    time.sleep(0.5)

    only = os.environ.get("E2E_ONLY", "").strip()
    prefixes = [p.strip() for p in only.split(",") if p.strip()]
    for c in CASES:
        if prefixes and not any(c["name"].startswith(p) for p in prefixes):
            continue
        try:
            # 用例间留空闲:dbg 取证表明,连续无间隔 evaluate_js 注入(setValue→setMode→setValue
            # 连发)会撞上 Vditor 内部异步任务未 settle 的竞态;真实用户操作间隔天然 >100ms。
            # 每个用例前后各留 0.5s,让 Vditor 内部任务落定,消除测试驱动的竞态。
            time.sleep(0.5)
            if c.get("before"):
                c["before"](api)
            if c.get("setup"):
                ev(c["setup"])
            time.sleep(c.get("sleep", 0.6))
            ok, last = _check(ev, c["js"], c.get("timeout", 8))
            # 可选第二阶段(setup2 + js2)
            if ok and c.get("js2"):
                if c.get("setup2"):
                    ev(c["setup2"])
                time.sleep(c.get("sleep2", 0.4))
                ok, last = _check(ev, c["js2"], c.get("timeout2", c.get("timeout", 8)))
            if ok and c.get("js3"):
                if c.get("setup3"):
                    ev(c["setup3"])
                time.sleep(c.get("sleep3", 0.4))
                ok, last = _check(ev, c["js3"], c.get("timeout3", c.get("timeout", 8)))
            if ok and c.get("py"):
                py_res = c["py"](api, ev)
                if py_res is not True:
                    ok, last = False, str(py_res)
            results.append((c["name"], ok, "" if ok else last))
            print(("PASS" if ok else "FAIL"), A(c["name"]), flush=True)
        except Exception as e:  # noqa: BLE001
            results.append((c["name"], False, f"EXC: {e}"))
            print("FAIL", A(c["name"]), "EXC", e, flush=True)

    failed = [r for r in results if not r[1]]
    print("\n================ E2E summary ================")
    print(f"passed {len(results) - len(failed)}/{len(results)}")
    for name, ok, detail in failed:
        print(f"  FAIL {A(name)} -> {detail}")
    sys.stdout.flush()
    window.destroy()


def main() -> int:
    config = Config()
    # 预置 last_file + startup_page=home：断言「有上次文档也不恢复」，只进首页
    config.update({
        "startup_page": "home",
        "last_file": str(SMALL_MD),
        "last_folder": str(REPO_DIR),
        # 跳过首启欢迎流程（其后的学习仓库静默注册会污染 T24 等的仓库计数断言；
        # 欢迎页本身由 T13 主动重开覆盖）
        "welcome_shown": True,
    })
    api = Api(config)
    index = str(ROOT / "app" / "web" / "index.html")
    # Vditor 渲染依赖窗口 rAF/焦点，E2E 须真实窗口；跑前关闭其他 RyuuMD / WebView2。
    # T19 字数统计走 loadDoc 路径（合成 InputEvent 无法驱动 Vditor 内部 input 回调）。
    window = webview.create_window(
        title="RyuuMD E2E", url=index, js_api=api, width=1200, height=800
    )
    api.bind_window(window)
    start_webview(lambda: threading.Thread(target=_run, args=(window, api), daemon=True).start(),
                  debug=False)
    return 1 if any(not ok for _, ok, _ in results) else 0


if __name__ == "__main__":
    sys.exit(main())
