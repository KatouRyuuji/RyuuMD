# -*- coding: utf-8 -*-
"""RyuuMD 真实窗口端到端测试(E2E)。

原理:启动真实 pywebview 窗口加载 app/web/index.html,用 evaluate_js 注入
操作与断言(探针模式),逐用例收集 PASS/FAIL,全部完成后输出汇总并销毁窗口。

隔离与防阻塞:
- 启动前将 APPDATA 指到临时目录(配置不污染用户数据);
- hook window.confirm 恒 true(未保存确认框不阻塞自动化);
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
SMALL_MD.write_text("# 小文档\n\n普通正文。\n", encoding="utf-8")
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
    dict(name="T00 首启无会话:显示首页(空态+快速操作)", sleep=0,
         # 临时 APPDATA 下无上次会话,welcome 关闭后应落到首页;验毕隐藏首页继续编辑器用例
         js=("(function(){if(!window.Home||!window.Home.isOpen())return 'home not open';"
             "if(document.querySelectorAll('#home .quick-card').length!==3)return 'quick cards';"
             "if(getComputedStyle(document.getElementById('repo-empty')).display==='none')return 'repo empty hidden';"
             "return true;})()"),
         timeout=12,
         setup2="window.Home.hide()", sleep2=0.3,
         js2="!window.Home.isOpen()"),

    dict(name="T01 启动:全局对象就绪、编辑器 ready", sleep=0,
         js="!!(window.App&&window.Editor&&window.Sidebar&&window.Welcome&&window.Settings&&window.SlashMenu&&window.Home&&window.Editor.isReady())"),

    dict(name="T02 工具栏按钮均有中文文字", sleep=0,
         js="(function(){var bs=document.querySelectorAll('#toolbar .icon-btn');return bs.length>=7&&Array.from(bs).every(function(b){var l=b.querySelector('.ib-label');return l&&l.textContent.trim().length>0;});})()"),

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
             "&&document.querySelector('#btn-theme .ib-icon').innerHTML.indexOf('<svg')>=0"
             "&&document.querySelector('#btn-theme .ib-label').textContent.trim().length>0")),

    dict(name="T11b 主题切回 light",
         setup="document.getElementById('btn-theme').click()", sleep=0.8,
         js="document.documentElement.getAttribute('data-theme')==='light'"),

    dict(name="T12 设置弹窗:打开五行设置并可关闭",
         setup="document.getElementById('btn-settings').click()", sleep=0.5,
         js=("(function(){var m=document.getElementById('settings-mask');"
             "return m.classList.contains('open')&&m.querySelectorAll('.setting-row').length===5;})()"),
         setup2="document.getElementById('set-close').click()", sleep2=0.4,
         js2="!document.getElementById('settings-mask').classList.contains('open')"),

    dict(name="T13 欢迎页重开:结构完整、二维码加载、可关闭",
         setup="window.App.showWelcome()", sleep=1.2,
         js=("(function(){var m=document.getElementById('welcome-mask');"
             "return m.classList.contains('open')&&m.querySelectorAll('.feature-card').length===6"
             "&&m.querySelectorAll('.style-opt').length===2"
             "&&document.getElementById('welcome-qr').classList.contains('has-img');})()"),
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
         js=("(function(){if(window.Editor.getMode()!=='sv')return 'mode='+window.Editor.getMode();"
             "return window.Editor.getValue().length>400000?true:'len='+window.Editor.getValue().length;})()"),
         timeout=25,
         # 第二阶段:点开小文档恢复 ir
         setup2="document.querySelectorAll('#file-tree .tree-item.file')[1].click()",
         sleep2=1.0,
         js2=("(function(){if(window.Editor.getMode()!=='ir')return 'mode='+window.Editor.getMode();"
              "return window.Editor.getValue().indexOf("+JV('小文档')+")>=0?true:'content';})()")),

    dict(name="T19 字数统计:经 loadDoc 路径统计正确",
         # 字数统计由 app.js 在 loadDoc / 编辑变化时调用 updateCount,去空白字符计数。
         # 合成 InputEvent 无法驱动 Vditor 内部 input 回调(真实键入才可,见手动项),改走 loadDoc 可达路径:
         # 点开 T18 渲染的小文档「# 小文档\n\n普通正文。\n」,去空白=「#小文档普通正文。」= 9 字
         # (# 与句号非空白字符,计入)。
         setup="document.querySelectorAll('#file-tree .tree-item.file')[1].click()",
         sleep=1.5,
         js=("(function(){var sc=document.getElementById('sb-count').textContent.trim();"
             "return sc==='9 '+(" + JV('字') + ")?true:'count='+sc;})()"),
         timeout=12),

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
             "var bg=getComputedStyle(el,'::selection').backgroundColor;"
             "if(!bg||bg==='rgba(0, 0, 0, 0)'||bg==='transparent')return 'no selection style:'+bg;"
             "if(bg.indexOf('234, 242, 248')>=0)return 'still faint #eaf2f8:'+bg;"
             "if(bg.indexOf('52, 152, 219')<0)return 'unexpected:'+bg;"
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


def _run(window):
    ev = _make_ev(window)
    time.sleep(4.5)  # 等 boot 完成(含会话恢复/首启欢迎流程)
    # 防阻塞:未保存确认框恒「确认」;并关闭可能弹出的首启欢迎窗
    ev("window.confirm=function(){return true;};window.alert=function(){};"
       "(function(){var w=document.getElementById('welcome-mask');"
       "if(w.classList.contains('open')){var b=document.getElementById('welcome-start');if(b)b.click();}})()")
    time.sleep(0.5)

    for c in CASES:
        try:
            # 用例间留空闲:dbg 取证表明,连续无间隔 evaluate_js 注入(setValue→setMode→setValue
            # 连发)会撞上 Vditor 内部异步任务未 settle 的竞态;真实用户操作间隔天然 >100ms。
            # 每个用例前后各留 0.5s,让 Vditor 内部任务落定,消除测试驱动的竞态。
            time.sleep(0.5)
            if c.get("setup"):
                ev(c["setup"])
            time.sleep(c.get("sleep", 0.6))
            ok, last = _check(ev, c["js"], c.get("timeout", 8))
            # 可选第二阶段(setup2 + js2)
            if ok and c.get("js2"):
                if c.get("setup2"):
                    ev(c["setup2"])
                time.sleep(c.get("sleep2", 0.4))
                ok, last = _check(ev, c["js2"], c.get("timeout", 8))
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
    api = Api(config)
    index = str(ROOT / "app" / "web" / "index.html")
    # 说明:Vditor 渲染依赖窗口 rAF/焦点,E2E 须真实窗口运行;跑前请关闭其他 RyuuMD/
    # WebView2 实例(残留进程会致首用例即败)。历史待查项已清零:T10(待办勾选)经
    # 移除自绘守卫改走 Vditor 原生处理修复;T19(字数统计)改走 loadDoc 可达路径,
    # 合成 InputEvent 无法驱动 Vditor 内部 input 回调属测试方法限制(见 docs/TEST_PLAN.md)。
    window = webview.create_window(
        title="RyuuMD E2E", url=index, js_api=api, width=1200, height=800
    )
    api.bind_window(window)
    webview.start(lambda: threading.Thread(target=_run, args=(window,), daemon=True).start(),
                  gui="edgechromium", debug=False)
    return 1 if any(not ok for _, ok, _ in results) else 0


if __name__ == "__main__":
    sys.exit(main())
