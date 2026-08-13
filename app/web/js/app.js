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
    });
  }

  // 后端不可用时的最小配置（welcome_shown=true：异常时不再弹欢迎窗添乱）
  function defaultConfig() {
    return { theme: "light", operation_style: "notion", display_mode: "ir", welcome_shown: true };
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
  function loadDoc(fileRes) {
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
  }

  // 离开被强制源码模式的大文档时，恢复用户偏好的渲染模式
  function maybeRestoreMode() {
    if (!state.svForced) return;
    state.svForced = false;
    if (state.config.display_mode !== "sv") window.Editor.setMode("ir");
  }

  async function openFileByPath(path) {
    if (!(await maybeConfirmDiscard())) return;
    const a = apiOrToast();
    if (!a) return;
    const res = await a.read_file(path);
    if (res.ok) loadDoc(res);
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

  async function save() {
    const a = apiOrToast();
    if (!a) return;
    const content = window.Editor.getValue();
    if (state.currentPath) {
      const res = await a.save_file(state.currentPath, content);
      if (res.ok) { markSaved(content); toast("已保存"); loadRecent(); }
      else toast("保存失败：" + (res.error || ""));
    } else {
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
  function onEditorChange(now) {
    if (now == null) now = window.Editor.getValue();
    state.dirty = now !== state.lastSaved;
    updateDocName();
    updateCount(now);
  }

  function updateCount(text) {
    const t = text == null ? window.Editor.getValue() : text;
    const chars = (t || "").replace(/\s/g, "").length;
    const el = document.getElementById("sb-count");
    if (el) el.textContent = chars + " 字";
  }

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
    on("btn-theme", toggleTheme);
    on("btn-settings", openSettings);
    on("btn-clear-recent", clearRecent);

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
    if (a) await a.update_config(partial);
    if ("theme" in partial) applyTheme(partial.theme);
    if ("operation_style" in partial) {
      window.SlashMenu.setStyle(partial.operation_style);
      window.Editor.setOpStyle(partial.operation_style);
    }
  }

  function bindShortcuts() {
    window.addEventListener("keydown", (e) => {
      // Esc 关闭首页（弹窗打开时让弹窗自己处理）
      if (e.key === "Escape" && window.Home.isOpen()
          && !document.querySelector(".modal-mask.open")) {
        window.Home.hide();
        return;
      }
      const mod = e.ctrlKey || e.metaKey;
      if (!mod) return;
      const k = e.key.toLowerCase();
      if (k === "s") { e.preventDefault(); save(); }
      else if (k === "o") { e.preventDefault(); openFile(); }
      else if (k === "n") { e.preventDefault(); newDoc(); }
      else if (k === "b" && e.shiftKey) {
        e.preventDefault();
        document.getElementById("sidebar").classList.toggle("collapsed");
      }
      else if (k === "h" && e.shiftKey) {
        e.preventDefault();
        window.Home.toggle();
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
