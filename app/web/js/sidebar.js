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

  let onOpenFile = null;  // (path) => void
  let onJumpHeading = null; // (id) => void
  let onOpenRecent = null;  // (path, kind) => void
  // 文件管理操作（真实磁盘文件），由 app.js 注入
  let onRenameFile = null;
  let onMoveFile = null;
  let onRevealFile = null;
  let onDeleteFile = null;
  let activePath = null;

  function setHandlers({ openFile, jumpHeading, openRecent, renameFile, moveFile, revealFile, deleteFile }) {
    onOpenFile = openFile;
    onJumpHeading = jumpHeading;
    onOpenRecent = openRecent;
    onRenameFile = renameFile;
    onMoveFile = moveFile;
    onRevealFile = revealFile;
    onDeleteFile = deleteFile;
  }

  // —— 文件树 ——
  function renderTree(tree, rootName) {
    if (!tree || !tree.length) {
      filesEmpty.style.display = "block";
      filesEmpty.innerHTML = rootName
        ? `「${rootName}」中没有 Markdown 文件`
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
              <div class="tree-item dir" data-path="${esc(n.path)}">
                <span class="caret">${window.ICONS.chevron}</span>
                <span class="tw-icon">${window.ICONS.folder}</span>
                <span class="tw-name">${esc(n.name)}</span>
              </div>
              <div class="tree-children">${buildNodes(n.children || [])}</div>
            </div>`;
        }
        return `
          <div class="tree-item file" data-path="${esc(n.path)}" title="${esc(n.path)}">
            <span class="tw-icon">${window.ICONS.fileText}</span>
            <span class="tw-name">${esc(n.name)}</span>
          </div>`;
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
    const children = item.parentElement.querySelector(".tree-children");
    if (children) children.style.display = item.classList.contains("collapsed") ? "none" : "block";
  });

  // —— 文件右键菜单（重命名 / 移动 / 删除真实磁盘文件） ——
  // 仅文件行弹出；目录行保持「点击折叠」单一语义，不挂管理操作
  const FILE_OPS = [
    { act: "rename", label: "重命名", icon: "edit" },
    { act: "move", label: "移动到…", icon: "move" },
    { act: "reveal", label: "在资源管理器中显示", icon: "folderOpen" },
    { act: "delete", label: "删除（移入回收站）", icon: "trash", danger: true },
  ];
  let menuPath = null;

  fileTreeEl.addEventListener("contextmenu", (e) => {
    const item = e.target.closest(".tree-item.file");
    if (!item) return;
    e.preventDefault();
    openMenu(item.dataset.path, e.clientX, e.clientY);
  });

  function openMenu(path, x, y) {
    menuPath = path;
    treeMenu.innerHTML = FILE_OPS.map(
      (op) => `
        <div class="ctx-item${op.danger ? " danger" : ""}" data-act="${op.act}">
          <span class="ci-icon">${window.ICONS[op.icon]}</span><span>${op.label}</span>
        </div>`
    ).join("");
    treeMenu.classList.add("open");
    // 视口边缘钳制（与 slash.js 同一定位思路）
    const mw = treeMenu.offsetWidth || 190;
    const mh = treeMenu.offsetHeight || 170;
    if (x + mw > window.innerWidth - 8) x = window.innerWidth - mw - 8;
    if (y + mh > window.innerHeight - 8) y = window.innerHeight - mh - 8;
    treeMenu.style.left = Math.max(8, x) + "px";
    treeMenu.style.top = Math.max(8, y) + "px";
  }

  function closeMenu() {
    treeMenu.classList.remove("open");
    menuPath = null;
  }

  treeMenu.addEventListener("click", (e) => {
    const btn = e.target.closest(".ctx-item");
    if (!btn || !menuPath) return;
    const path = menuPath;
    closeMenu();
    const handler = {
      rename: onRenameFile, move: onMoveFile,
      reveal: onRevealFile, delete: onDeleteFile,
    }[btn.dataset.act];
    if (handler) handler(path);
  });

  document.addEventListener("click", (e) => {
    if (menuPath && !treeMenu.contains(e.target)) closeMenu();
  });
  // 捕获阶段拦截 Esc：菜单打开时不让全局 Esc（如关闭首页）抢先响应
  document.addEventListener("keydown", (e) => {
    if (menuPath && e.key === "Escape") { e.stopPropagation(); closeMenu(); }
  }, true);
  window.addEventListener("resize", () => { if (menuPath) closeMenu(); });

  function markActive(path) {
    activePath = path;
    fileTreeEl.querySelectorAll(".tree-item.file").forEach((el) => {
      el.classList.toggle("active", el.dataset.path === path);
    });
  }

  function rebindEmpty() {
    const btn = document.getElementById("empty-open-folder");
    if (btn) btn.addEventListener("click", () => window.App && window.App.openFolder());
  }

  // —— 大纲 ——
  function renderOutline(headings) {
    if (!headings || !headings.length) {
      outlineEmpty.style.display = "block";
      outlineList.innerHTML = "";
      return;
    }
    outlineEmpty.style.display = "none";
    outlineList.innerHTML = headings
      .map(
        (h) =>
          `<div class="outline-item" data-level="${h.level}" data-id="${esc(h.id)}" title="${esc(h.text)}">${esc(h.text)}</div>`
      )
      .join("");
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
          <div class="tree-item recent${it.exists ? "" : " missing"}" data-path="${esc(it.path)}" data-kind="${esc(it.kind)}" title="${esc(it.path)}">
            <span class="tw-icon">${it.kind === "folder" ? window.ICONS.folder : window.ICONS.fileText}</span>
            <span class="tw-name">${esc(it.name)}</span>
          </div>`
      )
      .join("");
  }

  // 最近列表点击（委托，仅绑定一次）
  recentList.addEventListener("click", (e) => {
    const item = e.target.closest(".recent");
    if (item && onOpenRecent) onOpenRecent(item.dataset.path, item.dataset.kind);
  });

  function esc(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  rebindEmpty();
  window.Sidebar = { setHandlers, renderTree, markActive, renderOutline, renderRecent };
})();
