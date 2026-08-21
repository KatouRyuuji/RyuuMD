/* RyuuMD 主控制器：串联后端 API、编辑器、侧栏、欢迎/设置、拖放与快捷键。 */
(function () {
  const api = () => window.pywebview && window.pywebview.api;

  const state = {
    config: null,
    currentPath: null,   // 当前文件本地路径（null = 未保存的新文档）
    currentFolder: null, // 当前工作文件夹
    dirty: false,
    lastSaved: "",
    latestContent: "", // 编辑器最近一次已知内容（onEditorChange/装载/保存快照），保存竞态下重算脏标记用
    svForced: false,     // 当前文档因超过大文档阈值被强制为源码模式
    templates: [],
    diskMtime: 0,
    diskGone: false,
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
    bindViewToggles();
    bindSidebarResize();
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
    applyFonts();
    applyZoom(state.config.editor_zoom || 100, true);
    applyReadable(!!state.config.readable_width, true);
    applySidebarWidth(state.config.sidebar_width || 256);
    if (state.config.focus_mode) applyFocusVisual(true);
    if (state.config.typewriter_mode) window.Editor.toggleTypewriter(true);
    syncViewToggles();
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
      vaultIndex: async (kind, q) => {
        const a = api();
        if (!a || !a.vault_index) return { items: [] };
        return a.vault_index(state.currentFolder || "", kind, q || "", state.currentPath || "");
      },
    });

    // 与编辑器无关的后台任务：不等 Vditor，避免首页被 lute.min.js 堵住
    loadRecent();
    refreshCloudBadge();
    maybeStartCloudSync();

    decideStartup();
  }

  // 后端不可用时的最小配置（welcome_shown=true：异常时不再弹欢迎窗添乱）
  function defaultConfig() {
    return {
      theme: "light", palette_light: "sky", palette_dark: "vampire",
      font_ui: "", font_mono: "",
      operation_style: "notion", display_mode: "ir",
      welcome_shown: true, auto_save: true, daily_note_folder: "日记", editor_zoom: 100,
      readable_width: false, sidebar_width: 256, focus_mode: false, typewriter_mode: false,
      startup_page: "home",
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

  // ---------------------------------------------------------------
  // 启动决策 + 编辑器惰性创建
  // ---------------------------------------------------------------
  // 为何惰性：Vditor 首次构建会动态加载 lute.min.js（约 4MB）。旧流程把
  // 首页也挂在 after 上，用户一直盯着编辑器空壳。现在 boot 拿到 config
  // 后立刻决定去向：首页立即 Home.show()；只有 initial path / restore
  // / 用户打开文档时才 await ensureEditor()。

  let editorP = null; // 共享的创建 Promise，并发调用合并为一次 init

  function waitEditorReady() {
    // setMode 重建时 isReady() 会短暂为假；已成功 init 过则等面板回来，不重新 init
    return new Promise((resolve) => {
      let n = 0;
      const t = setInterval(() => {
        n++;
        if (window.Editor && window.Editor.isReady()) { clearInterval(t); resolve(true); }
        else if (n > 200) { clearInterval(t); resolve(false); } // ~20s
      }, 100);
    });
  }

  function ensureEditor() {
    if (window.Editor && window.Editor.isReady()) return Promise.resolve(true);
    if (editorP) {
      return editorP.then((ok) => {
        if (!ok) return false;
        if (window.Editor.isReady()) return true;
        return waitEditorReady();
      });
    }
    editorP = bootEditor().then((ok) => {
      if (!ok) editorP = null; // 失败后允许下次打开再试
      return ok;
    });
    return editorP;
  }

  function bootEditor() {
    return new Promise((resolve) => {
      let settled = false;
      let gen = 0;
      const WAIT = 7000;

      function finish(ok) {
        if (settled) return;
        settled = true;
        scheduleFontLoad();
        if (!ok) toast("编辑器启动失败，请再试一次打开文档");
        resolve(!!ok);
      }

      function readyCb(myGen) {
        return function () {
          if (myGen !== gen) return;
          finish(true);
        };
      }

      function start(isRetry) {
        gen += 1;
        const opts = {
          theme: themeMode(),
          mode: state.config && state.config.display_mode,
          change: onEditorChange,
          outline: (hs) => window.Sidebar.renderOutline(hs),
          onReady: readyCb(gen),
          modeChange: (m) => syncModeButtons(m),
          docDir: () => state.currentPath ? dirname(state.currentPath) : "",
        };
        // 看门狗重试走 rebuild（销毁后重建）；首次走 init
        if (isRetry && window.Editor.rebuild) window.Editor.rebuild(opts.onReady);
        else window.Editor.init(opts);
      }

      start(false);
      setTimeout(() => {
        if (settled) return;
        // 看门狗：首次 7s 未 ready（after 丢失 / lute 卡住）则销毁重建一次；
        // 二次仍失败则 toast，不阻塞首页。
        start(true);
        setTimeout(() => { if (!settled) finish(false); }, WAIT);
      }, WAIT);
    });
  }

  function scheduleEditorWarmup() {
    // 首页已可见：空闲时预热 Vditor，首次点开文档基本无感。
    // requestIdleCallback 在 WebView2 忙时可能很晚，用 timeout 兜底。
    const kick = function () { ensureEditor(); };
    if (window.requestIdleCallback) window.requestIdleCallback(kick, { timeout: 2000 });
    else setTimeout(kick, 200);
  }

  function scheduleFontLoad() {
    // 首屏已出（首页显示或编辑器就绪，取先到者）后再挂 25MB 字体，
    // 加载前走 --font-ui 系统字体回退，swap 后自动换。
    if (scheduleFontLoad._done) return;
    scheduleFontLoad._done = true;
    const inject = function () {
      if (document.getElementById("ryuu-fonts")) return;
      const link = document.createElement("link");
      link.id = "ryuu-fonts";
      link.rel = "stylesheet";
      link.href = "css/fonts.css";
      document.head.appendChild(link);
    };
    requestAnimationFrame(function () {
      if (window.requestIdleCallback) window.requestIdleCallback(inject, { timeout: 1500 });
      else setTimeout(inject, 80);
    });
  }

  async function decideStartup() {
    try {
      if (!state.config.welcome_shown) await runWelcome();

      // 命令行/拖到图标/单实例转发/新窗口传入的初始路径优先。
      // 向后端主动拉取（每窗口独立），消除 evaluate_js 注入的时序竞态。
      let initial = "";
      try {
        const a = api();
        if (a && a.get_initial_path) initial = await a.get_initial_path();
      } catch (e) { /* 后端不可用时走常规启动流程 */ }
      if (!initial && window.__INITIAL_PATH__) initial = window.__INITIAL_PATH__;
      if (initial) {
        await openPath(initial);
        scheduleFontLoad();
        return;
      }

      // 仅 restore 才恢复会话；home / 缺失 / 非法值一律首页
      if (state.config.startup_page === "restore") {
        const restored = await restoreSession();
        if (!restored) {
          window.Home.show();
          scheduleEditorWarmup();
        }
        scheduleFontLoad();
        return;
      }

      window.Home.show();
      scheduleFontLoad();
      scheduleEditorWarmup();
    } catch (e) {
      document.querySelectorAll(".modal-mask.open").forEach((m) => m.classList.remove("open"));
      window.Home.show();
      scheduleFontLoad();
      scheduleEditorWarmup();
      toast("会话恢复失败，已打开首页");
    }
  }

  // 欢迎窗口完整流程：展示 → 持久化所选操作风格与「不再显示」。
  // 首次启动（decideStartup）与「设置 → 欢迎页」共用同一入口。
  async function runWelcome() {
    const res = await window.Welcome.show(state.config.operation_style);
    await applyConfig({ operation_style: res.style, welcome_shown: res.dontShow });
  }

  async function restoreSession() {
    const a = apiOrToast();
    if (!a) return false;
    // 整树扫描与编辑器创建并行，避免串行把恢复路径拉得更长
    const pair = await Promise.all([a.restore_session(), ensureEditor()]);
    const res = pair[0];
    let restored = false;
    if (res && res.folder && res.folder.ok) { applyFolder(res.folder, false); restored = true; }
    if (res && res.file && res.file.ok) { await loadDoc(res.file); restored = true; }
    return restored;
  }

  // ---------------------------------------------------------------
  // 文档加载 / 保存
  // ---------------------------------------------------------------
  const scrollMem = {};
  async function loadDoc(fileRes, opts) {
    if (!(await ensureEditor())) return;
    if (state.currentPath && window.Editor.getScrollRatio) {
      scrollMem[state.currentPath] = window.Editor.getScrollRatio();
    }
    if (window.Home && window.Home.isOpen()) window.Home.hide(); // 打开文档即回编辑器
    state.currentPath = fileRes.path;
    state.lastSaved = fileRes.content;
    state.latestContent = fileRes.content;
    state.dirty = false;
    // 大文档策略：超阈值且当前是渲染模式时先切源码再写入，避免 ir 全量渲染卡死。
    // 小文档反过来：先写入再 maybeRestoreMode。若先切 ir，setMode 会把上一篇
    // 大文档带进重建，Vditor 异步 setValue(大文档) 可能盖掉随后的小文档。
    const size = fileRes.size != null ? fileRes.size : (fileRes.content || "").length;
    if (size > LARGE_DOC_BYTES) {
      if (window.Editor.getMode() === "ir") {
        state.svForced = true;
        window.Editor.setMode("sv");
        toast("文档较大（" + fmtBytes(size) + "），已用源码模式打开以保持流畅，可在右下角切回渲染模式");
      }
      window.Editor.setValue(fileRes.content);
    } else {
      window.Editor.setValue(fileRes.content);
      maybeRestoreMode();
    }
    if (fileRes.path) window.Sidebar.markActive(fileRes.path);
    updateDocName(fileRes.name || basename(fileRes.path));
    updateStatusPath();
    updateCount(fileRes.content);
    loadRecent(); // 后端已在 read_file 记录，刷新最近列表（fire-and-forget）
    refreshLinks();
    if (fileRes.mtime) state.diskMtime = fileRes.mtime;
    else rememberDiskMtime(fileRes.path);
    state.diskGone = false;
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
    const pair = await Promise.all([a.read_file(path), ensureEditor()]);
    const res = pair[0];
    if (res.ok) await loadDoc(res, opts);
    else toast("打开失败：" + (res.error || ""));
  }

  async function openPath(path) {
    const a = apiOrToast();
    if (!a) return;
    const pair = await Promise.all([a.open_path(path), ensureEditor()]);
    const res = pair[0];
    if (!res.ok) { toast("无法打开：" + (res.error || "")); return; }
    if (res.tree !== undefined) applyFolder(res, true);
    else await loadDoc(res);
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
    refreshTemplates();
  }

  async function save(opts) {
    const silent = opts && opts.silent;
    const a = apiOrToast();
    if (!a) return;
    if (!(await ensureEditor())) return;
    const content = window.Editor.getValue();
    state.latestContent = content; // 保存起点快照
    if (state.currentPath) {
      // _saving 门闩：磁盘守望在自己写盘的往返窗口内不得把 mtime 变化
      // 误判为外部修改而重载（会把阅读位置刷回顶部）
      state._saving = true;
      let res;
      try {
        res = await a.save_file(state.currentPath, content);
      } finally {
        state._saving = false;
      }
      if (res.ok) {
        markSaved(content);
        if (res.mtime) state.diskMtime = res.mtime;
        else rememberDiskMtime();
        if (!silent) toast("已保存");
        loadRecent();
      }
      else toast("保存失败：" + (res.error || ""));
    } else {
      if (silent) return; // 未保存新文档不自动弹另存对话框
      const res = await a.save_file_dialog(content, "未命名.md");
      if (res.ok) {
        state.currentPath = res.path;
        markSaved(content);
        if (res.mtime) state.diskMtime = res.mtime; // 与已保存分支一致，防守望误重载
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
    // 保存往返期间用户可能继续输入：以最新已知内容重算脏标记。
    // 直接置 false 会把保存进行中产生的新编辑错标为「已保存」，
    // 关闭时不弹确认、输入就此丢失。
    state.dirty = state.latestContent != null ? state.latestContent !== content : false;
    updateDocName();
  }

  async function refreshFolder() {
    if (!state.currentFolder) return;
    const a = api();
    if (!a) return;
    const res = await a.list_folder(state.currentFolder);
    if (res.ok) applyFolder(res, false);
    refreshTemplates();
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
    const pair = await Promise.all([a.open_path(path), ensureEditor()]);
    const res = pair[0];
    if (!res.ok) {
      toast("无法打开（可能已移动或删除）：" + basename(path));
      a.remove_recent(path).then(loadRecent);
      return;
    }
    if (res.tree !== undefined) applyFolder(res, true);
    else await loadDoc(res);
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
    state.latestContent = now;
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
      btn.addEventListener("click", async () => {
        const m = btn.dataset.mode;
        if (!(await ensureEditor())) return;
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

  function bindViewToggles() {
    on("sb-readable", () => toggleReadable());
    on("sb-focus", () => toggleFocus());
    on("sb-typewriter", () => toggleTypewriter());
  }

  function syncViewToggles() {
    const wrap = document.getElementById("editor-wrap");
    const map = {
      "sb-readable": wrap && wrap.classList.contains("readable-width"),
      "sb-focus": wrap && wrap.classList.contains("focus-mode"),
      "sb-typewriter": wrap && wrap.classList.contains("typewriter-mode"),
    };
    Object.keys(map).forEach((id) => {
      const el = document.getElementById(id);
      if (!el) return;
      el.classList.toggle("active", !!map[id]);
      el.setAttribute("aria-pressed", map[id] ? "true" : "false");
    });
  }

  function applyFocusVisual(on) {
    const side = document.getElementById("sidebar");
    const next = window.Editor.toggleFocusMode(on == null ? null : !!on);
    if (side) {
      if (next) {
        if (state._sideBeforeFocus == null) {
          state._sideBeforeFocus = side.classList.contains("collapsed");
        }
        side.classList.add("collapsed");
      } else {
        if (state._sideBeforeFocus === false) side.classList.remove("collapsed");
        state._sideBeforeFocus = null;
      }
    }
    syncViewToggles();
    return next;
  }

  function applySidebarWidth(px) {
    let w = parseInt(px, 10);
    if (isNaN(w)) w = 256;
    w = Math.max(180, Math.min(480, w));
    document.documentElement.style.setProperty("--sidebar-width", w + "px");
    return w;
  }

  function bindSidebarResize() {
    const handle = document.getElementById("sidebar-resizer");
    const side = document.getElementById("sidebar");
    if (!handle || !side) return;
    const MIN = 180, MAX = 480, DEF = 256;
    let dragging = false, startX = 0, startW = 0, onMove = null, onUp = null;

    function stopDrag(persist) {
      if (!dragging) return;
      dragging = false;
      side.classList.remove("resizing");
      if (onMove) document.removeEventListener("mousemove", onMove);
      if (onUp) document.removeEventListener("mouseup", onUp);
      onMove = onUp = null;
      if (persist) {
        const w = applySidebarWidth(side.getBoundingClientRect().width || DEF);
        applyConfig({ sidebar_width: w });
      }
    }

    handle.addEventListener("mousedown", (e) => {
      if (e.button !== 0 || side.classList.contains("collapsed")) return;
      e.preventDefault();
      dragging = true;
      startX = e.clientX;
      startW = side.getBoundingClientRect().width || DEF;
      side.classList.add("resizing");
      onMove = (ev) => {
        if (!dragging) return;
        applySidebarWidth(Math.max(MIN, Math.min(MAX, startW + (ev.clientX - startX))));
      };
      onUp = () => stopDrag(true);
      document.addEventListener("mousemove", onMove);
      document.addEventListener("mouseup", onUp);
    });
    handle.addEventListener("dblclick", (e) => {
      e.preventDefault();
      stopDrag(false);
      applySidebarWidth(DEF);
      applyConfig({ sidebar_width: DEF });
    });
  }

  function updateDocName(name) {
    const base = name || (state.currentPath ? basename(state.currentPath) : "未命名");
    docNameEl.innerHTML = esc(base) + (state.dirty ? '<span class="dirty">●</span>' : "");
  }

  // ---------------------------------------------------------------
  // 工具栏 / 快捷键
  // ---------------------------------------------------------------
  function bindToolbar() {
    on("btn-home", () => toggleHome());
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
    on("sb-path", locateInTree);

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
    startDiskWatch();
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
    const pair = await Promise.all([a.open_file_dialog(), ensureEditor()]);
    const res = pair[0];
    if (res && res.ok) await loadDoc(res);
  }

  async function newDoc() {
    if (!(await maybeConfirmDiscard())) return;
    if (!(await ensureEditor())) return;
    if (window.Home && window.Home.isOpen()) window.Home.hide();
    state.currentPath = null;
    state.lastSaved = "";
    state.latestContent = "";
    state.dirty = false;
    maybeRestoreMode();
    window.Editor.setValue("");
    window.Sidebar.markActive(null);
    updateDocName("未命名");
    updateStatusPath();
    updateCount("");
    window.Editor.focus();
  }

  function toggleHome() {
    if (window.Home && window.Home.isOpen()) {
      window.Home.hide();
      ensureEditor(); // 从首页回到编辑器：尚未预热则开始创建
    } else if (window.Home) {
      window.Home.show();
    }
  }

  async function setEditorMode(m) {
    if (!(await ensureEditor())) return;
    window.Editor.setMode(m);
    syncModeButtons(m);
    applyConfig({ display_mode: m });
  }

  async function openFindBar(opts) {
    if (!(await ensureEditor())) return;
    if (window.FindBar) window.FindBar.open(opts);
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
    if ("theme" in partial || "palette_light" in partial || "palette_dark" in partial) {
      applyTheme(state.config.theme);
    }
    if ("font_ui" in partial || "font_mono" in partial) applyFonts();
    if ("operation_style" in partial) {
      window.SlashMenu.setStyle(partial.operation_style);
      window.Editor.setOpStyle(partial.operation_style);
    }
    if ("cloud_sync" in partial) refreshCloudBadge();
    if ("editor_zoom" in partial) applyZoom(partial.editor_zoom, true);
    if ("readable_width" in partial) applyReadable(partial.readable_width, true);
    if ("focus_mode" in partial) applyFocusVisual(!!partial.focus_mode);
    if ("typewriter_mode" in partial) {
      window.Editor.toggleTypewriter(!!partial.typewriter_mode);
      syncViewToggles();
    }
    if ("sidebar_width" in partial) applySidebarWidth(partial.sidebar_width);
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
        // 分层：Palette / 查找条自处理或由此关闭 → 弹窗栈 → 首页
        if (window.Palette && window.Palette.isOpen()) return;
        if (window.FindBar && window.FindBar.isOpen()) {
          window.FindBar.close();
          return;
        }
        if (dismissEscape()) return;
        if (window.Home && window.Home.isOpen()) {
          window.Home.hide();
          ensureEditor();
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
        if (e.shiftKey) { refreshTemplates(); window.Palette.openCommands(); }
        else window.Palette.openFiles();
      }
      else if (k === "f") {
        e.preventDefault();
        if (e.shiftKey) window.Palette.openSearch();
        else openFindBar();
      }
      else if (k === "h") {
        e.preventDefault();
        if (e.shiftKey) toggleHome();
        else openFindBar({ replace: true });
      }
      else if (k === "d" && e.shiftKey) {
        e.preventDefault();
        openDailyNote();
      }
      else if (k === "b" && e.shiftKey) {
        e.preventDefault();
        document.getElementById("sidebar").classList.toggle("collapsed");
      }
      else if (k === "l" && e.shiftKey) {
        e.preventDefault();
        toggleTheme();
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
      else if (k === "g") {
        e.preventDefault();
        goToLine();
      }
    });
  }

  // ---------------------------------------------------------------
  // 主题
  // ---------------------------------------------------------------
  const LIGHT_PALETTES = ["cherry", "caramel", "forest", "mint", "sky", "prussian", "sakura", "mauve"];
  const DARK_PALETTES = ["vampire", "radiation", "abyss"];
  const FONT_UI_FALLBACK = '-apple-system, "Segoe UI", "Microsoft YaHei", system-ui, sans-serif';
  const FONT_MONO_FALLBACK = '"JetBrains Mono", Consolas, monospace';

  function themeMode() {
    return state.config.theme === "dark" ? "dark" : "light";
  }

  function currentPalette(mode) {
    if (mode === "dark") {
      const p = state.config.palette_dark || "vampire";
      return DARK_PALETTES.indexOf(p) >= 0 ? p : "vampire";
    }
    const p = state.config.palette_light || "sky";
    return LIGHT_PALETTES.indexOf(p) >= 0 ? p : "sky";
  }

  function quoteFontFamily(name) {
    const v = String(name || "").trim();
    if (!v) return "";
    if (v.indexOf(",") >= 0) return v;
    if (/^["'].*["']$/.test(v)) return v;
    if (/[^a-zA-Z0-9_-]/.test(v)) return '"' + v.replace(/"/g, "") + '"';
    return v;
  }

  function applyFonts() {
    const style = document.documentElement.style;
    const ui = ((state.config && state.config.font_ui) || "").trim();
    const mono = ((state.config && state.config.font_mono) || "").trim();
    if (ui) style.setProperty("--font-ui", quoteFontFamily(ui) + ", " + FONT_UI_FALLBACK);
    else style.removeProperty("--font-ui");
    if (mono) style.setProperty("--font-mono", quoteFontFamily(mono) + ", " + FONT_MONO_FALLBACK);
    else style.removeProperty("--font-mono");
  }

  function applyTheme(theme) {
    const mode = theme === "dark" ? "dark" : "light";
    const root = document.documentElement;
    root.setAttribute("data-theme", mode);
    root.setAttribute("data-palette", currentPalette(mode));
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
      await openFileByPath(res.path, { heading: res.heading || "" });
      return;
    }
    if (!(await window.App.confirm({
      title: "创建笔记",
      message: "笔记「" + name + "」不存在，是否创建？",
      okText: "创建",
      cancelText: "取消",
    }))) return;
    const suggested = res.suggested || "";
    const folder = dirname(suggested) || state.currentFolder || "";
    const fname = basename(suggested).replace(/\.md$/i, "");
    const created = await a.new_file(folder, fname);
    if (!created.ok) { toast(created.error || "创建失败"); return; }
    const file = await a.read_file(created.path);
    if (file.ok) await loadDoc(file);
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
    await loadDoc(res);
    refreshFolder();
  }

  async function exportHtml() {
    const a = apiOrToast();
    if (!a || !a.save_html_dialog) return;
    if (!(await ensureEditor())) return;
    const html = window.Editor.getHTML ? window.Editor.getHTML() : "";
    const name = (state.currentPath ? basename(state.currentPath).replace(/\.md$/i, "") : "导出") + ".html";
    const res = await a.save_html_dialog(html, name);
    if (res.ok) toast("已导出：" + basename(res.path));
    else if (!res.cancelled) toast("导出失败：" + (res.error || ""));
  }

  function toggleFocus() {
    const on = applyFocusVisual(null);
    applyConfig({ focus_mode: on });
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
      { id: "duplicate", title: "复制当前笔记", keys: "", group: "文件", run: duplicateCurrent },
      { id: "copy-path", title: "复制当前路径", keys: "", group: "文件", run: copyCurrentPath },
      { id: "copy-wiki", title: "复制双链", keys: "", group: "文件", run: copyCurrentWiki },
      { id: "copy-html", title: "复制为 HTML", keys: "", group: "文件", run: copyAsHtml },
      { id: "copy-md", title: "复制为 Markdown", keys: "", group: "文件", run: copyAsMarkdown },
      { id: "rename-cur", title: "重命名当前文件", keys: "", group: "文件", run: renameCurrent },
      { id: "move-cur", title: "移动当前文件", keys: "", group: "文件", run: moveCurrent },
      { id: "new-window", title: "在新窗口打开", keys: "", group: "文件", run: openCurrentNewWindow },
      { id: "capture", title: "快速收集", keys: "", group: "文件", run: captureQuick },
      { id: "quick-open", title: "快速打开笔记", keys: "Ctrl+P", group: "导航", run: () => window.Palette.openFiles() },
      { id: "search", title: "在仓库中搜索", keys: "Ctrl+Shift+F", group: "导航", run: () => window.Palette.openSearch() },
      { id: "find", title: "在本文查找", keys: "Ctrl+F", group: "导航", run: () => openFindBar() },
      { id: "replace", title: "查找替换", keys: "Ctrl+H", group: "导航", run: () => openFindBar({ replace: true }) },
      { id: "goto-line", title: "转到行", keys: "Ctrl+G", group: "导航", run: goToLine },
      { id: "locate", title: "在目录中定位当前文件", keys: "", group: "导航", run: locateInTree },
      { id: "new-here", title: "在当前文件夹新建笔记", keys: "", group: "文件", run: newNoteBeside },
      { id: "tasks", title: "仓库待办", keys: "", group: "仓库", run: () => window.Palette.openTasks() },
      { id: "tags", title: "浏览标签", keys: "", group: "仓库", run: () => window.Palette.openTags() },
      { id: "broken", title: "断开的双链", keys: "", group: "仓库", run: () => window.Palette.openBroken() },
      { id: "orphans", title: "孤立笔记", keys: "", group: "仓库", run: () => window.Palette.openOrphans() },
      { id: "mentions", title: "未链接提及", keys: "", group: "仓库", run: openMentions },
      { id: "stats", title: "仓库统计", keys: "", group: "仓库", run: showVaultStats },
      { id: "home", title: "首页", keys: "Ctrl+Shift+H", group: "导航", run: () => toggleHome() },
      { id: "sidebar", title: "切换侧栏", keys: "Ctrl+Shift+B", group: "视图", run: () => document.getElementById("sidebar").classList.toggle("collapsed") },
      { id: "theme", title: "切换主题", keys: "Ctrl+Shift+L", group: "视图", run: toggleTheme },
      { id: "focus", title: "专注模式", keys: "", group: "视图", run: toggleFocus },
      { id: "typewriter", title: "打字机模式", keys: "", group: "视图", run: toggleTypewriter },
      { id: "readable", title: "切换可读宽度", keys: "", group: "视图", run: toggleReadable },
      { id: "fold-all", title: "折叠全部目录", keys: "", group: "视图", run: () => foldTree(true) },
      { id: "unfold-all", title: "展开全部目录", keys: "", group: "视图", run: () => foldTree(false) },
      { id: "zoom-in", title: "放大编辑区", keys: "Ctrl+=", group: "视图", run: () => adjustZoom(10) },
      { id: "zoom-out", title: "缩小编辑区", keys: "Ctrl+-", group: "视图", run: () => adjustZoom(-10) },
      { id: "zoom-reset", title: "重置缩放", keys: "Ctrl+0", group: "视图", run: () => applyZoom(100) },
      { id: "ir", title: "渲染模式", keys: "", group: "视图", run: () => setEditorMode("ir") },
      { id: "sv", title: "源码模式", keys: "", group: "视图", run: () => setEditorMode("sv") },
      { id: "settings", title: "设置", keys: "", group: "应用", run: openSettings },
      { id: "cloud", title: "立即云同步", keys: "", group: "应用", run: runCloudSync },
      { id: "date", title: "插入今天日期", keys: "", group: "插入", run: () => window.Editor.insertValue(todayStamp(false)) },
      { id: "time", title: "插入当前时间", keys: "", group: "插入", run: () => window.Editor.insertValue(todayStamp(true)) },
      { id: "tpl-folder", title: "打开模板文件夹", keys: "", group: "模板", run: openTemplatesDir },
    ].concat(templateCommands());
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
    applyConfig({ typewriter_mode: on });
    syncViewToggles();
    toast(on ? "已进入打字机模式" : "已退出打字机模式");
  }

  function applyReadable(on, silent) {
    const wrap = document.getElementById("editor-wrap");
    if (!wrap) return false;
    const next = (on == null) ? !wrap.classList.contains("readable-width") : !!on;
    wrap.classList.toggle("readable-width", next);
    if (!state.config) state.config = {};
    state.config.readable_width = next;
    if (!silent && api()) api().update_config({ readable_width: next });
    syncViewToggles();
    return next;
  }

  function toggleReadable() {
    const on = applyReadable(null);
    toast(on ? "已开启可读宽度" : "已关闭可读宽度");
  }

  function foldTree(collapsed) {
    if (!window.Sidebar || !window.Sidebar.foldAll) return;
    window.Sidebar.foldAll(collapsed);
    toast(collapsed ? "已折叠全部目录" : "已展开全部目录");
  }

  async function goToLine() {
    if (!(await ensureEditor())) return;
    const md = window.Editor.getValue ? window.Editor.getValue() : "";
    const total = md ? md.split("\n").length : 1;
    const raw = await promptText({
      title: "转到行",
      sub: "共 " + total + " 行",
      value: "1",
      emptyMsg: "请输入行号",
    });
    if (raw == null) return;
    const n = parseInt(raw, 10);
    if (!n || n < 1) { toast("请输入有效行号"); return; }
    if (window.Editor.jumpToLine) window.Editor.jumpToLine(n);
  }

  function requireSavedFile() {
    if (!state.currentPath) { toast("请先保存或打开一篇笔记"); return ""; }
    return state.currentPath;
  }

  function renameCurrent() {
    const p = requireSavedFile();
    if (p) renameFileFlow(p);
  }

  function moveCurrent() {
    const p = requireSavedFile();
    if (p) moveFileFlow(p);
  }

  async function openCurrentNewWindow() {
    const a = apiOrToast();
    if (!a || !a.open_new_window) return;
    const path = state.currentPath || state.currentFolder || "";
    const res = await a.open_new_window(path);
    if (!res.ok) toast(res.error || "无法打开新窗口");
  }

  function openMentions() {
    if (!state.currentPath) { toast("请先打开一篇笔记"); return; }
    window.Palette.openMentions();
  }

  function pathsEqual(a, b) {
    if (!a || !b) return false;
    return a.replace(/\\/g, "/").toLowerCase() === b.replace(/\\/g, "/").toLowerCase();
  }

  async function captureQuick() {
    if (!state.currentFolder) { toast("请先打开仓库或文件夹"); return; }
    const text = await promptText({
      title: "快速收集",
      sub: "写入仓库根目录「收集箱.md」",
      value: "",
      maxLen: 2000,
      emptyMsg: "收集内容不能为空",
    });
    if (text == null) return;
    const a = apiOrToast();
    if (!a || !a.append_capture) return;
    const res = await a.append_capture(state.currentFolder, text);
    if (!res.ok) { toast(res.error || "收集失败"); return; }
    refreshFolder();
    if (res.path && pathsEqual(state.currentPath, res.path)) {
      if (state.dirty) {
        toast("已写入收集箱（当前文件有未保存更改，未自动重载）");
        return;
      }
      try {
        const fileRes = await a.read_file(res.path);
        if (fileRes && fileRes.ok) await loadDoc(fileRes);
      } catch (e) { /* 重载失败仍算收集成功 */ }
    }
    toast("已写入收集箱");
  }

  async function refreshTemplates() {
    const a = api();
    if (!a || !a.list_templates || !state.currentFolder) {
      state.templates = [];
      return;
    }
    try {
      const res = await a.list_templates(state.currentFolder);
      state.templates = (res && res.items) || [];
    } catch (e) {
      state.templates = [];
    }
  }

  function templateCommands() {
    const list = state.templates || [];
    const out = [];
    list.forEach((it) => {
      out.push({
        id: "tpl-ins-" + it.path,
        title: "插入模板：" + it.name,
        keys: "",
        group: "模板",
        run: () => insertTemplate(it.path),
      });
      out.push({
        id: "tpl-new-" + it.path,
        title: "从模板新建：" + it.name,
        keys: "",
        group: "模板",
        run: () => newFromTemplate(it),
      });
    });
    return out;
  }

  async function insertTemplate(path) {
    if (!(await ensureEditor())) return;
    const a = apiOrToast();
    if (!a || !a.render_template) return;
    const title = state.currentPath ? basename(state.currentPath).replace(/\.md$/i, "") : "";
    const res = await a.render_template(state.currentFolder || "", path, title);
    if (!res.ok) { toast(res.error || "读取模板失败"); return; }
    window.Editor.insertValue(res.content || "");
  }

  async function newFromTemplate(it) {
    const a = apiOrToast();
    if (!a || !a.new_from_template) return;
    const dir = state.currentFolder;
    if (!dir) { toast("请先打开仓库或文件夹"); return; }
    const name = await promptText({ title: "从模板新建", sub: it.name, value: it.name });
    if (name == null) return;
    if (!(await maybeConfirmDiscard())) return;
    const res = await a.new_from_template(dir, it.path, name);
    if (!res.ok) { toast(res.error || "创建失败"); return; }
    await loadDoc(res);
    refreshFolder();
    toast("已创建：" + (res.name || name));
  }

  async function openTemplatesDir() {
    const a = apiOrToast();
    if (!a || !a.ensure_templates_dir) return;
    const res = await a.ensure_templates_dir(state.currentFolder || "");
    if (!res.ok) { toast(res.error || "无法打开模板文件夹"); return; }
    if (a.reveal_in_explorer) await a.reveal_in_explorer(res.path);
    toast("模板目录：仓库下的「模板」文件夹，放入 .md 即可");
    refreshTemplates();
  }

  async function copyText(text, okMsg) {
    const s = String(text || "");
    if (!s) { toast("没有可复制的内容"); return; }
    try {
      if (navigator.clipboard && navigator.clipboard.writeText) {
        await navigator.clipboard.writeText(s);
      } else {
        throw new Error("no clipboard");
      }
      toast(okMsg || "已复制");
    } catch (e) {
      toast("复制失败，请手动选择复制");
    }
  }

  function copyCurrentPath() {
    if (!state.currentPath) { toast("未打开已保存的文件"); return; }
    copyText(state.currentPath, "已复制路径");
  }

  function copyCurrentWiki() {
    if (!state.currentPath) { toast("未打开已保存的文件"); return; }
    const stem = basename(state.currentPath).replace(/\.md$/i, "");
    copyText("[[" + stem + "]]", "已复制双链");
  }

  async function copyAsHtml() {
    if (!(await ensureEditor())) return;
    const html = window.Editor.getHTML ? window.Editor.getHTML() : "";
    copyText(html, "已复制 HTML");
  }

  async function copyAsMarkdown() {
    if (!(await ensureEditor())) return;
    copyText(window.Editor.getValue() || "", "已复制 Markdown");
  }

  async function duplicateCurrent() {
    const a = apiOrToast();
    if (!a || !a.duplicate_file) return;
    if (!state.currentPath) { toast("请先保存当前笔记"); return; }
    if (!(await maybeConfirmDiscard())) return;
    const res = await a.duplicate_file(state.currentPath);
    if (!res.ok) { toast(res.error || "复制失败"); return; }
    const file = await a.read_file(res.path);
    if (file.ok) await loadDoc(file);
    refreshFolder();
    toast("已复制：" + res.name);
  }

  function locateInTree() {
    if (!state.currentPath) { toast("未打开文件"); return; }
    const side = document.getElementById("sidebar");
    if (side) side.classList.remove("collapsed");
    const tab = document.querySelector('.side-tab[data-panel="files"]');
    if (tab) tab.click();
    const ok = window.Sidebar.revealPath && window.Sidebar.revealPath(state.currentPath);
    if (!ok) toast("当前文件不在目录树中");
  }

  function newNoteBeside() {
    const dir = state.currentPath ? dirname(state.currentPath) : state.currentFolder;
    newNoteInFolder(dir);
  }

  async function showVaultStats() {
    const a = apiOrToast();
    if (!a || !a.vault_stats) return;
    const res = await a.vault_stats(state.currentFolder || "");
    if (!res.ok) { toast(res.error || "统计失败"); return; }
    const n = (v, trunc) => String(v == null ? 0 : v) + (trunc ? "+" : "");
    toast(
      n(res.files, false) + " 篇 · "
      + n(res.tasks, res.tasks_truncated) + " 待办 · "
      + n(res.tags, res.tags_truncated) + " 标签 · "
      + n(res.broken, res.broken_truncated) + " 断链"
    );
  }

  async function rememberDiskMtime(path) {
    const p = path || state.currentPath;
    const a = api();
    if (!a || !a.file_stat || !p) { state.diskMtime = 0; return; }
    try {
      const res = await a.file_stat(p);
      state.diskMtime = res && res.ok ? res.mtime : 0;
    } catch (e) {
      state.diskMtime = 0;
    }
  }

  function startDiskWatch() {
    if (startDiskWatch._t) return;
    startDiskWatch._t = setInterval(checkDiskChange, 2500);
  }

  async function checkDiskChange() {
    if (!state.currentPath || state.dirty || state._saving) return;
    const a = api();
    if (!a || !a.file_stat) return;
    let res;
    try { res = await a.file_stat(state.currentPath); } catch (e) { return; }
    if (!res || !res.exists) {
      if (!state.diskGone) {
        state.diskGone = true;
        toast("磁盘上的文件已不存在");
      }
      return;
    }
    state.diskGone = false;
    if (state.diskMtime && res.mtime > state.diskMtime + 0.4) {
      const file = await a.read_file(state.currentPath);
      if (file && file.ok && !state.dirty) {
        await loadDoc(file);
        toast("文件已从磁盘重新载入");
      }
    } else if (res.mtime) {
      state.diskMtime = res.mtime;
    }
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
    if (file.ok) await loadDoc(file);
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
  function promptText({ title, sub, value, maxLen, emptyMsg }) {
    const mask = document.getElementById("input-modal-mask");
    return new Promise((resolve) => {
      mask.innerHTML = `
        <div class="modal mini-modal" role="dialog" aria-modal="true" aria-label="${esc(title)}">
          <div class="modal-head">
            <span class="badge">${window.ICONS.edit}</span>
            <div><h2>${esc(title)}</h2><p>${esc(sub || "")}</p></div>
          </div>
          <div class="modal-body">
            <input class="mm-input" id="pm-input" maxlength="${maxLen || 120}" />
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
        unregisterEscape(onEsc);
        mask.removeEventListener("click", onMask);
        mask.classList.remove("open");
        mask.innerHTML = "";
        resolve(val);
      }
      function onEsc() { done(null); }
      function onMask(e) { if (e.target === mask) done(null); }
      registerEscape(onEsc);
      mask.addEventListener("click", onMask);
      document.getElementById("pm-ok").addEventListener("click", () => {
        const v = input.value.trim();
        if (!v) { toast(emptyMsg || "名称不能为空"); return; }
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
    if (!(await window.App.confirm({
      title: "移入回收站",
      message: "确定把「" + basename(path) + "」移入回收站？\n可从系统回收站恢复。",
      okText: "移入回收站",
      cancelText: "取消",
    }))) return;
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
    return window.App.confirm({
      title: "放弃更改",
      message: "当前文档有未保存的更改，是否放弃？",
      okText: "放弃",
      cancelText: "取消",
    });
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

  // Esc 弹窗栈：后开先关，由 bindShortcuts 统一弹出，避免各弹窗抢 window.keydown
  const escapeStack = [];
  function registerEscape(fn) {
    if (typeof fn !== "function") return;
    unregisterEscape(fn);
    escapeStack.push(fn);
  }
  function unregisterEscape(fn) {
    const i = escapeStack.lastIndexOf(fn);
    if (i >= 0) escapeStack.splice(i, 1);
  }
  function dismissEscape() {
    if (!escapeStack.length) return false;
    const fn = escapeStack.pop();
    if (fn) fn();
    return true;
  }

  let confirmPending = null;
  function confirmDialog(opts) {
    opts = opts || {};
    const title = opts.title || "确认";
    const message = opts.message || "";
    const okText = opts.okText || "确定";
    const cancelText = opts.cancelText || "取消";
    const mask = document.getElementById("confirm-mask");
    if (!mask) return Promise.resolve(false);
    if (confirmPending) confirmPending(false);
    return new Promise((resolve) => {
      mask.innerHTML = `
        <div class="modal mini-modal" role="dialog" aria-modal="true" aria-label="${esc(title)}">
          <div class="modal-head">
            <span class="badge">${window.ICONS.gear}</span>
            <div><h2>${esc(title)}</h2></div>
          </div>
          <div class="modal-body">
            <p class="confirm-msg">${esc(message)}</p>
          </div>
          <div class="modal-foot">
            <button class="btn btn-ghost" id="cf-cancel">${esc(cancelText)}</button>
            <button class="btn btn-primary" id="cf-ok">${esc(okText)}</button>
          </div>
        </div>`;
      mask.classList.add("open");
      let settled = false;
      function done(ok) {
        if (settled) return;
        settled = true;
        if (confirmPending === done) confirmPending = null;
        unregisterEscape(onEsc);
        mask.removeEventListener("click", onMask);
        mask.classList.remove("open");
        mask.innerHTML = "";
        resolve(!!ok);
      }
      function onEsc() { done(false); }
      function onMask(e) { if (e.target === mask) done(false); }
      confirmPending = done;
      registerEscape(onEsc);
      mask.addEventListener("click", onMask);
      document.getElementById("cf-cancel").addEventListener("click", () => done(false));
      const okBtn = document.getElementById("cf-ok");
      okBtn.addEventListener("click", () => done(true));
      okBtn.addEventListener("keydown", (e) => {
        if (e.key === "Enter") { e.preventDefault(); e.stopPropagation(); done(true); }
      });
      okBtn.focus();
    });
  }

  const TOAST_MAX = 3;
  const toastItems = [];
  function toast(msg, opts) {
    opts = opts || {};
    const host = document.getElementById("toast");
    if (!host) return;
    let type = opts.type;
    if (!type) type = /失败/.test(String(msg || "")) ? "error" : "info";
    const duration = opts.duration != null ? opts.duration : (type === "error" ? 4000 : 2200);
    while (toastItems.length >= TOAST_MAX) removeToast(toastItems[0]);
    const el = document.createElement("div");
    el.className = "toast-item toast-" + type;
    el.textContent = String(msg == null ? "" : msg);
    host.appendChild(el);
    const item = { el, timer: null };
    toastItems.push(item);
    requestAnimationFrame(() => el.classList.add("show"));
    item.timer = setTimeout(() => removeToast(item), duration);
  }
  function removeToast(item) {
    const i = toastItems.indexOf(item);
    if (i < 0) return;
    toastItems.splice(i, 1);
    clearTimeout(item.timer);
    item.el.classList.remove("show");
    setTimeout(() => {
      if (item.el.parentNode) item.el.parentNode.removeChild(item.el);
    }, 260);
  }

  window.App = {
    openFolder,
    openFile,
    openPath,
    ensureEditor,
    save,
    toast,
    confirm: confirmDialog,
    registerEscape,
    unregisterEscape,
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
    applyReadable,
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
