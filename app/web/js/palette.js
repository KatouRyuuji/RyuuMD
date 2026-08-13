/* 命令面板（Ctrl+P 快速打开 / Ctrl+Shift+P 命令 / Ctrl+Shift+F 仓库搜索）
   与本文查找条（Ctrl+F）。对标 Obsidian / VS Code，三模式共用一块浮层。 */
(function () {
  const mask = document.getElementById("palette-mask");
  const input = document.getElementById("pal-input");
  const listEl = document.getElementById("pal-list");
  const emptyEl = document.getElementById("pal-empty");
  const hintEl = document.getElementById("pal-hint");
  const iconEl = document.getElementById("pal-icon");

  const HINTS = {
    file: "快速打开笔记",
    command: "运行命令",
    search: "在仓库中搜索",
  };

  let mode = "file";
  let items = [];
  let activeIdx = 0;
  let seq = 0;
  let handlers = {};
  let debounceTimer = null;

  function isOpen() {
    return mask && mask.classList.contains("open");
  }

  function init(h) {
    handlers = h || {};
  }

  function open(nextMode) {
    mode = nextMode === "command" || nextMode === "search" ? nextMode : "file";
    mask.classList.add("open");
    hintEl.textContent = HINTS[mode];
    iconEl.setAttribute("data-kind", mode);
    input.value = "";
    input.placeholder = HINTS[mode] + "…";
    input.focus();
    renderLoading();
    refresh();
  }

  function close() {
    mask.classList.remove("open");
    items = [];
    input.value = "";
  }

  function renderLoading() {
    emptyEl.style.display = "none";
    listEl.innerHTML = '<div class="pal-empty-inline">正在搜索…</div>';
  }

  function esc(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function highlight() {
    listEl.querySelectorAll(".pal-item").forEach((el) => {
      el.classList.toggle("active", parseInt(el.dataset.idx, 10) === activeIdx);
    });
    const cur = listEl.querySelector(".pal-item.active");
    if (cur) cur.scrollIntoView({ block: "nearest" });
  }

  function render() {
    if (!items.length) {
      listEl.innerHTML = "";
      emptyEl.style.display = "";
      emptyEl.textContent = mode === "file"
        ? "没有匹配的笔记（请先打开仓库）"
        : (mode === "search" ? "没有匹配的内容" : "没有匹配的命令");
      return;
    }
    emptyEl.style.display = "none";
    let html = "";
    items.forEach((it, i) => {
      const sub = it.sub || it.rel || it.keys || "";
      html += `<div class="pal-item${i === activeIdx ? " active" : ""}" data-idx="${i}">
        <div class="pal-title">${esc(it.title)}</div>
        ${sub ? `<div class="pal-sub">${esc(sub)}</div>` : ""}
        ${it.badge ? `<span class="pal-badge">${esc(it.badge)}</span>` : ""}
      </div>`;
    });
    listEl.innerHTML = html;
    listEl.querySelectorAll(".pal-item").forEach((el) => {
      el.addEventListener("mousedown", (e) => {
        e.preventDefault();
        activeIdx = parseInt(el.dataset.idx, 10);
        choose();
      });
      el.addEventListener("mousemove", () => {
        const i = parseInt(el.dataset.idx, 10);
        if (i !== activeIdx) { activeIdx = i; highlight(); }
      });
    });
  }

  async function refresh() {
    const q = input.value;
    const my = ++seq;
    if (mode === "command") {
      const src = (handlers.commands && handlers.commands()) || [];
      const ql = q.trim().toLowerCase();
      items = src.filter((c) => {
        if (!ql) return true;
        const hay = ((c.title || "") + " " + (c.keys || "") + " " + (c.id || "") + " " + (c.group || "")).toLowerCase();
        return hay.indexOf(ql) >= 0;
      }).map((c) => ({
        kind: "command",
        id: c.id,
        title: c.title,
        keys: c.keys,
        sub: c.group || "",
        badge: c.keys || "",
        run: c.run,
      }));
      activeIdx = 0;
      if (my === seq) render();
      return;
    }
    if (mode === "file") {
      if (!handlers.listFiles) { items = []; render(); return; }
      const res = await handlers.listFiles(q);
      if (my !== seq) return;
      const arr = (res && res.items) || [];
      items = arr.map((f) => ({
        kind: "file",
        title: f.name,
        sub: f.rel,
        path: f.path,
        badge: "",
      }));
      activeIdx = 0;
      render();
      return;
    }
    if (!q.trim()) {
      items = [];
      emptyEl.style.display = "";
      emptyEl.textContent = "输入关键词，搜索当前仓库";
      listEl.innerHTML = "";
      return;
    }
    if (!handlers.search) { items = []; render(); return; }
    const res = await handlers.search(q);
    if (my !== seq) return;
    const hits = (res && res.hits) || [];
    items = hits.map((h) => ({
      kind: "hit",
      title: h.name,
      sub: (h.kind === "name" ? h.rel : ((h.line ? h.line + ": " : "") + (h.snippet || h.rel))),
      path: h.path,
      badge: h.kind === "name" ? "文件名" : "正文",
    }));
    activeIdx = 0;
    render();
  }

  function choose() {
    const it = items[activeIdx];
    if (!it) return;
    close();
    if (it.kind === "command" && it.run) it.run();
    else if (it.kind === "file" && handlers.onPickFile) handlers.onPickFile(it);
    else if (it.kind === "hit" && handlers.onPickHit) handlers.onPickHit(it);
  }

  function onKey(e) {
    if (!isOpen()) return;
    if (e.key === "Escape") {
      e.preventDefault();
      e.stopPropagation();
      close();
      return;
    }
    if (e.key === "ArrowDown") {
      e.preventDefault();
      e.stopPropagation();
      if (items.length) { activeIdx = (activeIdx + 1) % items.length; highlight(); }
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      e.stopPropagation();
      if (items.length) { activeIdx = (activeIdx - 1 + items.length) % items.length; highlight(); }
    } else if (e.key === "Enter") {
      e.preventDefault();
      e.stopPropagation();
      choose();
    }
  }

  input.addEventListener("input", () => {
    clearTimeout(debounceTimer);
    debounceTimer = setTimeout(refresh, mode === "command" ? 0 : 120);
  });
  input.addEventListener("keydown", onKey);
  document.addEventListener("keydown", (e) => {
    if (isOpen()) onKey(e);
  }, true);
  mask.addEventListener("mousedown", (e) => {
    if (e.target === mask) close();
  });

  window.Palette = { init, open, close, isOpen, openFiles: () => open("file"),
    openCommands: () => open("command"), openSearch: () => open("search") };
})();

/* 本文查找条：Ctrl+F 打开，Enter 下一个，Shift+Enter 上一个 */
(function () {
  const bar = document.getElementById("find-bar");
  const input = document.getElementById("find-input");
  const countEl = document.getElementById("find-count");

  function isOpen() {
    return bar && bar.classList.contains("open");
  }

  function open() {
    bar.classList.add("open");
    input.focus();
    input.select();
    updateCount();
  }

  function close() {
    bar.classList.remove("open");
    input.value = "";
    countEl.textContent = "";
  }

  function updateCount() {
    const q = input.value;
    if (!q) { countEl.textContent = ""; return; }
    const src = window.Editor && window.Editor.getValue ? window.Editor.getValue() : "";
    if (!src) { countEl.textContent = "0"; return; }
    const ql = q.toLowerCase();
    const hay = src.toLowerCase();
    let n = 0, from = 0;
    while (ql && hay.indexOf(ql, from) >= 0) {
      n++;
      from = hay.indexOf(ql, from) + ql.length;
      if (n > 999) break;
    }
    countEl.textContent = n > 999 ? "999+" : String(n);
  }

  function find(backward) {
    const q = input.value;
    if (!q) return;
    try {
      window.find(q, false, !!backward, true, false, true, false);
    } catch (e) { /* 部分 WebView 无 window.find */ }
    updateCount();
  }

  document.getElementById("find-next").addEventListener("click", () => find(false));
  document.getElementById("find-prev").addEventListener("click", () => find(true));
  document.getElementById("find-close").addEventListener("click", close);
  input.addEventListener("input", updateCount);
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter") {
      e.preventDefault();
      find(e.shiftKey);
    } else if (e.key === "Escape") {
      e.preventDefault();
      e.stopPropagation();
      close();
    }
  });

  window.FindBar = { open, close, isOpen };
})();
