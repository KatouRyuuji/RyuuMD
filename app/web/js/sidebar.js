/* 侧栏：文件树 + 大纲 + 最近打开。
   文件树由后端扫描结果渲染；大纲从编辑器标题实时生成；最近打开由后端记录。
   点击一律走容器级事件委托：目录很大时逐节点 addEventListener 会明显拖慢渲染。 */
(function () {
  const fileTreeEl = document.getElementById("file-tree");
  const filesEmpty = document.getElementById("files-empty");
  const outlineList = document.getElementById("outline-list");
  const outlineEmpty = document.getElementById("outline-empty");
  const recentList = document.getElementById("recent-list");
  const recentEmpty = document.getElementById("recent-empty");
  const recentFoot = document.getElementById("recent-foot");
  const treeMenu = document.getElementById("tree-menu");
  const linksList = document.getElementById("links-list");
  const linksEmpty = document.getElementById("links-empty");

  let onOpenFile = null;  // (path) => void
  let onJumpHeading = null; // (id) => void
  let onOpenRecent = null;  // (path, kind) => void
  // 文件管理操作（真实磁盘文件），由 app.js 注入
  let onRenameFile = null;
  let onMoveFile = null;
  let onRevealFile = null;
  let onDeleteFile = null;
  let onNewFile = null;
  let onNewFolder = null;
  let activePath = null;

  function setHandlers({ openFile, jumpHeading, openRecent, renameFile, moveFile, revealFile, deleteFile, newFile, newFolder }) {
    onOpenFile = openFile;
    onJumpHeading = jumpHeading;
    onOpenRecent = openRecent;
    onRenameFile = renameFile;
    onMoveFile = moveFile;
    onRevealFile = revealFile;
    onDeleteFile = deleteFile;
    onNewFile = newFile;
    onNewFolder = newFolder;
  }

  // —— 文件树 ——
  function renderTree(tree, rootName) {
    if (!tree || !tree.length) {
      filesEmpty.style.display = "block";
      filesEmpty.innerHTML = rootName
        ? `「${esc(rootName)}」中没有 Markdown 文件<br><button class="btn" id="empty-new-file">新建笔记</button>`
        : '还没有打开文件夹<br><button class="btn" id="empty-open-folder">打开文件夹</button>';
      rebindEmpty();
      fileTreeEl.innerHTML = "";
      return;
    }
    filesEmpty.style.display = "none";
    // 整树拼成一个字符串一次性赋值，只触发一次 HTML 解析与回流
    fileTreeEl.innerHTML = buildNodes(tree);
  }

  function buildNodes(nodes) {
    return nodes
      .map((n) => {
        if (n.type === "dir") {
          return `
            <div class="tree-dir">
              <button type="button" class="tree-item dir" data-path="${esc(n.path)}" aria-expanded="true" aria-label="展开或折叠文件夹：${esc(n.name)}">
                <span class="caret">${window.ICONS.chevron}</span>
                <span class="tw-icon">${window.ICONS.folder}</span>
                <span class="tw-name">${esc(n.name)}</span>
              </button>
              <div class="tree-children" role="group">${buildNodes(n.children || [])}</div>
            </div>`;
        }
        return `
          <button type="button" class="tree-item file" data-path="${esc(n.path)}" title="${esc(n.path)}" aria-label="打开文件：${esc(n.name)}">
            <span class="tw-icon">${window.ICONS.fileText}</span>
            <span class="tw-name">${esc(n.name)}</span>
          </button>`;
      })
      .join("");
  }

  // 文件树点击（委托，仅绑定一次）：文件 -> 打开；目录 -> 折叠/展开
  fileTreeEl.addEventListener("click", (e) => {
    const item = e.target.closest(".tree-item");
    if (!item) return;
    if (item.classList.contains("file")) {
      if (onOpenFile) onOpenFile(item.dataset.path);
      return;
    }
    item.classList.toggle("collapsed");
    item.setAttribute("aria-expanded", String(!item.classList.contains("collapsed")));
    const children = item.parentElement.querySelector(".tree-children");
    if (children) children.style.display = item.classList.contains("collapsed") ? "none" : "block";
  });

  fileTreeEl.addEventListener("keydown", (e) => {
    const item = e.target.closest(".tree-item");
    if (!item) return;
    if ((e.key === "ArrowRight" && item.classList.contains("collapsed")) ||
        (e.key === "ArrowLeft" && !item.classList.contains("collapsed"))) {
      if (item.classList.contains("dir")) {
        e.preventDefault();
        item.click();
      }
    } else if (e.key === "ContextMenu" || (e.shiftKey && e.key === "F10")) {
      e.preventDefault();
      const rect = item.getBoundingClientRect();
      openMenu(item.classList.contains("dir") ? "dir" : "file", item.dataset.path, rect.left, rect.bottom, true);
    }
  });

  // —— 文件/目录右键菜单（文件：重命名等；目录：新建笔记/文件夹） ——
  const FILE_OPS = [
    { act: "rename", label: "重命名", icon: "edit" },
    { act: "move", label: "移动到…", icon: "move" },
    { act: "reveal", label: "在资源管理器中显示", icon: "folderOpen" },
    { act: "delete", label: "删除（移入回收站）", icon: "trash", danger: true },
  ];
  const DIR_OPS = [
    { act: "new-file", label: "新建笔记", icon: "plus" },
    { act: "new-folder", label: "新建文件夹", icon: "folder" },
    { act: "reveal", label: "在资源管理器中显示", icon: "folderOpen" },
  ];
  let menuPath = null;
  let menuKind = "file";
  let menuTrigger = null;

  fileTreeEl.addEventListener("contextmenu", (e) => {
    const file = e.target.closest(".tree-item.file");
    const dir = e.target.closest(".tree-item.dir");
    if (file) {
      e.preventDefault();
      openMenu("file", file.dataset.path, e.clientX, e.clientY, false);
    } else if (dir) {
      e.preventDefault();
      openMenu("dir", dir.dataset.path, e.clientX, e.clientY, false);
    }
  });

  function openMenu(kind, path, x, y, focusFirst) {
    menuKind = kind;
    menuPath = path;
    menuTrigger = focusFirst ? fileTreeEl.querySelector(`.tree-item[data-path="${CSS.escape(path)}"]`) : null;
    const ops = kind === "dir" ? DIR_OPS : FILE_OPS;
    // 与编辑器右键菜单同构：分组小标题 + 图标瓦片 + 标题；无快捷键不渲染芯片
    treeMenu.innerHTML =
      `<div class="slash-group-label">${kind === "dir" ? "文件夹" : "文件"}</div>` +
      ops.map(
        (op) => `
          <button type="button" role="menuitem" class="ctx-item${op.danger ? " danger" : ""}" data-act="${op.act}">
            <span class="ci-icon">${window.ICONS[op.icon]}</span><span class="ci-title">${op.label}</span>
          </button>`
      ).join("");
    treeMenu.classList.add("open");
    treeMenu.setAttribute("role", "menu");
    const mw = treeMenu.offsetWidth || 190;
    const mh = treeMenu.offsetHeight || 170;
    if (x + mw > window.innerWidth - 8) x = window.innerWidth - mw - 8;
    if (y + mh > window.innerHeight - 8) y = window.innerHeight - mh - 8;
    treeMenu.style.left = Math.max(8, x) + "px";
    treeMenu.style.top = Math.max(8, y) + "px";
    if (focusFirst) treeMenu.querySelector("[role=menuitem]")?.focus();
  }

  function closeMenu() {
    treeMenu.classList.remove("open");
    menuPath = null;
    if (menuTrigger && menuTrigger.isConnected) menuTrigger.focus();
    menuTrigger = null;
  }

  treeMenu.addEventListener("click", (e) => {
    const btn = e.target.closest(".ctx-item");
    if (!btn || !menuPath) return;
    const path = menuPath;
    closeMenu();
    const handler = {
      rename: onRenameFile, move: onMoveFile,
      reveal: onRevealFile, delete: onDeleteFile,
      "new-file": onNewFile, "new-folder": onNewFolder,
    }[btn.dataset.act];
    if (handler) handler(path);
  });

  treeMenu.addEventListener("keydown", (e) => {
    const items = Array.from(treeMenu.querySelectorAll('[role="menuitem"]'));
    const index = items.indexOf(document.activeElement);
    if (e.key === "Escape") {
      e.preventDefault();
      closeMenu();
    } else if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      const dir = e.key === "ArrowDown" ? 1 : -1;
      items[(index + dir + items.length) % items.length]?.focus();
    } else if (e.key === "Home") {
      e.preventDefault();
      items[0]?.focus();
    } else if (e.key === "End") {
      e.preventDefault();
      items[items.length - 1]?.focus();
    }
  });

  document.addEventListener("click", (e) => {
    if (menuPath && !treeMenu.contains(e.target)) closeMenu();
  });
  // 捕获阶段拦截 Esc：菜单打开时不让全局 Esc（如关闭首页）抢先响应
  document.addEventListener("keydown", (e) => {
    if (menuPath && e.key === "Escape") {
      e.preventDefault();
      e.stopPropagation();
      closeMenu();
    }
  }, true);
  window.addEventListener("resize", () => { if (menuPath) closeMenu(); });

  function markActive(path) {
    activePath = path;
    fileTreeEl.querySelectorAll(".tree-item.file").forEach((el) => {
      const active = el.dataset.path === path;
      el.classList.toggle("active", active);
      if (active) el.setAttribute("aria-current", "page");
      else el.removeAttribute("aria-current");
    });
  }

  function rebindEmpty() {
    const btn = document.getElementById("empty-open-folder");
    if (btn) btn.addEventListener("click", () => window.App && window.App.openFolder());
    const nf = document.getElementById("empty-new-file");
    if (nf) nf.addEventListener("click", () => window.App && window.App.newNoteInFolder && window.App.newNoteInFolder());
  }

  // —— 大纲 ——
  let outlineKey = null; // 当前位置标题（ir: data-ryuu-id；sv: "L<行号>"）

  function renderOutline(headings) {
    if (!headings || !headings.length) {
      outlineEmpty.style.display = "block";
      outlineList.innerHTML = "";
      outlineKey = null;
      return;
    }
    outlineEmpty.style.display = "none";
    outlineList.innerHTML = headings
      .map(
        (h) =>
          `<button type="button" class="outline-item" data-level="${h.level}" data-id="${esc(h.id)}"${h.line != null ? ` data-line="${h.line}"` : ""} title="${esc(h.text)}" aria-label="跳转到标题：${esc(h.text)}">${esc(h.text)}</button>`
      )
      .join("");
    applyOutlineActive();
  }

  /* 滚动跟随的当前标题：重渲染后也要恢复标记 */
  function applyOutlineActive() {
    outlineList.querySelectorAll(".outline-item").forEach((el) => {
      const k = el.dataset.id || (el.dataset.line != null ? "L" + el.dataset.line : "");
      el.classList.toggle("active", !!k && k === outlineKey);
    });
  }

  function markOutlineActive(key) {
    if (key === outlineKey) return;
    outlineKey = key;
    applyOutlineActive();
  }

  // 大纲点击（委托，仅绑定一次）：大纲会随编辑频繁重建，不逐项绑监听
  outlineList.addEventListener("click", (e) => {
    const item = e.target.closest(".outline-item");
    if (item && onJumpHeading) onJumpHeading(item.dataset.id);
  });

  // —— 最近打开 ——
  function renderRecent(items) {
    if (!items || !items.length) {
      recentEmpty.style.display = "block";
      recentList.innerHTML = "";
      recentFoot.style.display = "none";
      return;
    }
    recentEmpty.style.display = "none";
    recentFoot.style.display = "block";
    recentList.innerHTML = items
      .map(
        (it) => `
          <button type="button" class="tree-item recent${it.exists ? "" : " missing"}" data-path="${esc(it.path)}" data-kind="${esc(it.kind)}" title="${esc(it.path)}" aria-label="打开最近${it.kind === "folder" ? "文件夹" : "文件"}：${esc(it.name)}">
            <span class="tw-icon">${it.kind === "folder" ? window.ICONS.folder : window.ICONS.fileText}</span>
            <span class="tw-name">${esc(it.name)}</span>
          </button>`
      )
      .join("");
  }

  // 最近列表点击（委托，仅绑定一次）
  recentList.addEventListener("click", (e) => {
    const item = e.target.closest(".recent");
    if (item && onOpenRecent) onOpenRecent(item.dataset.path, item.dataset.kind);
  });

  function renderLinks(data) {
    const back = (data && data.back) || [];
    const out = (data && data.out) || [];
    if (!back.length && !out.length) {
      linksEmpty.style.display = "block";
      linksEmpty.textContent = data && data.message
        ? data.message
        : "打开一篇笔记后，这里显示指向它的双向链接";
      linksList.innerHTML = "";
      return;
    }
    linksEmpty.style.display = "none";
    let html = "";
    if (back.length) {
      html += `<div class="links-group">反向链接 · ${back.length}</div>`;
      back.forEach((it) => {
        html += `<button type="button" class="tree-item file link-item" data-path="${esc(it.path)}" title="${esc(it.path)}" aria-label="打开反向链接：${esc(it.name)}">
          <span class="tw-icon">${window.ICONS.fileText}</span>
          <span class="tw-name">${esc(it.name)}</span>
        </button>`;
      });
    }
    if (out.length) {
      html += `<div class="links-group">本文链出 · ${out.length}</div>`;
      out.forEach((it) => {
        html += `<button type="button" class="tree-item file link-item${it.exists === false ? " missing" : ""}" data-wiki="${esc(it.wiki || "")}" data-path="${esc(it.path || "")}" title="${esc(it.rel || it.wiki || "")}" aria-label="打开链出链接：${esc(it.name || it.wiki)}">
          <span class="tw-icon">${window.ICONS.wiki || window.ICONS.link}</span>
          <span class="tw-name">${esc(it.name || it.wiki)}</span>
        </button>`;
      });
    }
    linksList.innerHTML = html;
  }

  linksList.addEventListener("click", (e) => {
    const item = e.target.closest(".link-item");
    if (!item) return;
    if (item.dataset.path && onOpenFile) onOpenFile(item.dataset.path);
    else if (item.dataset.wiki && window.App && window.App.openWikilink) window.App.openWikilink(item.dataset.wiki);
  });

  function esc(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  rebindEmpty();
  function revealPath(path) {
    if (!path) return false;
    markActive(path);
    let file = null;
    fileTreeEl.querySelectorAll(".tree-item.file").forEach((el) => {
      if (el.dataset.path === path) file = el;
    });
    if (!file) return false;
    let node = file.parentElement;
    while (node && node !== fileTreeEl) {
      if (node.classList && node.classList.contains("tree-dir")) {
        const dirItem = node.querySelector(":scope > .tree-item.dir");
        const children = node.querySelector(":scope > .tree-children");
        if (dirItem && dirItem.classList.contains("collapsed")) {
          dirItem.classList.remove("collapsed");
          dirItem.setAttribute("aria-expanded", "true");
          if (children) children.style.display = "block";
        }
      }
      node = node.parentElement;
    }
    file.scrollIntoView({ block: "nearest" });
    return true;
  }

  function foldAll(collapsed) {
    const hide = !!collapsed;
    fileTreeEl.querySelectorAll(".tree-item.dir").forEach((item) => {
      item.classList.toggle("collapsed", hide);
      item.setAttribute("aria-expanded", String(!hide));
      const parent = item.parentElement;
      const children = parent ? parent.querySelector(":scope > .tree-children") : null;
      if (children) children.style.display = hide ? "none" : "block";
    });
  }

  window.Sidebar = {
    setHandlers, renderTree, markActive, revealPath,
    renderOutline, renderRecent, renderLinks, foldAll, markOutlineActive,
  };
})();
