/* 命令菜单控制器（统一面板）。
   触发方式：
     1. 在编辑区键入 "/"（行首或空白后）——边输入边过滤
     2. 在编辑区右键——展示全部命令
   两种方式使用同一个 #slash-menu 面板，外观与行为完全一致。
   操作：上下箭头选择，Tab / Enter 插入，Esc 关闭。
   触发词与展示词随操作风格（notion=英文 / wolai=拼音）切换。 */
(function () {
  const menu = document.getElementById("slash-menu");
  let editor = null;       // Vditor 实例
  let style = "notion";    // 当前操作风格
  let open = false;
  let mode = "slash";      // slash（带 / 查询）| context（右键）
  let items = [];          // 当前过滤结果
  let activeIdx = 0;

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
    // "/" 位于行首或空白/引用符之后，后跟非空白非斜杠串（允许中英文字母数字）
    const m = before.match(/(?:^|[\s>])\/([^\s/]*)$/);
    if (!m) return null;
    let rect = range.getBoundingClientRect();
    if (!rect || (rect.top === 0 && rect.left === 0)) {
      const r2 = range.cloneRange();
      r2.collapse(true);
      const rects = r2.getClientRects();
      if (rects.length) rect = rects[0];
    }
    return { query: m[1], deleteLen: m[1].length + 1, rect };
  }

  function render() {
    if (!items.length) {
      menu.innerHTML = '<div class="slash-empty">没有匹配的命令</div>';
      return;
    }
    let html = "";
    let lastGroup = null;
    items.forEach((cmd, i) => {
      if (cmd.group !== lastGroup) {
        html += `<div class="slash-group-label">${cmd.group}</div>`;
        lastGroup = cmd.group;
      }
      const icon = window.ICONS[cmd.icon] || "";
      const key = window.displayKey(cmd, style);
      html += `
        <div class="slash-item${i === activeIdx ? " active" : ""}" data-idx="${i}">
          <span class="si-icon">${icon}</span>
          <span class="si-body">
            <div class="si-title">${cmd.title}</div>
            ${cmd.desc ? `<div class="si-desc">${cmd.desc}</div>` : ""}
          </span>
          <span class="si-keys">${key}</span>
        </div>`;
    });
    menu.innerHTML = html;
    menu.querySelectorAll(".slash-item").forEach((el) => {
      el.addEventListener("mousedown", (e) => {
        e.preventDefault();
        activeIdx = parseInt(el.dataset.idx, 10);
        choose();
      });
      el.addEventListener("mousemove", () => {
        const idx = parseInt(el.dataset.idx, 10);
        if (idx !== activeIdx) { activeIdx = idx; highlight(); }
      });
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

  /* 右键模式：鼠标处展示全部命令 */
  function showContext(x, y) {
    items = window.filterCommands("", style);
    activeIdx = 0;
    mode = "context";
    render();
    menu.classList.add("open");
    open = true;
    position({ left: x, right: x, top: y, bottom: y }, false);
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
  }

  /* 插入选中命令；slash 模式先删除已键入的 "/查询" */
  function choose() {
    const cmd = items[activeIdx];
    const wasSlash = mode === "slash";
    const ctx = wasSlash ? caretContext() : null;
    close();
    if (!cmd) return;
    if (wasSlash && ctx) {
      const sel = window.getSelection();
      if (sel && sel.rangeCount) {
        for (let i = 0; i < ctx.deleteLen; i++) sel.modify("extend", "backward", "character");
        sel.deleteFromDocument();
      }
    }
    if (editor) {
      editor.focus();
      window.runCommand(cmd, editor);
    }
  }

  /* 输入事件：仅 slash 模式随输入刷新；右键模式不被打断 */
  function onInput() {
    if (mode === "context" && open) return;
    const ctx = caretContext();
    if (ctx) showSlash(ctx);
    else if (open && mode === "slash") close();
  }

  /* 键盘导航：菜单打开时拦截 上/下/Tab/Enter/Esc */
  function onKeydown(e) {
    if (!open) return;
    if (e.__menuHandled) return;
    const navKeys = ["ArrowDown", "ArrowUp", "Enter", "Tab", "Escape"];
    if (!navKeys.includes(e.key)) return;
    e.__menuHandled = true;
    if (e.key === "ArrowDown") {
      e.preventDefault(); e.stopPropagation();
      activeIdx = (activeIdx + 1) % Math.max(items.length, 1);
      highlight();
    } else if (e.key === "ArrowUp") {
      e.preventDefault(); e.stopPropagation();
      activeIdx = (activeIdx - 1 + items.length) % Math.max(items.length, 1);
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

  /* 绑定一次到 document（事件委托），Vditor 重建可编辑元素后仍有效。 */
  function attach() {
    if (document.__menuBound) return;
    document.__menuBound = true;

    document.addEventListener("input", (e) => {
      if (inEditor(e.target)) onInput();
    });
    // 捕获阶段，菜单打开时优先于 Vditor 处理导航键（尤其 Tab/Enter）
    document.addEventListener("keydown", (e) => { if (open) onKeydown(e); }, true);
    document.addEventListener("contextmenu", (e) => {
      if (inEditor(e.target)) {
        e.preventDefault();
        showContext(e.clientX, e.clientY);
      }
    });
    document.addEventListener("focusout", (e) => {
      if (inEditor(e.target)) setTimeout(() => { if (open && mode === "slash") close(); }, 150);
    });
  }

  document.addEventListener("click", (e) => {
    if (open && !menu.contains(e.target)) close();
  });
  window.addEventListener("resize", () => { if (open) close(); });

  window.SlashMenu = { setEditor, setStyle, attach, close };
})();
