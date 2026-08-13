/* RyuuMD 主控制器：串联后端 API、编辑器、侧栏、欢迎/设置、拖放与快捷键。 */
(function () {
  const api = () => window.pywebview && window.pywebview.api;

  const state = {
    config: null,
    currentPath: null,   // 当前文件本地路径（null = 未保存的新文档）
    currentFolder: null, // 当前工作文件夹
    dirty: false,
    lastSaved: "",
    svForced: false,     // 当前文档因超过大文档阈值被强制为源码模式
  };

  // 大文档阈值（字节）：Vditor 渲染(ir)模式全量渲染超长文档会卡死，
  // 超过则以源码模式打开并 toast 告知（不静默降级，用户可随时切回）
  const LARGE_DOC_BYTES = 512 * 1024;

  const docNameEl = document.getElementById("doc-name");

  // ---------------------------------------------------------------
  // 初始化
  // ---------------------------------------------------------------
  async function boot() {
    window.renderIconButtons();

    // 按钮/快捷键/拖放绑定「前置」：不依赖后端通道。
    // 历史 bug：绑定排在 await 链之后，js_api 注入超时/异常时任一前置步骤失败，
    // 后续绑定永远不执行 → 启动后所有按钮无响应（间歇性，重启碰运气）。
    bindToolbar();
    bindModeBar();
    bindShortcuts();
    bindDragDrop();
    bindPasteImages();

    // 后端通道兜底：js_api 注入失败/超时不得拖死 boot，降级默认配置继续，
    // 保证 UI 可交互；功能调用处另有 apiOrToast 给出明确提示而非静默失败。
    try {
      await waitForApi();
      state.config = await api().get_config();
    } catch (e) {
      state.config = defaultConfig();
      toast("后端连接异常，已以默认配置启动；请尝试重启应用");
    }

    applyTheme(state.config.theme);
    applyZoom(state.config.editor_zoom || 100, true);
    window.SlashMenu.setStyle(state.config.operation_style);
    window.Editor.setOpStyle(state.config.operation_style);

    window.Sidebar.setHandlers({
      openFile: openFileByPath,
      jumpHeading: (id) => window.Editor.jumpTo(id),
      openRecent: openRecentItem,
      renameFile: renameFileFlow,
      moveFile: moveFileFlow,
      revealFile: revealFileFlow,
      deleteFile: deleteFileFlow,
      newFile: (folder) => newNoteInFolder(folder),
      newFolder: (folder) => newFolderIn(folder),
    });

    window.Home.init({
      getConfig: () => state.config,
      applyConfig,
      openFolderResult: (res) => applyFolder(res, true),
      openPath: openRecentItem,
      newDoc,
      openFileDialog: openFile,
      toast,
    });

    window.Editor.init({
      theme: themeMode(),
      mode: state.config.display_mode,
      change: onEditorChange,
      outline: (hs) => window.Sidebar.renderOutline(hs),
      onReady: afterEditorReady,
      modeChange: (m) => syncModeButtons(m),
      docDir: () => state.currentPath ? dirname(state.currentPath) : "",
    });

    window.Palette.init({
      commands: appCommands,
      listFiles: async (q) => {
        const a = api();
        if (!a || !a.list_md_files) return { items: [] };
        return a.list_md_files(state.currentFolder || "", q || "");
      },
      recentFiles: async () => {
        const a = api();
        if (!a || !a.get_recent) return [];
        const res = await a.get_recent();
        return (res && res.items) || [];
      },
      search: async (q) => {
        const a = api();
        if (!a || !a.search_vault) return { hits: [] };
        return a.search_vault(state.currentFolder || "", q || "");
      },
      onPickFile: (it) => { if (it && it.path) openFileByPath(it.path); },
      onPickHit: (it) => {
        if (!it || !it.path) return;
        openFileByPath(it.path, { line: it.line, snippet: it.snippet });
      },
      onCreate: (name) => newNoteInFolder(state.currentFolder, name),
    });
  }

  // 后端不可用时的最小配置（welcome_shown=true：异常时不再弹欢迎窗添乱）
  function defaultConfig() {
    return {
      theme: "light", operation_style: "notion", display_mode: "ir",
      welcome_shown: true, auto_save: true, daily_note_folder: "日记", editor_zoom: 100,
    };
  }

  function waitForApi() {
    return new Promise((resolve) => {
      if (api()) return resolve();
      window.addEventListener("pywebviewready", () => resolve(), { once: true });
      // 兜底轮询
      const t = setInterval(() => {
        if (api()) { clearInterval(t); resolve(); }
      }, 60);
      // 超时降级：js_api 注入异常时不无限等待（曾致 boot 卡死、按钮全灭）
      setTimeout(() => { clearInterval(t); resolve(); }, 8000);
    });
  }

  // api 的就绪检查 + 用户提示：按钮点击后静默抛错会被感知为「按钮没反应」
  function apiOrToast() {
    const a = api();
    if (!a) toast("后端连接中，请稍候再试…");
    return a;
  }

  async function afterEditorReady() {
    // 会话恢复/欢迎流程失败不得拖死编辑器（mask 常驻也会拦截所有按钮）
    try {
      // 首次启动 -> 欢迎窗口
      if (!state.config.welcome_shown) await runWelcome();

      // 命令行/拖到图标/单实例转发/新窗口传入的初始路径优先。
      // 改为向后端主动拉取（每窗口独立），消除 evaluate_js 注入的时序竞态。
      let initial = "";
      try {
        const a = api();
        if (a && a.get_initial_path) initial = await a.get_initial_path();
      } catch (e) { /* 后端不可用时走常规启动流程 */ }
      if (!initial && window.__INITIAL_PATH__) initial = window.__INITIAL_PATH__;
      if (initial) {
        await openPath(initial);
        return;
      }

      // 启动页策略：home = 始终首页；restore = 恢复上次会话（无会话则进首页）
      if (state.config.startup_page === "home") {
        window.Home.show();
        return;
      }
      const restored = await restoreSession();
      if (!restored) window.Home.show();
    } catch (e) {
      // 恢复失败不致命：确保欢迎/弹窗 mask 不残留，编辑器可用
      document.querySelectorAll(".modal-mask.open").forEach((m) => m.classList.remove("open"));
      toast("会话恢复失败，已打开空白文档");
    } finally {
      loadRecent(); // 初始化「最近」列表（无会话恢复时也要拉一次）
      refreshCloudBadge();
      maybeStartCloudSync();
    }
  }

  // 欢迎窗口完整流程：展示 → 持久化所选操作风格与「不再显示」。
  // 首次启动（afterEditorReady）与「设置 → 欢迎页」共用同一入口。
  async function runWelcome() {
    const res = await window.Welcome.show(state.config.operation_style);
    await applyConfig({ operation_style: res.style, welcome_shown: res.dontShow });
  }

  async function restoreSession() {
    const a = apiOrToast();
    if (!a) return false;
    const res = await a.restore_session();
    let restored = false;
    if (res && res.folder && res.folder.ok) { applyFolder(res.folder, false); restored = true; }
    if (res && res.file && res.file.ok) { loadDoc(res.file); restored = true; }
    return restored;
  }

  // ---------------------------------------------------------------
  // 文档加载 / 保存
  // ---------------------------------------------------------------
  const scrollMem = {};
  function loadDoc(fileRes, opts) {
    if (state.currentPath && window.Editor.getScrollRatio) {
      scrollMem[state.currentPath] = window.Editor.getScrollRatio();
    }
    if (window.Home && window.Home.isOpen()) window.Home.hide(); // 打开文档即回编辑器
    state.currentPath = fileRes.path;
    state.lastSaved = fileRes.content;
    state.dirty = false;
    // 大文档策略：超阈值且当前是渲染模式时切到源码模式，并明确告知用户
    const size = fileRes.size != null ? fileRes.size : (fileRes.content || "").length;
    if (size > LARGE_DOC_BYTES) {
      if (window.Editor.getMode() === "ir") {
        state.svForced = true;
        window.Editor.setMode("sv");
        toast("文档较大（" + fmtBytes(size) + "），已用源码模式打开以保持流畅，可在右下角切回渲染模式");
      }
    } else {
      maybeRestoreMode();
    }
    window.Editor.setValue(fileRes.content);
    if (fileRes.path) window.Sidebar.markActive(fileRes.path);
    updateDocName(fileRes.name || basename(fileRes.path));
    updateStatusPath();
    updateCount(fileRes.content);
    loadRecent(); // 后端已在 read_file 记录，刷新最近列表（fire-and-forget）
    refreshLinks();
    const jump = opts || {};
    const restore = (!jump.heading && !jump.line && !jump.snippet) ? scrollMem[fileRes.path] : null;
    setTimeout(() => {
      if (jump.heading && window.Editor.jumpToHeading) window.Editor.jumpToHeading(jump.heading);
      else if ((jump.line || jump.snippet) && window.Editor.jumpToLine) window.Editor.jumpToLine(jump.line, jump.snippet);
      else if (restore != null && window.Editor.setScrollRatio) window.Editor.setScrollRatio(restore);
    }, 280);
  }

  // 离开被强制源码模式的大文档时，恢复用户偏好的渲染模式
  function maybeRestoreMode() {
    if (!state.svForced) return;
    state.svForced = false;
    if (state.config.display_mode !== "sv") window.Editor.setMode("ir");
  }

  async function openFileByPath(path, opts) {
    if (!(await maybeConfirmDiscard())) return;
    const a = apiOrToast();
    if (!a) return;
    const res = await a.read_file(path);
    if (res.ok) loadDoc(res, opts);
    else toast("打开失败：" + (res.error || ""));
  }

  async function openPath(path) {
    const a = apiOrToast();
    if (!a) return;
    const res = await a.open_path(path);
    if (!res.ok) { toast("无法打开：" + (res.error || "")); return; }
    if (res.tree !== undefined) applyFolder(res, true);
    else loadDoc(res);
  }

  // 文件夹打开/刷新的统一处理；后端超限截断时明确提示（隐藏/巨型目录已被后端过滤）
  function applyFolder(res, announce) {
    if (announce && window.Home && window.Home.isOpen()) window.Home.hide();
    state.currentFolder = res.root;
    window.Sidebar.renderTree(res.tree, res.name);
    if (state.currentPath) window.Sidebar.markActive(state.currentPath);
    if (res.truncated) toast("「" + res.name + "」条目过多或层级过深，文件树已截断显示");
    else if (announce) toast("已打开：" + res.name);
    loadRecent(); // 后端已在 list_folder 记录，刷新最近列表（fire-and-forget）
  }

  async function save(opts) {
    const silent = opts && opts.silent;
    const a = apiOrToast();
    if (!a) return;
    const content = window.Editor.getValue();
    if (state.currentPath) {
      const res = await a.save_file(state.currentPath, content);
      if (res.ok) { markSaved(content); if (!silent) toast("已保存"); loadRecent(); }
      else toast("保存失败：" + (res.error || ""));
    } else {
      if (silent) return; // 未保存新文档不自动弹另存对话框
      const res = await a.save_file_dialog(content, "未命名.md");
      if (res.ok) {
        state.currentPath = res.path;
        markSaved(content);
        updateDocName(basename(res.path));
        window.Sidebar.markActive(res.path);
        updateStatusPath();
        toast("已保存");
        loadRecent();
        if (state.currentFolder) refreshFolder();
      } else if (!res.cancelled) {
        toast("保存失败：" + (res.error || ""));
      }
    }
  }

  function markSaved(content) {
    state.lastSaved = content;
    state.dirty = false;
    updateDocName();
  }

  async function refreshFolder() {
    if (!state.currentFolder) return;
    const a = api();
    if (!a) return;
    const res = await a.list_folder(state.currentFolder);
    if (res.ok) applyFolder(res, false);
  }

  // ---------------------------------------------------------------
  // 最近打开（侧栏「最近」tab）
  // ---------------------------------------------------------------
  async function loadRecent() {
    const a = api();
    if (!a) return;
    const res = await a.get_recent();
    if (res && res.ok) window.Sidebar.renderRecent(res.items);
  }

  // 点击最近项：与 openPath 同路，但打开失败（文件已移动/删除）时自动移除失效项
  async function openRecentItem(path) {
    if (!(await maybeConfirmDiscard())) return;
    const a = apiOrToast();
    if (!a) return;
    const res = await a.open_path(path);
    if (!res.ok) {
      toast("无法打开（可能已移动或删除）：" + basename(path));
      a.remove_recent(path).then(loadRecent);
      return;
    }
    if (res.tree !== undefined) applyFolder(res, true);
    else loadDoc(res);
  }

  async function clearRecent() {
    const a = apiOrToast();
    if (!a) return;
    await a.clear_recent();
    loadRecent();
  }

  // ---------------------------------------------------------------
  // 编辑变化
  // ---------------------------------------------------------------
  // now 由编辑器防抖回调传入已序列化的全文，避免这里再 getValue 一次（大文档开销大）
  let autoSaveTimer = null;
  function onEditorChange(now) {
    if (now == null) now = window.Editor.getValue();
    state.dirty = now !== state.lastSaved;
    updateDocName();
    updateCount(now);
    scheduleAutoSave();
  }

  function scheduleAutoSave() {
    clearTimeout(autoSaveTimer);
    if (!state.config || state.config.auto_save === false) return;
    if (!state.currentPath || !state.dirty) return;
    autoSaveTimer = setTimeout(() => {
      if (state.dirty && state.currentPath) save({ silent: true });
    }, 1800);
  }

  function updateCount(text) {
    const t = text == null ? window.Editor.getValue() : text;
    const chars = (t || "").replace(/\s/g, "").length;
    const el = document.getElementById("sb-count");
    if (el) el.textContent = chars + " 字";
    state._count = chars;
  }

  document.addEventListener("selectionchange", () => {
    const el = document.getElementById("sb-count");
    if (!el) return;
    const host = document.getElementById("editor");
    const sel = window.getSelection();
    if (!sel || sel.isCollapsed || !host || !sel.anchorNode || !host.contains(sel.anchorNode)) {
      if (state._count != null) el.textContent = state._count + " 字";
      return;
    }
    const n = String(sel).replace(/\s/g, "").length;
    el.textContent = "选中 " + n + " 字";
  });

  function updateStatusPath() {
    const el = document.getElementById("sb-path");
    if (el) el.textContent = state.currentPath || "未保存文档";
  }

  // ---------------------------------------------------------------
  // 渲染 / 源码模式切换（底部状态栏右下角）
  // ---------------------------------------------------------------
  function bindModeBar() {
    document.querySelectorAll(".sb-mode").forEach((btn) => {
      btn.addEventListener("click", () => {
        const m = btn.dataset.mode;
        // 不在此按 getMode() 去重：快速连切时 getMode 是重建目的地而非最终意图，
        // 统一交给 Editor.setMode 仲裁（同模式幂等、重建中排队补切）
        window.Editor.setMode(m);
        syncModeButtons(m);
        applyConfig({ display_mode: m });
      });
    });
  }

  function syncModeButtons(m) {
    document.querySelectorAll(".sb-mode").forEach((b) =>
      b.classList.toggle("active", b.dataset.mode === m)
    );
  }

  function updateDocName(name) {
    const base = name || (state.currentPath ? basename(state.currentPath) : "未命名");
    docNameEl.innerHTML = base + (state.dirty ? '<span class="dirty">●</span>' : "");
  }

  // ---------------------------------------------------------------
  // 工具栏 / 快捷键
  // ---------------------------------------------------------------
  function bindToolbar() {
    on("btn-home", () => window.Home.toggle());
    on("btn-sidebar", () => {
      document.getElementById("sidebar").classList.toggle("collapsed");
    });
    on("btn-open-folder", openFolder);
    on("btn-open-file", openFile);
    on("btn-new", newDoc);
    on("btn-save", save);
    on("btn-search", () => window.Palette.openSearch());
    on("btn-theme", toggleTheme);
    on("btn-settings", openSettings);
    on("btn-clear-recent", clearRecent);
    on("sb-cloud", runCloudSync);
    on("sb-zoom", () => applyZoom(100));

    // 侧栏标签切换（目录 / 大纲 / 最近）：tab 的 data-panel 对应 panel-<name> 面板
    document.querySelectorAll(".side-tab").forEach((tab) => {
      tab.addEventListener("click", () => {
        document.querySelectorAll(".side-tab").forEach((t) => t.classList.remove("active"));
        tab.classList.add("active");
        document.querySelectorAll(".side-panel").forEach((p) =>
          p.classList.toggle("active", p.id === "panel-" + tab.dataset.panel)
        );
      });
    });

    // 空态「打开文件夹」按钮由 sidebar.js 的 rebindEmpty 统一绑定
    // （按钮会被 renderTree 的 innerHTML 重建，这里再绑会造成首启双弹对话框）
  }

  async function openFolder() {
    const a = apiOrToast();
    if (!a) return;
    const res = await a.open_folder_dialog();
    if (res && res.ok) applyFolder(res, true);
  }

  async function openFile() {
    if (!(await maybeConfirmDiscard())) return;
    const a = apiOrToast();
    if (!a) return;
    const res = await a.open_file_dialog();
    if (res && res.ok) loadDoc(res);
  }

  async function newDoc() {
    if (!(await maybeConfirmDiscard())) return;
    if (window.Home && window.Home.isOpen()) window.Home.hide();
    state.currentPath = null;
    state.lastSaved = "";
    state.dirty = false;
    maybeRestoreMode();
    window.Editor.setValue("");
    window.Sidebar.markActive(null);
    updateDocName("未命名");
    updateStatusPath();
    updateCount("");
    window.Editor.focus();
  }

  function openSettings() {
    window.Settings.open(state.config, applyConfig);
  }

  async function applyConfig(partial) {
    Object.assign(state.config, partial);
    const a = api();
    // cloud_sync 含脱敏字段，必须走 save_cloud_settings，禁止经 update_config 把密码冲掉
    const persist = Object.assign({}, partial);
    delete persist.cloud_sync;
    if (a && Object.keys(persist).length) await a.update_config(persist);
    if ("theme" in partial) applyTheme(partial.theme);
    if ("operation_style" in partial) {
      window.SlashMenu.setStyle(partial.operation_style);
      window.Editor.setOpStyle(partial.operation_style);
    }
    if ("cloud_sync" in partial) refreshCloudBadge();
    if ("editor_zoom" in partial) applyZoom(partial.editor_zoom, true);
  }

  function refreshCloudBadge() {
    const el = document.getElementById("sb-cloud");
    if (!el) return;
    const cs = (state.config && state.config.cloud_sync) || {};
    el.style.display = cs.enabled ? "" : "none";
  }

  async function runCloudSync() {
    const a = apiOrToast();
    if (!a || !a.sync_cloud) return;
    const el = document.getElementById("sb-cloud");
    if (el) { el.classList.add("busy"); el.textContent = "同步中…"; }
    toast("正在云同步…");
    try {
      const res = await a.sync_cloud("");
      toast(res.ok ? (res.message || "同步完成") : "同步失败：" + (res.error || ""));
    } finally {
      if (el) { el.classList.remove("busy"); el.textContent = "云同步"; }
    }
  }

  function maybeStartCloudSync() {
    const cs = (state.config && state.config.cloud_sync) || {};
    if (cs.enabled && cs.auto_on_start) runCloudSync();
  }

  function bindShortcuts() {
    window.addEventListener("keydown", (e) => {
      if (e.key === "Escape") {
        if (window.Palette && window.Palette.isOpen()) return;
        if (window.FindBar && window.FindBar.isOpen()) {
          window.FindBar.close();
          return;
        }
        if (window.Home.isOpen() && !document.querySelector(".modal-mask.open")) {
          window.Home.hide();
        }
        return;
      }
      const mod = e.ctrlKey || e.metaKey;
      if (!mod) return;
      const k = e.key.toLowerCase();
      if (k === "s") { e.preventDefault(); save(); }
      else if (k === "o") { e.preventDefault(); openFile(); }
      else if (k === "n") { e.preventDefault(); newDoc(); }
      else if (k === "p") {
        e.preventDefault();
        if (e.shiftKey) window.Palette.openCommands();
        else window.Palette.openFiles();
      }
      else if (k === "f") {
        e.preventDefault();
        if (e.shiftKey) window.Palette.openSearch();
        else window.FindBar.open();
      }
      else if (k === "d" && e.shiftKey) {
        e.preventDefault();
        openDailyNote();
      }
      else if (k === "b" && e.shiftKey) {
        e.preventDefault();
        document.getElementById("sidebar").classList.toggle("collapsed");
      }
      else if (k === "h" && e.shiftKey) {
        e.preventDefault();
        window.Home.toggle();
      }
      else if (k === "=" || k === "+" || e.key === "Add") {
        e.preventDefault();
        adjustZoom(10);
      }
      else if (k === "-" || e.key === "Subtract") {
        e.preventDefault();
        adjustZoom(-10);
      }
      else if (k === "0") {
        e.preventDefault();
        applyZoom(100);
      }
    });
  }

  // ---------------------------------------------------------------
  // 主题
  // ---------------------------------------------------------------
  function themeMode() {
    return state.config.theme === "dark" ? "dark" : "light";
  }

  function applyTheme(theme) {
    const mode = theme === "dark" ? "dark" : "light";
    document.documentElement.setAttribute("data-theme", mode);
    // 只替换按钮内的图标容器，保留旁边的「主题」文字
    const themeIcon = document.querySelector("#btn-theme .ib-icon");
    if (themeIcon) themeIcon.innerHTML = window.ICONS[mode === "dark" ? "sun" : "moon"];
    window.Editor.setTheme(mode);
  }

  function toggleTheme() {
    const next = themeMode() === "dark" ? "light" : "dark";
    applyConfig({ theme: next });
  }

  // ---------------------------------------------------------------
  // 拖放（文件 / 文件夹）
  // ---------------------------------------------------------------
  function bindDragDrop() {
    const hint = document.getElementById("drop-hint");
    let depth = 0;
    window.addEventListener("dragenter", (e) => {
      e.preventDefault();
      depth++;
      hint.classList.add("show");
    });
    window.addEventListener("dragover", (e) => e.preventDefault());
    window.addEventListener("dragleave", (e) => {
      e.preventDefault();
      depth = Math.max(0, depth - 1);
      if (depth === 0) hint.classList.remove("show");
    });
    // drop 必须在「捕获阶段」接管（第三参 true），先于 Vditor 面板的 drop 处理：
    // Vditor 把拖入文件当附件插入，其处理器首行即 stopPropagation + preventDefault，
    // 事件冒泡被截断 —— document 级 pywebview 监听收不到（拿不到本地路径、文件
    // 打不开），window 冒泡监听也收不到（hint 遮罩常驻、界面看似卡死在拖拽状态）。
    // 捕获阶段统一接管：含文件 → 拦截并转发 Python 打开；无文件（编辑器内部
    // 拖拽选区/图片）→ 放行给 Vditor。
    window.addEventListener(
      "drop",
      (e) => {
        depth = 0;
        hint.classList.remove("show");
        const dt = e.dataTransfer;
        const hasFiles =
          dt && dt.types &&
          Array.prototype.indexOf.call(dt.types, "Files") >= 0;
        if (!hasFiles) return; // 编辑器内部拖拽，放行
        e.preventDefault();
        e.stopPropagation();
        if (!dt.files || !dt.files.length) return;
        const dropped = Array.prototype.slice.call(dt.files);
        if (dropped.length && dropped.every(isImageFile)) {
          saveImages(dropped).then((md) => {
            if (md) window.Editor.insertValue(md);
          });
          return;
        }
        // WebView2 不在 JS File 上暴露本地路径。经 AdditionalObjects 把真实 File
        // 回传原生层：pywebview 收到 "FilesDropped" 后把路径暂存 _dnd_state['paths']
        // （该通道要求 _dnd_state['num_listeners']>0，见 main.py _bind_drop），
        // 再由 api.open_dropped_files 按文件名匹配路径并打开。两条 WebMessage 按序
        // 派发，paths 先于 api 调用就绪，无竞态。
        try {
          window.chrome.webview.postMessageWithAdditionalObjects(
            "FilesDropped",
            dt.files
          );
        } catch (err) {
          // 仅合成事件（测试）/非 edgechromium 环境会抛；忽略，让 api 调用照常发出
        }
        const names = Array.prototype.map.call(dt.files, (f) => f.name);
        if (window.pywebview && window.pywebview.api) {
          // 即时反馈：drop → 打开完成的链路（原生回传 → Python 匹配 → 读文件 →
          // Vditor 渲染）需要数百毫秒，无反馈会被感知为「卡住」
          toast("正在打开：" + (names[0] || ""));
          window.pywebview.api.open_dropped_files(names);
        }
      },
      true
    );
  }

  // 由 Python 端回调，传入拖入项的本地完整路径。
  // 文件夹 -> 打开为工作区；.md 文件 -> 打开文档。WebView2 下 JS 无法拿到本地路径，
  // 必须经由原生 FilesDropped 通道回传（见 bindDragDrop 捕获转发），这里只负责接收并打开。
  async function openDroppedPath(path) {
    const hint = document.getElementById("drop-hint");
    if (hint) hint.classList.remove("show");
    if (!path) return;
    if (!(await maybeConfirmDiscard())) return;
    await openPath(path);
  }
  window.__openDroppedPath = openDroppedPath;

  function isImageFile(f) {
    if (!f) return false;
    if (f.type && f.type.indexOf("image/") === 0) return true;
    return /\.(png|jpe?g|gif|webp|bmp|svg)$/i.test(f.name || "");
  }

  function fileToB64(file) {
    return new Promise((resolve, reject) => {
      const r = new FileReader();
      r.onload = () => {
        const s = String(r.result || "");
        const i = s.indexOf(",");
        resolve(i >= 0 ? s.slice(i + 1) : s);
      };
      r.onerror = reject;
      r.readAsDataURL(file);
    });
  }

  function bindPasteImages() {
    document.addEventListener("paste", (e) => {
      const host = document.getElementById("editor");
      if (!host || !e.target || !host.contains(e.target)) return;
      const files = [];
      const cd = e.clipboardData;
      if (cd && cd.files) {
        for (let i = 0; i < cd.files.length; i++) {
          if (isImageFile(cd.files[i])) files.push(cd.files[i]);
        }
      }
      if (!files.length && cd && cd.items) {
        for (let i = 0; i < cd.items.length; i++) {
          const it = cd.items[i];
          if (it.type && it.type.indexOf("image/") === 0) {
            const f = it.getAsFile();
            if (f) files.push(f);
          }
        }
      }
      if (!files.length) return;
      e.preventDefault();
      e.stopPropagation();
      saveImages(files).then((md) => { if (md) window.Editor.insertValue(md); });
    }, true);
  }

  async function saveImages(files) {
    const a = apiOrToast();
    if (!a || !a.save_image) return "";
    if (!state.currentPath && !state.currentFolder) {
      toast("请先保存文档或打开仓库，再插入图片");
      return "";
    }
    let md = "";
    for (let i = 0; i < files.length; i++) {
      const f = files[i];
      if (!isImageFile(f)) continue;
      let b64;
      try { b64 = await fileToB64(f); } catch (err) { toast("读取图片失败"); return md; }
      const res = await a.save_image(
        state.currentPath || "", state.currentFolder || "",
        f.name || "image.png", b64, f.type || ""
      );
      if (!res.ok) { toast(res.error || "图片保存失败"); return md; }
      md += "![" + (res.name || "") + "](" + res.rel + ")\n";
    }
    if (md) setTimeout(() => { if (window.Editor.enhanceRendered) window.Editor.enhanceRendered(); }, 80);
    return md;
  }

  async function listVaultFiles(query) {
    const a = api();
    if (!a || !a.list_md_files) return [];
    const res = await a.list_md_files(state.currentFolder || "", query || "");
    return (res && res.items) || [];
  }

  async function openWikilink(name) {
    const a = apiOrToast();
    if (!a || !a.resolve_wikilink) return;
    const res = await a.resolve_wikilink(state.currentFolder || "", name, state.currentPath || "");
    if (!res.ok) { toast(res.error || "无法解析链接"); return; }
    if (res.exists) {
      openFileByPath(res.path, { heading: res.heading || "" });
      return;
    }
    if (!window.confirm("笔记「" + name + "」不存在，是否创建？")) return;
    const suggested = res.suggested || "";
    const folder = dirname(suggested) || state.currentFolder || "";
    const fname = basename(suggested).replace(/\.md$/i, "");
    const created = await a.new_file(folder, fname);
    if (!created.ok) { toast(created.error || "创建失败"); return; }
    const file = await a.read_file(created.path);
    if (file.ok) loadDoc(file);
    refreshFolder();
  }

  function openRelLink(href) {
    const h = decodeURIComponent((href || "").split("#")[0].trim());
    if (!h) return;
    if (/\.(md|markdown|mdown|mkd|mdx)$/i.test(h) || !/\.[a-z0-9]+$/i.test(h)) {
      openWikilink(h);
    }
  }

  function openExternal(url) {
    const a = api();
    if (a && a.open_external) a.open_external(url);
  }

  async function openDailyNote() {
    const a = apiOrToast();
    if (!a || !a.open_daily_note) return;
    if (!(await maybeConfirmDiscard())) return;
    const res = await a.open_daily_note(state.currentFolder || "", (state.config && state.config.daily_note_folder) || "");
    if (!res.ok) { toast(res.error || "无法打开日记"); return; }
    loadDoc(res);
    refreshFolder();
  }

  async function exportHtml() {
    const a = apiOrToast();
    if (!a || !a.save_html_dialog) return;
    const html = window.Editor.getHTML ? window.Editor.getHTML() : "";
    const name = (state.currentPath ? basename(state.currentPath).replace(/\.md$/i, "") : "导出") + ".html";
    const res = await a.save_html_dialog(html, name);
    if (res.ok) toast("已导出：" + basename(res.path));
    else if (!res.cancelled) toast("导出失败：" + (res.error || ""));
  }

  function toggleFocus() {
    const on = window.Editor.toggleFocusMode();
    const side = document.getElementById("sidebar");
    if (on) {
      state._sideBeforeFocus = side.classList.contains("collapsed");
      side.classList.add("collapsed");
    } else if (state._sideBeforeFocus === false) {
      side.classList.remove("collapsed");
    }
    toast(on ? "已进入专注模式" : "已退出专注模式");
  }

  function appCommands() {
    return [
      { id: "save", title: "保存", keys: "Ctrl+S", group: "文件", run: save },
      { id: "new", title: "新建文档", keys: "Ctrl+N", group: "文件", run: newDoc },
      { id: "open", title: "打开文件", keys: "Ctrl+O", group: "文件", run: openFile },
      { id: "open-folder", title: "打开文件夹", keys: "", group: "文件", run: openFolder },
      { id: "daily", title: "今日日记", keys: "Ctrl+Shift+D", group: "文件", run: openDailyNote },
      { id: "export", title: "导出 HTML", keys: "", group: "文件", run: exportHtml },
      { id: "quick-open", title: "快速打开笔记", keys: "Ctrl+P", group: "导航", run: () => window.Palette.openFiles() },
      { id: "search", title: "在仓库中搜索", keys: "Ctrl+Shift+F", group: "导航", run: () => window.Palette.openSearch() },
      { id: "home", title: "首页", keys: "Ctrl+Shift+H", group: "导航", run: () => window.Home.toggle() },
      { id: "sidebar", title: "切换侧栏", keys: "Ctrl+Shift+B", group: "视图", run: () => document.getElementById("sidebar").classList.toggle("collapsed") },
      { id: "theme", title: "切换主题", keys: "", group: "视图", run: toggleTheme },
      { id: "focus", title: "专注模式", keys: "", group: "视图", run: toggleFocus },
      { id: "typewriter", title: "打字机模式", keys: "", group: "视图", run: toggleTypewriter },
      { id: "zoom-in", title: "放大编辑区", keys: "Ctrl+=", group: "视图", run: () => adjustZoom(10) },
      { id: "zoom-out", title: "缩小编辑区", keys: "Ctrl+-", group: "视图", run: () => adjustZoom(-10) },
      { id: "zoom-reset", title: "重置缩放", keys: "Ctrl+0", group: "视图", run: () => applyZoom(100) },
      { id: "ir", title: "渲染模式", keys: "", group: "视图", run: () => { window.Editor.setMode("ir"); syncModeButtons("ir"); applyConfig({ display_mode: "ir" }); } },
      { id: "sv", title: "源码模式", keys: "", group: "视图", run: () => { window.Editor.setMode("sv"); syncModeButtons("sv"); applyConfig({ display_mode: "sv" }); } },
      { id: "settings", title: "设置", keys: "", group: "应用", run: openSettings },
      { id: "cloud", title: "立即云同步", keys: "", group: "应用", run: runCloudSync },
      { id: "date", title: "插入今天日期", keys: "", group: "插入", run: () => window.Editor.insertValue(todayStamp(false)) },
      { id: "time", title: "插入当前时间", keys: "", group: "插入", run: () => window.Editor.insertValue(todayStamp(true)) },
    ];
  }

  function pad2(n) { return (n < 10 ? "0" : "") + n; }
  function todayStamp(withTime) {
    const d = new Date();
    const day = d.getFullYear() + "-" + pad2(d.getMonth() + 1) + "-" + pad2(d.getDate());
    if (!withTime) return day;
    return day + " " + pad2(d.getHours()) + ":" + pad2(d.getMinutes());
  }

  function applyZoom(pct, silent) {
    let z = parseInt(pct, 10);
    if (isNaN(z)) z = 100;
    z = Math.max(80, Math.min(160, z));
    if (!state.config) state.config = {};
    state.config.editor_zoom = z;
    const ed = document.getElementById("editor");
    if (ed) ed.style.zoom = String(z / 100);
    const badge = document.getElementById("sb-zoom");
    if (badge) badge.textContent = z + "%";
    if (!silent && api()) api().update_config({ editor_zoom: z });
  }

  function adjustZoom(delta) {
    applyZoom(((state.config && state.config.editor_zoom) || 100) + delta);
  }

  function toggleTypewriter() {
    const on = window.Editor.toggleTypewriter();
    toast(on ? "已进入打字机模式" : "已退出打字机模式");
  }

  async function newNoteInFolder(folder, name) {
    const a = apiOrToast();
    if (!a) return;
    const dir = folder || state.currentFolder;
    if (!dir) { toast("请先打开仓库或文件夹"); return; }
    let fname = (name || "").trim();
    if (!fname) {
      fname = await promptText({ title: "新建笔记", sub: dir, value: "未命名" });
      if (fname == null) return;
    }
    if (!(await maybeConfirmDiscard())) return;
    const res = await a.new_file(dir, fname);
    if (!res.ok) { toast(res.error || "创建失败"); return; }
    const file = await a.read_file(res.path);
    if (file.ok) loadDoc(file);
    refreshFolder();
    toast("已创建：" + res.name);
  }

  async function newFolderIn(parent) {
    const a = apiOrToast();
    if (!a) return;
    const dir = parent || state.currentFolder;
    if (!dir) { toast("请先打开仓库或文件夹"); return; }
    const name = await promptText({ title: "新建文件夹", sub: dir, value: "新建文件夹" });
    if (name == null) return;
    const res = await a.new_folder(dir, name);
    if (!res.ok) { toast(res.error || "创建失败"); return; }
    refreshFolder();
    toast("已创建文件夹：" + res.name);
  }

  async function refreshLinks() {
    if (!window.Sidebar.renderLinks) return;
    if (!state.currentPath) {
      window.Sidebar.renderLinks({ back: [], out: [], message: "打开一篇笔记后，这里显示指向它的双向链接" });
      return;
    }
    const md = window.Editor.getValue() || state.lastSaved || "";
    const re = /\[\[([^\]|#]+)(?:[#|][^\]]*)?\]\]/g;
    const names = [];
    let m;
    while ((m = re.exec(md))) {
      const n = m[1].trim();
      if (n && names.indexOf(n) < 0) names.push(n);
    }
    const a = api();
    const out = [];
    if (a && a.resolve_wikilink) {
      for (let i = 0; i < names.length; i++) {
        try {
          const r = await a.resolve_wikilink(state.currentFolder || "", names[i], state.currentPath);
          out.push({
            wiki: names[i],
            name: names[i],
            path: r.exists ? r.path : "",
            exists: !!r.exists,
            rel: r.exists ? r.path : (r.suggested || ""),
          });
        } catch (e) {
          out.push({ wiki: names[i], name: names[i], exists: false });
        }
      }
    }
    let back = [];
    if (a && a.find_backlinks) {
      try {
        const res = await a.find_backlinks(state.currentFolder || "", state.currentPath);
        back = (res && res.hits) || [];
      } catch (e) { back = []; }
    }
    window.Sidebar.renderLinks({ back, out });
  }

  // ---------------------------------------------------------------
  // 文件管理（侧栏右键菜单：重命名 / 移动 / 删除真实磁盘文件）
  // ---------------------------------------------------------------
  // 通用输入小弹窗：Promise<string|null>，null = 取消。
  // 结构与 home.js 仓库重命名弹窗同款（.mini-modal 样式复用）。
  function promptText({ title, sub, value }) {
    const mask = document.getElementById("input-modal-mask");
    return new Promise((resolve) => {
      mask.innerHTML = `
        <div class="modal mini-modal">
          <div class="modal-head">
            <span class="badge">${window.ICONS.edit}</span>
            <div><h2>${esc(title)}</h2><p>${esc(sub || "")}</p></div>
          </div>
          <div class="modal-body">
            <input class="mm-input" id="pm-input" maxlength="120" />
          </div>
          <div class="modal-foot">
            <button class="btn btn-ghost" id="pm-cancel">取消</button>
            <button class="btn btn-primary" id="pm-ok">确定</button>
          </div>
        </div>`;
      mask.classList.add("open");
      const input = document.getElementById("pm-input");
      input.value = value || "";
      function done(val) {
        mask.removeEventListener("click", onMask);
        mask.classList.remove("open");
        mask.innerHTML = "";
        resolve(val);
      }
      function onMask(e) { if (e.target === mask) done(null); }
      mask.addEventListener("click", onMask);
      document.getElementById("pm-ok").addEventListener("click", () => {
        const v = input.value.trim();
        if (!v) { toast("名称不能为空"); return; }
        done(v);
      });
      document.getElementById("pm-cancel").addEventListener("click", () => done(null));
      input.addEventListener("keydown", (e) => {
        if (e.key === "Enter") { e.preventDefault(); document.getElementById("pm-ok").click(); }
        else if (e.key === "Escape") { e.stopPropagation(); done(null); }
      });
      input.focus();
      input.select();
    });
  }

  // 重命名/移动后，若动的是当前打开的文件，同步编辑器侧的路径与标题
  function syncCurrentPath(oldPath, newPath, newName) {
    if (state.currentPath !== oldPath) return;
    state.currentPath = newPath;
    if (newName) updateDocName(newName);
    updateStatusPath();
  }

  async function renameFileFlow(path) {
    const a = apiOrToast();
    if (!a) return;
    const name = await promptText({ title: "重命名文件", sub: path, value: basename(path) });
    if (name == null) return;
    const res = await a.rename_file(path, name);
    if (!res.ok) { toast("重命名失败：" + (res.error || "")); return; }
    syncCurrentPath(path, res.path, res.name);
    refreshFolder();
    loadRecent();
    toast("已重命名为：" + res.name);
  }

  async function moveFileFlow(path) {
    const a = apiOrToast();
    if (!a) return;
    const res = await a.move_file_dialog(path);
    if (!res || res.cancelled) return;
    if (!res.ok) { toast("移动失败：" + (res.error || "")); return; }
    syncCurrentPath(path, res.path, null);
    refreshFolder();
    loadRecent();
    toast("已移动到：" + res.path);
  }

  async function revealFileFlow(path) {
    const a = apiOrToast();
    if (!a) return;
    const res = await a.reveal_in_explorer(path);
    if (!res.ok) toast(res.error || "无法打开资源管理器");
  }

  async function deleteFileFlow(path) {
    const a = apiOrToast();
    if (!a) return;
    // 风险操作确认：明确告知去向（回收站可恢复）
    if (!window.confirm("确定把「" + basename(path) + "」移入回收站？\n可从系统回收站恢复。")) return;
    const res = await a.delete_file(path);
    if (!res.ok) { toast("删除失败：" + (res.error || "")); return; }
    if (state.currentPath === path) {
      // 当前打开的文件被删：内容保留为未保存草稿，编辑成果不丢
      state.currentPath = null;
      state.dirty = true;
      updateDocName(basename(path));
      updateStatusPath();
      window.Sidebar.markActive(null);
    }
    refreshFolder();
    loadRecent();
    toast("已移入回收站");
  }

  // ---------------------------------------------------------------
  // 工具
  // ---------------------------------------------------------------
  async function maybeConfirmDiscard() {
    if (!state.dirty) return true;
    return window.confirm("当前文档有未保存的更改，是否放弃？");
  }

  function basename(p) {
    if (!p) return "未命名";
    return p.split(/[\\/]/).pop();
  }

  function dirname(p) {
    if (!p) return "";
    const i = Math.max(p.lastIndexOf("\\"), p.lastIndexOf("/"));
    return i >= 0 ? p.slice(0, i) : "";
  }

  // 注入 HTML 前转义（文件路径/名称是用户数据）
  function esc(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function fmtBytes(n) {
    if (n >= 1024 * 1024) return (n / 1024 / 1024).toFixed(1) + " MB";
    return Math.round(n / 1024) + " KB";
  }

  function on(id, fn) {
    const el = document.getElementById(id);
    if (el) el.addEventListener("click", fn);
  }

  let toastTimer = null;
  function toast(msg) {
    const el = document.getElementById("toast");
    el.textContent = msg;
    el.classList.add("show");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => el.classList.remove("show"), 2200);
  }

  window.App = {
    openFolder,
    openFile,
    save,
    toast,
    showWelcome: runWelcome,
    syncCloud: runCloudSync,
    listVaultFiles,
    openWikilink,
    openRelLink,
    openExternal,
    openDailyNote,
    exportHtml,
    toggleFocus,
    newNoteInFolder,
    adjustZoom,
    applyZoom,
  };

  let booted = false;
  function bootOnce() {
    if (booted) return;
    booted = true;
    boot();
  }

  document.addEventListener("DOMContentLoaded", bootOnce);
  if (document.readyState !== "loading") bootOnce();
})();
