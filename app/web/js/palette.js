/* 命令面板（Ctrl+P 快速打开 / Ctrl+Shift+P 命令 / Ctrl+Shift+F 仓库搜索）
   与本文查找条（Ctrl+F）。对标 Obsidian / VS Code，三模式共用一块浮层。 */
(function () {
  const mask = document.getElementById("palette-mask");
  const input = document.getElementById("pal-input");
  const listEl = document.getElementById("pal-list");
  const emptyEl = document.getElementById("pal-empty");
  const hintEl = document.getElementById("pal-hint");
  const iconEl = document.getElementById("pal-icon");
  const modesEl = document.getElementById("pal-modes");
  const answerEl = document.getElementById("pal-answer");
  const palBox = document.getElementById("palette");
  const footEl = document.getElementById("pal-foot");
  const FOOT_PICK = "↑↓ 选择 · Enter 打开 · Esc 关闭";
  const FOOT_ENTER_SEARCH = "Enter 搜索 · Esc 关闭";
  const FOOT_ENTER_ASK = "Enter 提问 · Esc 关闭";

  const HINTS = {
    file: "快速打开笔记",
    command: "运行命令",
    search: "在仓库中搜索",
    tasks: "仓库未完成待办",
    tags: "浏览标签",
    broken: "断开的双链",
    orphans: "孤立笔记",
    mentions: "未链接提及",
  };

  const INDEX_MODES = { tasks: 1, tags: 1, broken: 1, orphans: 1, mentions: 1 };

  let mode = "file";
  let searchMode = "title";
  let items = [];
  let activeIdx = 0;
  let seq = 0;
  let handlers = {};
  let debounceTimer = null;
  let askChip = false; // ask 前缀已 chip 化，输入框里只剩问题本体

  const chipEl = document.getElementById("pal-ask-chip");

  /* 查询原文：chip 态下补回 ask 前缀，其余消费方无需关心 chip */
  function rawQuery() {
    return askChip ? "ask " + input.value : input.value;
  }

  /* 「ask 」前缀 → chip：输入框只留问题本体；空输入按 Backspace 弹出还原 */
  function syncAskChip(e) {
    if (!askChip && /^ask\s+/i.test(input.value)) {
      askChip = true;
      input.value = input.value.replace(/^ask\s+/i, "");
    } else if (askChip && e && e.type === "keydown" && e.key === "Backspace" && input.value === "") {
      askChip = false;
      input.value = "ask ";
      e.preventDefault();
    }
    if (chipEl) chipEl.hidden = !askChip;
  }

  function isAskQuery(q) {
    return /^ask\s+\S/i.test(String(q || "").trim());
  }

  // ask / 语义只在 Enter 时打模型；标题与内容仍即时搜
  function searchFiresOnInput(q, mode) {
    if (isAskQuery(q)) return false;
    if ((mode || searchMode) === "semantic") return false;
    return true;
  }

  function syncSearchChrome() {
    const searching = mode === "search";
    if (modesEl) modesEl.hidden = !searching;
    if (searching && modesEl) {
      modesEl.querySelectorAll(".pal-mode").forEach((btn) => {
        const on = btn.dataset.mode === searchMode;
        btn.classList.toggle("active", on);
        btn.setAttribute("aria-pressed", on ? "true" : "false");
      });
    }
    const asking = searching && isAskQuery(rawQuery());
    const enterOnly = searching && !searchFiresOnInput(rawQuery(), searchMode);
    if (palBox) {
      palBox.classList.toggle("is-ask", asking);
      palBox.classList.toggle("is-enter-search", enterOnly);
    }
    // ask 态由 #pal-ask-chip 一个指示承担，图标不再切换成 "A"（双指示冗余）
    if (iconEl && searching) iconEl.setAttribute("data-kind", "search");
    if (hintEl && searching) {
      hintEl.textContent = asking ? "Enter 提问" : (searchMode === "semantic" ? "Enter 搜索" : "即时搜索");
    }
    if (searching) {
      input.placeholder = asking
        ? "输入问题后按 Enter 提问…"
        : (searchMode === "semantic"
          ? "输入问题后按 Enter 进行语义搜索…"
          : "搜索笔记，或输入 ask 加空格后按 Enter 提问…");
    }
    if (footEl) {
      if (asking) footEl.textContent = FOOT_ENTER_ASK;
      else if (enterOnly) footEl.textContent = FOOT_ENTER_SEARCH;
      else footEl.textContent = FOOT_PICK;
    }
  }

  function isOpen() {
    return mask && mask.classList.contains("open");
  }

  function init(h) {
    handlers = h || {};
  }

  function open(nextMode) {
    mode = HINTS[nextMode] ? nextMode : "file";
    if (mode === "search") searchMode = "title";
    mask.classList.add("open");
    hintEl.textContent = HINTS[mode] || "";
    iconEl.setAttribute("data-kind", mode === "command" || mode === "search" || INDEX_MODES[mode] ? (INDEX_MODES[mode] ? "search" : mode) : "file");
    input.value = "";
    askChip = false;
    if (chipEl) chipEl.hidden = true;
    input.placeholder = HINTS[mode] + "…";
    if (answerEl) { answerEl.hidden = true; answerEl.innerHTML = ""; }
    syncSearchChrome();
    input.focus();
    renderLoading();
    refresh();
  }

  function close() {
    mask.classList.remove("open");
    items = [];
    input.value = "";
    askChip = false;
    if (chipEl) chipEl.hidden = true;
    if (answerEl) { answerEl.hidden = true; answerEl.innerHTML = ""; }
    if (palBox) {
      palBox.classList.remove("is-ask");
      palBox.classList.remove("is-enter-search");
    }
    if (modesEl) modesEl.hidden = true;
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

  function displayRel(it) {
    const title = it.title || "";
    const raw = String(it.rel || it.sub || it.path || "");
    if (!raw) return "";
    const norm = raw.replace(/\\/g, "/");
    const looksAbs = /^[A-Za-z]:\//.test(norm) || norm.charAt(0) === "/";
    if (!looksAbs && norm !== title) return norm;
    const folder = handlers.getFolder ? String(handlers.getFolder() || "") : "";
    if (folder) {
      const f = folder.replace(/\\/g, "/").replace(/\/+$/, "");
      const p = String(it.path || raw).replace(/\\/g, "/");
      if (f && p.toLowerCase().indexOf(f.toLowerCase()) === 0) {
        const rel = p.slice(f.length).replace(/^\/+/, "");
        // 根目录文件的相对路径与标题同名：副行不重复显示
        return rel === title ? "" : rel;
      }
    }
    if (looksAbs) {
      const parts = norm.split("/");
      const leaf = parts[parts.length - 1] || "";
      return leaf === title ? "" : leaf;
    }
    return norm === title ? "" : norm;
  }

  function highlight() {
    listEl.querySelectorAll(".pal-item").forEach((el) => {
      el.classList.toggle("active", parseInt(el.dataset.idx, 10) === activeIdx);
    });
    const cur = listEl.querySelector(".pal-item.active");
    if (cur) cur.scrollIntoView({ block: "nearest" });
  }

  /* 标题中的首个命中片段加粗染色；无命中（如按路径/分组匹配）原样输出 */
  function markTitle(title) {
    const q = askChip ? "" : input.value.trim();
    if (!q) return esc(title);
    const i = String(title).toLowerCase().indexOf(q.toLowerCase());
    if (i < 0) return esc(title);
    const t = String(title);
    return esc(t.slice(0, i)) + '<b class="pal-hit">' + esc(t.slice(i, i + q.length)) + "</b>" + esc(t.slice(i + q.length));
  }

  function render() {
    if (!items.length) {
      listEl.innerHTML = "";
      emptyEl.style.display = "";
      emptyEl.textContent = mode === "file"
        ? "没有匹配的笔记（请先打开仓库）"
        : (mode === "search" ? "没有匹配的内容"
          : (INDEX_MODES[mode] ? "没有匹配的条目" : "没有匹配的命令"));
      return;
    }
    emptyEl.style.display = "none";
    let html = "";
    items.forEach((it, i) => {
      // displayRel 返回 "" 表示副行与标题重复，不再回落 it.sub 把重复内容显示出来
      const sub = (it.kind === "file" || it.kind === "create" || it.kind === "hit" || it.kind === "orphan")
        ? displayRel(it)
        : (it.sub || it.rel || it.keys || "");
      html += `<div class="pal-item${i === activeIdx ? " active" : ""}" data-idx="${i}">
        <div class="pal-title">${markTitle(it.title)}</div>
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
      /* 同 slash 菜单：悬停由 CSS :hover 淡色承担，不写 activeIdx */
    });
  }

  async function refresh() {
    const q = rawQuery();
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
      const qtrim = (q || "").trim();
      // 文件索引与最近记录并行：两次桥往返串行会把面板首开拖慢一倍
      const wantRecent = !qtrim && handlers.recentFiles;
      const [res, rec] = await Promise.all([
        handlers.listFiles(q),
        wantRecent ? handlers.recentFiles().catch(() => []) : Promise.resolve(null),
      ]);
      if (my !== seq) return;
      const arr = (res && res.items) || [];
      items = arr.map((f) => ({
        kind: "file",
        title: f.name,
        sub: f.rel,
        path: f.path,
        badge: "",
      }));
      if (wantRecent) {
        const recentItems = (rec || [])
          .filter((r) => r.kind !== "folder" && r.exists !== false)
          .slice(0, 8)
          .map((r) => ({
            kind: "file",
            title: r.name,
            sub: r.rel || "",
            path: r.path,
            badge: "最近",
          }));
        if (recentItems.length) {
          const seen = {};
          recentItems.forEach((it) => { seen[it.path] = true; });
          items = recentItems.concat(items.filter((it) => !seen[it.path]));
        }
      }
      if (qtrim) {
        const ql = qtrim.toLowerCase();
        const exact = items.some((it) => {
          const n = (it.title || "").toLowerCase();
          return n === ql || n === ql + ".md" || (it.title && it.title.replace(/\.md$/i, "").toLowerCase() === ql);
        });
        if (!exact) {
          items.unshift({
            kind: "create",
            title: "新建「" + qtrim + ".md」",
            sub: "在当前仓库根目录创建",
            badge: "新建",
            name: qtrim,
          });
        }
      }
      activeIdx = 0;
      render();
      return;
    }
    if (INDEX_MODES[mode]) {
      if (!handlers.vaultIndex) { items = []; render(); return; }
      const res = await handlers.vaultIndex(mode, q);
      if (my !== seq) return;
      const arr = (res && res.items) || [];
      items = arr.map((h) => ({
        kind: h.kind || "hit",
        title: h.title || h.name,
        sub: h.sub || ((h.line ? h.line + ": " : "") + (h.snippet || h.rel || "")),
        path: h.path,
        line: h.line,
        snippet: h.snippet,
        tag: h.tag,
        wiki: h.wiki,
        badge: h.badge || "",
      }));
      activeIdx = 0;
      render();
      return;
    }
    if (!q.trim()) {
      items = [];
      emptyEl.style.display = "";
      emptyEl.textContent = "输入关键词，或 ask 加空格后按 Enter 提问";
      listEl.innerHTML = "";
      if (answerEl) { answerEl.hidden = true; answerEl.innerHTML = ""; }
      syncSearchChrome();
      return;
    }
    if (isAskQuery(q)) {
      renderAskHint(q);
      return;
    }
    if (answerEl) { answerEl.hidden = true; answerEl.innerHTML = ""; }
    if (!handlers.search) { items = []; render(); return; }
    let res;
    try {
      res = await handlers.search(q, searchMode);
    } catch (e) {
      if (my !== seq) return;
      renderSearchError((e && e.message) || "搜索失败");
      return;
    }
    if (my !== seq) return;
    if (res && res.kind === "ask") {
      renderAskResult(res);
      return;
    }
    if (res && res.ok === false && res.error) {
      renderSearchError(res.error);
      return;
    }
    const hits = (res && res.hits) || [];
    const badgeOf = (k) => {
      if (k === "title" || k === "name") return "标题";
      if (k === "semantic") return "语义";
      return "内容";
    };
    items = hits.map((h) => ({
      kind: "hit",
      hitKind: h.kind,
      title: h.name,
      sub: (h.kind === "title" || h.kind === "name" ? h.rel : ((h.line ? h.line + ": " : "") + (h.snippet || h.rel))),
      path: h.path,
      line: h.line,
      snippet: h.snippet,
      badge: badgeOf(h.kind),
    }));
    activeIdx = 0;
    render();
  }

  function renderAskHint(q) {
    items = [];
    listEl.innerHTML = "";
    emptyEl.style.display = "";
    emptyEl.textContent = "按 Enter 提问，将打开 AI 侧栏";
    syncSearchChrome();
    if (answerEl) { answerEl.hidden = true; answerEl.innerHTML = ""; }
  }

  function renderAskDraft(q) {
    renderAskHint(q);
  }

  function renderSearchError(msg) {
    items = [];
    listEl.innerHTML = "";
    syncSearchChrome();
    if (answerEl) { answerEl.hidden = true; answerEl.innerHTML = ""; }
    emptyEl.style.display = "";
    emptyEl.textContent = msg || "搜索失败";
  }

  function renderSemanticHint(q) {
    items = [];
    listEl.innerHTML = "";
    syncSearchChrome();
    if (answerEl) { answerEl.hidden = true; answerEl.innerHTML = ""; }
    emptyEl.style.display = "";
    emptyEl.textContent = (q || "").trim()
      ? "按 Enter 进行语义搜索（将调用已配置的模型）"
      : "输入问题后按 Enter 进行语义搜索";
  }

  let searchInflight = false;

  async function runPaidSearch() {
    if (searchInflight) return;
    searchInflight = true;
    try {
      await refresh();
    } finally {
      searchInflight = false;
    }
  }

  function renderAskPending(q) {
    items = [];
    listEl.innerHTML = "";
    emptyEl.style.display = "none";
    syncSearchChrome();
    if (!answerEl) return;
    answerEl.hidden = false;
    const parsed = String(q || "").trim().replace(/^ask\s+/i, "");
    answerEl.innerHTML = '<div class="pal-ask-label">AI 回答</div>'
      + '<div class="pal-ask-q">' + esc(parsed) + '</div>'
      + '<div class="pal-ask-body pal-ask-wait">正在询问…</div>';
  }

  function renderAskResult(res) {
    items = [];
    listEl.innerHTML = "";
    emptyEl.style.display = "none";
    syncSearchChrome();
    if (!answerEl) return;
    answerEl.hidden = false;
    const qtext = (res && res.question) || String(rawQuery() || "").trim().replace(/^ask\s+/i, "");
    const err = res && !res.ok ? (res.error || "提问失败") : "";
    const answer = (res && (res.answer || res.text)) || "";
    const body = err
      ? '<div class="pal-ask-body pal-ask-error">' + esc(err) + "</div>"
      : '<div class="pal-ask-body">' + ((window.AiText && window.AiText.markdown)
        ? window.AiText.markdown(answer || "（无内容）")
        : esc(answer || "（无内容）")) + "</div>";
    const note = res && res.truncated
      ? '<div class="pal-ask-note">仅根据仓库前若干篇摘录，结果可能不完整。</div>'
      : "";
    answerEl.innerHTML = '<div class="pal-ask-label">AI 回答</div>'
      + '<div class="pal-ask-q">' + esc(qtext) + "</div>"
      + body + note;
  }

  function choose() {
    const it = items[activeIdx];
    if (!it) return;
    if (it.kind === "tag-group" && it.tag) {
      input.value = it.tag;
      refresh();
      return;
    }
    close();
    if (it.kind === "command" && it.run) it.run();
    else if (it.kind === "create" && handlers.onCreate) handlers.onCreate(it.name);
    else if ((it.kind === "file" || it.kind === "orphan") && handlers.onPickFile) handlers.onPickFile(it);
    else if ((it.kind === "hit" || it.kind === "task" || it.kind === "tag" || it.kind === "broken" || it.kind === "mention") && handlers.onPickHit) {
      handlers.onPickHit(it);
    }
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
      if (mode === "search" && isAskQuery(rawQuery())) {
        const q = String(rawQuery() || "").trim().replace(/^ask\s+/i, "");
        close();
        if (window.AiPanel) {
          window.AiPanel.open();
          window.AiPanel.ask(q);
        }
        return;
      }
      if (mode === "search" && searchMode === "semantic") {
        runPaidSearch();
        return;
      }
      if (mode === "search" && !items.length && String(rawQuery() || "").trim()) {
        refresh();
        return;
      }
      choose();
    }
  }

  input.addEventListener("input", () => {
    clearTimeout(debounceTimer);
    syncAskChip();
    const q = rawQuery();
    syncSearchChrome();
    if (mode === "search" && isAskQuery(q)) {
      renderAskDraft(q);
      return;
    }
    if (mode === "search" && !searchFiresOnInput(q, searchMode)) {
      renderSemanticHint(q);
      return;
    }
    const delay = mode === "command" ? 0 : 120;
    debounceTimer = setTimeout(refresh, delay);
  });

  if (modesEl) {
    modesEl.querySelectorAll(".pal-mode").forEach((btn) => {
      btn.addEventListener("mousedown", (e) => {
        e.preventDefault();
        searchMode = btn.dataset.mode || "title";
        syncSearchChrome();
        const q = rawQuery();
        if (mode === "search" && isAskQuery(q)) {
          renderAskDraft(q);
          return;
        }
        if (!searchFiresOnInput(q, searchMode)) {
          renderSemanticHint(q);
          return;
        }
        refresh();
      });
    });
  }
  input.addEventListener("keydown", (e) => { syncAskChip(e); onKey(e); });
  document.addEventListener("keydown", (e) => {
    if (isOpen()) onKey(e);
  }, true);
  mask.addEventListener("mousedown", (e) => {
    if (e.target === mask) close();
  });

  window.Palette = {
    init, open, close, isOpen,
    openFiles: () => open("file"),
    openCommands: () => open("command"),
    openSearch: () => open("search"),
    openTasks: () => open("tasks"),
    openTags: () => open("tags"),
    openBroken: () => open("broken"),
    openOrphans: () => open("orphans"),
    openMentions: () => open("mentions"),
    getSearchMode: () => searchMode,
    setSearchMode: (m) => {
      searchMode = m || "title";
      syncSearchChrome();
      if (!isOpen()) return;
      const q = rawQuery();
      if (isAskQuery(q)) renderAskDraft(q);
      else if (!searchFiresOnInput(q, searchMode)) renderSemanticHint(q);
      else refresh();
    },
    searchFiresOnInput,
    isAskQuery,
  };
})();

/* 本文查找条：Ctrl+F 打开，Ctrl+H 展开替换，Enter 下一个，Shift+Enter 上一个 */
(function () {
  const bar = document.getElementById("find-bar");
  const input = document.getElementById("find-input");
  const replaceInput = document.getElementById("replace-input");
  const countEl = document.getElementById("find-count");
  const toggleBtn = document.getElementById("find-toggle-replace");
  let findIdx = 0;

  function syncReplaceToggle() {
    const on = !!(bar && bar.classList.contains("replace-open"));
    if (!toggleBtn) return;
    toggleBtn.setAttribute("aria-pressed", on ? "true" : "false");
    toggleBtn.title = on ? "收起替换 (Ctrl+H)" : "替换 (Ctrl+H)";
  }

  function isOpen() {
    return bar && bar.classList.contains("open");
  }

  function open(opts) {
    bar.classList.add("open");
    if (opts && opts.replace) bar.classList.add("replace-open");
    syncReplaceToggle();
    input.focus();
    input.select();
    updateCount();
  }

  function close() {
    bar.classList.remove("open");
    bar.classList.remove("replace-open");
    syncReplaceToggle();
    input.value = "";
    if (replaceInput) replaceInput.value = "";
    countEl.textContent = "";
  }

  function toggleReplace() {
    if (!isOpen()) open({ replace: true });
    else bar.classList.toggle("replace-open");
    syncReplaceToggle();
    if (bar.classList.contains("replace-open") && replaceInput) replaceInput.focus();
  }

  function countMatches() {
    const q = input.value;
    if (!q) return 0;
    const src = window.Editor && window.Editor.getValue ? window.Editor.getValue() : "";
    if (!src) return 0;
    const ql = q.toLowerCase();
    const hay = src.toLowerCase();
    let n = 0, from = 0;
    while (ql && hay.indexOf(ql, from) >= 0) {
      n++;
      from = hay.indexOf(ql, from) + ql.length;
      if (n > 999) break;
    }
    return n;
  }

  function updateCount() {
    const q = input.value;
    if (!q) { countEl.textContent = ""; findIdx = 0; return; }
    const n = countMatches();
    if (!n) { countEl.textContent = "0"; findIdx = 0; return; }
    const total = n > 999 ? "999+" : String(n);
    // 未定位时不显示 0/N（用户读不出"当前在第几处"），改为总数
    if (findIdx < 1) countEl.textContent = "共 " + total + " 处";
    else countEl.textContent = Math.min(findIdx, n) + "/" + total;
  }

  function find(backward) {
    const q = input.value;
    if (!q) return;
    const n = countMatches();
    try {
      window.find(q, false, !!backward, true, false, true, false);
    } catch (e) { /* 部分 WebView 无 window.find */ }
    if (!n) { findIdx = 0; updateCount(); return; }
    if (backward) findIdx = findIdx <= 1 ? n : findIdx - 1;
    else findIdx = findIdx >= n ? 1 : findIdx + 1;
    updateCount();
  }

  function escapeRe(s) {
    return String(s).replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  }

  function replaceOne() {
    const q = input.value;
    if (!q) return;
    const repl = replaceInput ? replaceInput.value : "";
    let found = false;
    try { found = window.find(q, false, false, true, false, true, false); } catch (e) { found = false; }
    if (found) {
      try { document.execCommand("insertText", false, repl); found = true; } catch (e) { found = false; }
    }
    if (!found && window.Editor && window.Editor.getValue) {
      const src = window.Editor.getValue();
      const i = src.toLowerCase().indexOf(q.toLowerCase());
      if (i < 0) { updateCount(); return; }
      window.Editor.setValue(src.slice(0, i) + repl + src.slice(i + q.length));
    }
    updateCount();
  }

  function replaceAll() {
    const q = input.value;
    if (!q || !window.Editor || !window.Editor.getValue) return;
    const repl = replaceInput ? replaceInput.value : "";
    const src = window.Editor.getValue();
    let next;
    try {
      next = src.replace(new RegExp(escapeRe(q), "gi"), repl);
    } catch (e) {
      next = src;
    }
    if (next !== src) window.Editor.setValue(next);
    updateCount();
  }

  document.getElementById("find-next").addEventListener("click", () => find(false));
  document.getElementById("find-prev").addEventListener("click", () => find(true));
  document.getElementById("find-close").addEventListener("click", close);
  if (toggleBtn) toggleBtn.addEventListener("click", toggleReplace);
  const oneBtn = document.getElementById("replace-one");
  if (oneBtn) oneBtn.addEventListener("click", replaceOne);
  const allBtn = document.getElementById("replace-all");
  if (allBtn) allBtn.addEventListener("click", replaceAll);
  input.addEventListener("input", () => {
    findIdx = 0;
    updateCount();
    // 输入即定位首个匹配（VSCode/Typora 同款）：当前匹配高亮 + 滚动到位，
    // 否则「共 N 处」没有文档内反馈环
    const q = input.value;
    if (q && countMatches() > 0) {
      try {
        if (window.find(q, false, false, true, false, true, false)) findIdx = 1;
      } catch (e) { /* 部分 WebView 无 window.find */ }
      updateCount();
    }
  });
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
  if (replaceInput) {
    replaceInput.addEventListener("keydown", (e) => {
      if (e.key === "Enter") {
        e.preventDefault();
        if (e.ctrlKey || e.metaKey) replaceAll();
        else replaceOne();
      } else if (e.key === "Escape") {
        e.preventDefault();
        e.stopPropagation();
        close();
      }
    });
  }

  window.FindBar = { open, close, isOpen, toggleReplace, replaceAll };
})();
