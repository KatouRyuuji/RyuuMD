/* 命令菜单控制器（统一面板）。
   触发方式：
     1. 在编辑区键入 "/"（行首或空白后）——边输入边过滤（纯插入命令）
     2. 在编辑区右键——顶部剪贴板组（复制/剪切/粘贴）+ 文件组（打开所在目录）+ 全部插入命令
   两种方式使用同一个 #slash-menu 面板，外观与行为完全一致。
   操作：上下箭头选择（跳过禁用项），Tab / Enter 插入，Esc 关闭。
   触发词与展示词随操作风格（notion=英文 / wolai=拼音）切换。 */
(function () {
  const menu = document.getElementById("slash-menu");
  let editor = null;       // Vditor 实例
  let style = "notion";    // 当前操作风格
  let open = false;
  let mode = "slash";      // slash（带 / 查询）| context（右键）| wiki（[[ 查询）
  let items = [];          // 当前过滤结果
  let allItems = null;     // context 模式的完整命令列表（打字过滤的源）
  let filter = "";         // context 模式的打字过滤串
  let activeIdx = 0;
  let wikiSeq = 0;
  let wikiTimer = 0; // wiki 查询防抖（每击键一次后端往返太贵，且结果闪烁）

  function setEditor(ed) { editor = ed; }
  function setStyle(s) { style = s === "wolai" ? "wolai" : "notion"; }

  function editorEl() {
    // Vditor 同时创建 wysiwyg/sv/ir 三个面板、仅显示其一，
    // 取可见的那个（display:none 的隐藏面板 offsetParent 为 null）
    const els = document.querySelectorAll("#editor [contenteditable='true']");
    for (const el of els) if (el.offsetParent !== null) return el;
    return null;
  }

  /* 读取光标前的 "/查询" 上下文（仅 slash 模式用） */
  function caretContext() {
    const sel = window.getSelection();
    if (!sel || !sel.rangeCount || !sel.isCollapsed) return null;
    const range = sel.getRangeAt(0);
    const node = range.startContainer;
    if (node.nodeType !== Node.TEXT_NODE) return null;
    const before = node.textContent.slice(0, range.startOffset);
    let rect = range.getBoundingClientRect();
    if (!rect || (rect.top === 0 && rect.left === 0)) {
      const r2 = range.cloneRange();
      r2.collapse(true);
      const rects = r2.getClientRects();
      if (rects.length) rect = rects[0];
    }
    const wiki = before.match(/\[\[([^\]]*)$/);
    if (wiki) {
      return { kind: "wiki", query: wiki[1], deleteLen: wiki[0].length, rect };
    }
    // "/" 位于行首或空白/引用符之后，后跟非空白非斜杠串（允许中英文字母数字）
    const m = before.match(/(?:^|[\s>])\/([^\s/]*)$/);
    if (!m) return null;
    return { kind: "slash", query: m[1], deleteLen: m[1].length + 1, rect };
  }

  function esc(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;")
      .replace(/>/g, "&gt;").replace(/"/g, "&quot;");
  }

  function render() {
    // context 模式头部：打字过滤提示（不占焦点，键盘事件在 document 捕获）
    const head = mode === "context"
      ? `<div class="slash-filter">${filter ? "过滤：" + esc(filter) : "输入以过滤…"}</div>`
      : "";
    if (!items.length) {
      menu.innerHTML = head + '<div class="slash-empty">没有匹配的命令</div>';
      return;
    }
    let html = "";
    let lastGroup = null;
    items.forEach((cmd, i) => {
      if (cmd.group !== lastGroup) {
        html += `<div class="slash-group-label">${esc(cmd.group)}</div>`;
        lastGroup = cmd.group;
      }
      const icon = window.ICONS[cmd.icon] || "";
      const key = cmd.keys != null ? cmd.keys : window.displayKey(cmd, style);
      html += `
        <div class="slash-item${i === activeIdx ? " active" : ""}${cmd.disabled ? " disabled" : ""}" data-idx="${i}">
          <span class="si-icon">${icon}</span>
          <span class="si-body">
            <div class="si-title">${esc(cmd.title)}</div>
            ${cmd.desc ? `<div class="si-desc">${esc(cmd.desc)}</div>` : ""}
          </span>
          ${key ? `<span class="si-keys">${esc(key)}</span>` : ""}
        </div>`;
    });
    menu.innerHTML = head + html;
    menu.querySelectorAll(".slash-item").forEach((el) => {
      el.addEventListener("mousedown", (e) => {
        e.preventDefault();
        activeIdx = parseInt(el.dataset.idx, 10);
        choose();
      });
      /* 鼠标悬停交给 CSS :hover（淡色），不写 activeIdx：实心选中态只跟随
         键盘导航与初始位置，瞬间 hover 与持续选中不再共用最强填充 */
    });
    scrollActiveIntoView();
  }

  function highlight() {
    menu.querySelectorAll(".slash-item").forEach((el) => {
      el.classList.toggle("active", parseInt(el.dataset.idx, 10) === activeIdx);
    });
    scrollActiveIntoView();
  }

  function scrollActiveIntoView() {
    const el = menu.querySelector(".slash-item.active");
    if (el) el.scrollIntoView({ block: "nearest" });
  }

  /* slash 模式：根据查询展示/刷新菜单 */
  function showSlash(ctx) {
    items = window.filterCommands(ctx.query, style);
    if (!items.length) { close(); return; }
    activeIdx = 0;
    mode = "slash";
    render();
    menu.classList.add("open");
    open = true;
    position(ctx.rect, true);
  }

  async function showWiki(ctx) {
    const my = ++wikiSeq;
    mode = "wiki";
    let files = [];
    if (window.App && window.App.listVaultFiles) {
      try { files = await window.App.listVaultFiles(ctx.query); } catch (e) { files = []; }
    }
    if (my !== wikiSeq) return;
    const q = (ctx.query || "").trim();
    items = (files || []).slice(0, 40).map((f) => ({
      id: "wiki-" + f.path,
      icon: "fileText",
      title: f.stem || f.name,
      desc: f.rel || "",
      group: "笔记",
      keys: "[[ ]]",
      wikiInsert: "[[" + (f.stem || f.name.replace(/\.md$/i, "")) + "]]",
    }));
    if (q && !items.some((it) => it.title.toLowerCase() === q.toLowerCase())) {
      items.unshift({
        id: "wiki-new",
        icon: "plus",
        title: "插入 [[" + q + "]]",
        desc: "尚未创建的笔记，单击链接时可新建",
        group: "笔记",
        keys: "[[ ]]",
        wikiInsert: "[[" + q + "]]",
      });
    }
    if (!items.length) {
      items = [{
        id: "wiki-empty",
        icon: "wiki",
        title: "插入双向链接",
        desc: "输入笔记名进行过滤",
        group: "笔记",
        keys: "[[ ]]",
        wikiInsert: "[[]]",
      }];
    }
    activeIdx = 0;
    render();
    menu.classList.add("open");
    open = true;
    position(ctx.rect, true);
  }

  /* context 模式打字过滤：完整列表存 allItems，按标题/描述/分组/快捷键包含匹配。
     不过滤禁用项（保持可见但不可选，与未过滤时一致） */
  function applyFilter() {
    if (!allItems) return;
    const q = filter.trim().toLowerCase();
    items = !q ? allItems : allItems.filter((c) => {
      const hay = ((c.title || "") + " " + (c.desc || "") + " " + (c.group || "") + " " +
        (c.keys != null ? c.keys : window.displayKey(c, style) || "")).toLowerCase();
      return hay.indexOf(q) >= 0;
    });
    activeIdx = 0;
    if (items[0] && items[0].disabled) step(1);
  }

  /* 通用菜单入口（操作柄等外部调用）：直接给完整命令列表，复用同一面板 */
  function showItems(list, x, y) {
    allItems = list || [];
    filter = "";
    items = allItems;
    if (!items.length) { allItems = null; return; }
    activeIdx = 0;
    mode = "context";
    if (items[0] && items[0].disabled) step(1);
    render();
    menu.classList.add("open");
    open = true;
    position({ left: x, right: x, top: y, bottom: y }, false);
  }

  function isOpen() { return open; }

  /* 右键模式：鼠标处展示剪贴板组 + 块操作组（表格/代码块/数学块，按光标所在块
     动态出现）+ 转换为组（框选段落转格式）+ 全部插入命令 */
  function showContext(x, y) {
    const blk = window.Convert ? window.Convert.blockItemsForContext() : [];
    const conv = window.Convert ? window.Convert.itemsForContext() : [];
    allItems = clipboardItems().concat(fileItems(), blk, conv, window.filterCommands("", style));
    filter = "";
    items = allItems;
    activeIdx = 0;
    mode = "context";
    if (items[0] && items[0].disabled) step(1); // 首项禁用则落到首个可用项
    render();
    menu.classList.add("open");
    open = true;
    position({ left: x, right: x, top: y, bottom: y }, false);
  }

  /* 右键菜单的剪贴板伪命令（仅 context 模式，不参与 "/" 过滤）。
     复制/剪切在无选区时禁用；粘贴始终可用（剪贴板为空/无权限时退化为提示）。 */
  function clipboardItems() {
    const sel = window.getSelection();
    const hasSel = !!(sel && !sel.isCollapsed && inEditor(sel.anchorNode));
    return [
      { id: "clip-copy", clip: "copy", title: "复制", desc: "", group: "剪贴板",
        icon: "copy", keys: "Ctrl+C", disabled: !hasSel },
      { id: "clip-cut", clip: "cut", title: "剪切", desc: "", group: "剪贴板",
        icon: "cut", keys: "Ctrl+X", disabled: !hasSel },
      { id: "clip-paste", clip: "paste", title: "粘贴", desc: "", group: "剪贴板",
        icon: "clipboard", keys: "Ctrl+V", disabled: false },
    ];
  }

  /* 右键菜单的文件伪命令（仅 context 模式）：对当前已保存文档的磁盘操作 */
  function fileItems() {
    const has = !!(window.App && window.App.hasSavedFile && window.App.hasSavedFile());
    return [
      { id: "file-reveal-dir", appAction: "revealCurrentDir",
        title: "在资源管理器中打开所在目录", desc: "", group: "文件",
        icon: "folderOpen", keys: "", disabled: !has },
    ];
  }

  /* 复制/剪切走 execCommand（菜单项 mousedown 已 preventDefault，编辑器选区不丢）；
     粘贴读系统剪贴板后插入（Vditor insertValue 按当前选区替换/插入）。 */
  function doClipboard(kind) {
    if (!editor) return;
    if (kind === "copy" || kind === "cut") {
      try { document.execCommand(kind); } catch (e) { /* ignore */ }
      return;
    }
    navigator.clipboard.readText().then((text) => {
      if (!text) return;
      editor.focus();
      editor.insertValue(text, true);
    }).catch(() => {
      if (window.App && window.App.toast) window.App.toast("无法读取剪贴板，请按 Ctrl+V 粘贴");
    });
  }

  function position(rect, below) {
    const mh = menu.offsetHeight || 320;
    const mw = menu.offsetWidth || 300;
    let top = below ? rect.bottom + 6 : rect.top;
    let left = rect.left;
    if (top + mh > window.innerHeight - 10) top = (below ? rect.top : rect.bottom) - mh - 6;
    if (left + mw > window.innerWidth - 10) left = window.innerWidth - mw - 10;
    menu.style.top = Math.max(8, top) + "px";
    menu.style.left = Math.max(8, left) + "px";
  }

  function close() {
    menu.classList.remove("open");
    open = false;
    items = [];
    allItems = null;
    filter = "";
    clearTimeout(wikiTimer);
  }

  /* 插入选中命令；slash 模式先删除已键入的 "/查询"；剪贴板伪命令走 doClipboard */
  function choose() {
    const cmd = items[activeIdx];
    const wasSlash = mode === "slash" || mode === "wiki";
    const ctx = wasSlash ? caretContext() : null;
    if (!cmd || cmd.disabled) return; // 禁用项不响应、不关菜单
    close();
    if (cmd.clip) { doClipboard(cmd.clip); return; }
    if (cmd.struct) {
      // 操作柄菜单的结构动作（移动/删除/转换/范围切换），由 BlockUI 执行
      if (window.BlockUI) window.BlockUI.applyMenuAction(cmd.struct);
      return;
    }
    if (cmd.appAction) {
      // 转发给 App 层动作（如「在资源管理器中打开所在目录」）
      if (window.App && window.App[cmd.appAction]) window.App[cmd.appAction]();
      return;
    }
    if (cmd.blockop) {
      // 块操作（表格行列/代码块/数学块）；返回 0 表示定位失败
      const done = window.Convert ? window.Convert.applyBlockOp(cmd.blockop, editor) : 0;
      if (!done && window.App && window.App.toast) window.App.toast("操作未完成");
      return;
    }
    if (cmd.convert) {
      // 段落转换：无块被转换（不支持的块类型）时提示
      const done = window.Convert ? window.Convert.apply(cmd.convert, editor) : 0;
      if (!done && window.App && window.App.toast) window.App.toast("当前段落不支持转换");
      return;
    }
    if (wasSlash && ctx) {
      const sel = window.getSelection();
      if (sel && sel.rangeCount) {
        for (let i = 0; i < ctx.deleteLen; i++) sel.modify("extend", "backward", "character");
        sel.deleteFromDocument();
      }
    }
    if (editor) {
      editor.focus();
      if (cmd.wikiInsert != null) editor.insertValue(cmd.wikiInsert, true);
      else window.runCommand(cmd, editor);
    }
  }

  /* wiki 模式：120ms 防抖后按「当时的」光标上下文查询，避免用击键瞬间的旧
     查询发请求；查询期间菜单保持旧结果，不闪空。 */
  function scheduleWiki() {
    clearTimeout(wikiTimer);
    wikiTimer = setTimeout(() => {
      const ctx = caretContext();
      if (ctx && ctx.kind === "wiki") showWiki(ctx);
    }, 120);
  }

  /* 输入事件：仅 slash 模式随输入刷新；右键模式不被打断 */
  function onInput() {
    if (mode === "context" && open) return;
    const ctx = caretContext();
    if (ctx && ctx.kind === "wiki") scheduleWiki();
    else if (ctx) showSlash(ctx);
    else if (open && (mode === "slash" || mode === "wiki")) close();
  }

  /* 上下导航：跳过禁用项（如无选区时的复制/剪切） */
  function step(dir) {
    if (!items.length) return;
    let i = activeIdx;
    for (let n = 0; n < items.length; n++) {
      i = (i + dir + items.length) % items.length;
      if (!items[i].disabled) break;
    }
    activeIdx = i;
  }

  /* 键盘导航：菜单打开时拦截 上/下/Tab/Enter/Esc；
     context 模式额外拦截可打印字符与 Backspace 做打字过滤
     （此前这些键会穿透菜单直接插入编辑器，属于盲打） */
  function onKeydown(e) {
    if (!open) return;
    // IME 组词期不拦截：context/wiki 模式的过滤与 Enter 上屏都必须等组词结束，
    // 否则拼音会被当作过滤词或触发组词期 insertValue（中文+拼音双份）
    if (e.isComposing || e.keyCode === 229) return;
    if (e.__menuHandled) return;
    if (mode === "context" && !e.ctrlKey && !e.altKey && !e.metaKey &&
      (e.key.length === 1 || e.key === "Backspace")) {
      e.__menuHandled = true;
      e.preventDefault(); e.stopPropagation();
      filter = e.key === "Backspace" ? filter.slice(0, -1) : filter + e.key;
      applyFilter();
      render();
      return;
    }
    // 光标/翻页键：关闭菜单并放行给编辑器移动光标（Notion 同款；
    // 此前这些键穿透菜单移动光标后菜单悬空在旧位置）
    const caretKeys = ["ArrowLeft", "ArrowRight", "Home", "End", "PageUp", "PageDown"];
    if (caretKeys.includes(e.key)) { close(); return; }
    const navKeys = ["ArrowDown", "ArrowUp", "Enter", "Tab", "Escape"];
    if (!navKeys.includes(e.key)) return;
    e.__menuHandled = true;
    if (e.key === "ArrowDown") {
      e.preventDefault(); e.stopPropagation();
      step(1);
      highlight();
    } else if (e.key === "ArrowUp") {
      e.preventDefault(); e.stopPropagation();
      step(-1);
      highlight();
    } else if (e.key === "Enter" || e.key === "Tab") {
      // Tab 与 Enter 都执行插入
      e.preventDefault(); e.stopPropagation();
      if (items.length) choose(); else close();
    } else if (e.key === "Escape") {
      e.preventDefault(); e.stopPropagation();
      close();
    }
  }

  function inEditor(target) {
    const host = document.getElementById("editor");
    return host && target && host.contains(target);
  }

  /* 正在编辑器正文里打字（contenteditable），不是日后塞进 #editor 的输入框 */
  function inEditorTyping(target) {
    if (!inEditor(target)) return false;
    if (target.isContentEditable) return true;
    return !!(target.closest && target.closest("#editor [contenteditable='true']"));
  }

  /* 绑定一次到 document（事件委托），Vditor 重建可编辑元素后仍有效。 */
  function attach() {
    if (document.__menuBound) return;
    document.__menuBound = true;

    document.addEventListener("input", (e) => {
      if (inEditor(e.target)) onInput();
    });
    // 捕获阶段，菜单打开时优先于 Vditor 处理导航键（尤其 Tab/Enter）。
    // 空标题的 Enter/Backspace 只拦编辑器内的按键，查找条/面板不受影响。
    document.addEventListener("keydown", (e) => {
      if (open) onKeydown(e);
      else if (inEditorTyping(e.target) && window.Convert &&
        (window.Convert.handleEmptyEnter(e) || window.Convert.handleEmptyBackspace(e))) return;
    }, true);
    document.addEventListener("contextmenu", (e) => {
      if (inEditor(e.target)) {
        e.preventDefault();
        showContext(e.clientX, e.clientY);
      }
    });
    document.addEventListener("focusout", (e) => {
      if (inEditor(e.target)) setTimeout(() => {
        if (open && (mode === "slash" || mode === "wiki")) close();
      }, 150);
    });
  }

  document.addEventListener("click", (e) => {
    if (open && !menu.contains(e.target)) close();
  });
  window.addEventListener("resize", () => { if (open) close(); });

  window.SlashMenu = { setEditor, setStyle, attach, close, showItems, isOpen };
})();
