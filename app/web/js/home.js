/* 首页：问候 + 写/打开 + 仓库列表 + 最近打开 + 随手记。
   仓库数据来自后端 ProjectStore（config["projects"]），视图偏好存 config.home_view。
   点击一律容器级事件委托，与侧栏同一模式。 */
(function () {
  const api = () => window.pywebview && window.pywebview.api;

  const homeEl = document.getElementById("home");
  const repoWrap = document.getElementById("repo-container");
  const repoEmpty = document.getElementById("repo-empty");
  const repoCount = document.getElementById("repo-count");
  const recentWrap = document.getElementById("home-recent");
  const recentEmpty = document.getElementById("home-recent-empty");
  const modalMask = document.getElementById("home-modal-mask");

  let handlers = {};   // { getConfig, applyConfig, openFolderResult, openPath, newDoc, openFileDialog, toast }
  let repoItems = [];  // 最近一次 list_projects 结果
  let view = "card";   // card | list
  let modalReturnFocus = null;

  // ---------------------------------------------------------------
  // 初始化 / 显示控制
  // ---------------------------------------------------------------
  function init(h) {
    handlers = h || {};
    view = (handlers.getConfig && handlers.getConfig().home_view) === "list" ? "list" : "card";
    bindStatic();
    syncViewButtons();
  }

  function show() {
    renderGreeting();
    refresh();
    homeEl.classList.add("show");
    syncHomeButton(true);
    // 首页态 AI 按钮隐藏，侧栏一并收起，保持 chrome 一致
    if (window.AiPanel && window.AiPanel.close) window.AiPanel.close();
  }

  function hide() {
    homeEl.classList.remove("show");
    syncHomeButton(false);
  }

  function isOpen() {
    return homeEl.classList.contains("show");
  }

  function toggle() {
    if (isOpen()) hide();
    else show();
  }

  function syncHomeButton(active) {
    const btn = document.getElementById("btn-home");
    if (btn) btn.classList.toggle("active", active);
    // 首页态工具栏只留全局动作（显隐规则见 app.css body.is-home）
    document.body.classList.toggle("is-home", active);
  }

  // ---------------------------------------------------------------
  // 数据加载
  // ---------------------------------------------------------------
  async function fetchHomeData(a) {
    return Promise.all([
      a.list_projects(),
      a.get_recent(),
      a.read_scratch(),
    ]);
  }

  async function refresh() {
    const a = api();
    if (!a || typeof a.list_projects !== "function") return;
    repoWrap.setAttribute("aria-busy", "true");
    recentWrap.setAttribute("aria-busy", "true");
    try {
      let pair;
      try {
        pair = await fetchHomeData(a);
      } catch (e) {
        await new Promise((r) => setTimeout(r, 120));
        pair = await fetchHomeData(a);
      }
      const [repos, recent, scratch] = pair;
      if (repos && repos.ok) renderRepos(repos.items);
      if (recent && recent.ok) renderRecent(recent.items);
      const scratchEl = document.getElementById("home-scratch-input");
      if (scratchEl && scratch && scratch.ok) scratchEl.value = scratch.content || "";
    } catch (e) {
      toast("首页数据加载失败，请稍后重试", { type: "error" });
    } finally {
      repoWrap.removeAttribute("aria-busy");
      recentWrap.removeAttribute("aria-busy");
    }
  }

  // ---------------------------------------------------------------
  // 仓库渲染
  // ---------------------------------------------------------------
  function renderRepos(items) {
    repoItems = items || [];
    repoCount.textContent = repoItems.length ? String(repoItems.length) : "";
    if (!repoItems.length) {
      repoEmpty.style.display = "block";
      repoWrap.innerHTML = "";
      return;
    }
    repoEmpty.style.display = "none";
    repoWrap.className = view === "card" ? "repo-grid" : "repo-list";
    repoWrap.innerHTML = repoItems
      .map((r) => (view === "card" ? cardHTML(r) : rowHTML(r)))
      .join("");
  }

  function actionsHTML(r) {
    return `
      <div class="repo-actions">
        <button data-act="pin" class="${r.pinned ? "pinned" : ""}" title="${r.pinned ? "取消置顶" : "置顶"}" aria-label="${r.pinned ? "取消置顶" : "置顶"}">${window.ICONS.pin}</button>
        <button data-act="cloud" class="${cloudOn(r) ? "pinned" : ""}" title="${cloudOn(r) ? "关闭此仓库云同步" : "为此仓库开启云同步"}" aria-label="${cloudOn(r) ? "关闭此仓库云同步" : "为此仓库开启云同步"}">${window.ICONS.cloud}</button>
        <button data-act="new-window" title="新窗口打开" aria-label="新窗口打开">${window.ICONS.newWindow}</button>
        <button data-act="rename" title="重命名" aria-label="重命名仓库">${window.ICONS.edit}</button>
        <button data-act="reveal" title="在资源管理器中显示" aria-label="在资源管理器中显示">${window.ICONS.folderOpen}</button>
        <button data-act="remove" class="danger" title="从列表移除（不删除文件）" aria-label="从仓库列表移除">${window.ICONS.trash}</button>
      </div>`;
  }

  function metaHTML(r) {
    const parts = [];
    if (!r.exists) parts.push('<span class="repo-missing-tag">目录不存在</span>');
    else if (r.md_count != null)
      parts.push(`<span>${r.md_count}${r.md_count_capped ? "+" : ""} 篇</span>`);
    if (cloudOn(r)) parts.push('<span class="dot">·</span>', "<span>云同步</span>");
    const t = relTime(r.last_opened_at);
    if (t) parts.push('<span class="dot">·</span>', `<span>${t}</span>`);
    return parts.join("");
  }

  function nameHTML(r) {
    const pin = r.pinned ? `<span class="pin-flag">${window.ICONS.pin}</span>` : "";
    const cloud = cloudOn(r) ? `<span class="pin-flag">${window.ICONS.cloud}</span>` : "";
    return `${pin}${cloud}${esc(r.name)}`;
  }

  function cloudCfg() {
    return (handlers.getConfig && handlers.getConfig().cloud_sync) || {};
  }

  function cloudOn(r) {
    const cs = cloudCfg();
    if (!cs.enabled) return false;
    return !!(cs.sync_all_projects || r.cloud_enabled);
  }

  /* 展示路径：末段与仓库名同名时去掉（名字就在旁边，避免一行两处），
     过长时中段折叠为 盘符\…\末段，头尾都保留语义 */
  function dispPath(r) {
    let p = String(r.path || "");
    const name = String(r.name || "");
    const segs = p.split(/[\\/]/).filter(Boolean);
    if (name && segs.length > 1 && segs[segs.length - 1].toLowerCase() === name.toLowerCase()) segs.pop();
    p = segs.join("\\");
    if (p.length <= 46) return p;
    const head = segs.slice(0, 2).join("\\");
    const tail = segs[segs.length - 1] || "";
    return head + "\\…\\" + tail;
  }

  function cardHTML(r) {
    return `
      <div class="repo-card hover-lift press${r.exists ? "" : " missing"}" data-id="${esc(r.id)}">
        <button class="repo-open" type="button" aria-label="打开仓库：${esc(r.name)}">
          <span class="repo-card-top">
            <span class="repo-avatar">${esc(initial(r.name))}</span>
          </span>
          <span class="repo-name" title="${esc(r.name)}">${nameHTML(r)}</span>
          <span class="repo-path" title="${esc(r.path)}">${esc(dispPath(r))}</span>
          <span class="repo-meta">${metaHTML(r)}</span>
        </button>
        ${actionsHTML(r)}
      </div>`;
  }

  function rowHTML(r) {
    return `
      <div class="repo-row${r.exists ? "" : " missing"}" data-id="${esc(r.id)}">
        <button class="repo-open repo-open-row" type="button" aria-label="打开仓库：${esc(r.name)}">
          <span class="repo-avatar">${esc(initial(r.name))}</span>
          <span class="repo-name" title="${esc(r.name)}">${nameHTML(r)}</span>
          <span class="repo-path" title="${esc(r.path)}">${esc(dispPath(r))}</span>
          <span class="repo-meta">${metaHTML(r)}</span>
        </button>
        ${actionsHTML(r)}
      </div>`;
  }

  // ---------------------------------------------------------------
  // 最近打开（最近 3 个文件单独成卡，其余归入「其他」）
  // ---------------------------------------------------------------
  function renderRecent(items) {
    items = items || [];
    const clearBtn = document.getElementById("home-clear-recent");
    if (clearBtn) {
      clearBtn.hidden = !items.length;
      clearBtn.style.display = items.length ? "" : "none";
    }
    if (!items.length) {
      recentEmpty.style.display = "block";
      recentWrap.innerHTML = "";
      return;
    }
    recentEmpty.style.display = "none";
    const files = items.filter((it) => it.kind !== "folder").slice(0, 3);
    const others = items.filter((it) => files.indexOf(it) < 0);
    let html = "";
    if (files.length) {
      html += '<div class="recent-group-label">最近文件</div>'
        + '<div class="recent-files">'
        + files.map(fileCardHTML).join("")
        + "</div>";
    }
    if (others.length) {
      // 两组并存才标「其他」；只有一组时不贴标签，减少视觉噪声
      if (files.length) html += '<div class="recent-group-label">其他</div>';
      html += others.map(recentRowHTML).join("");
    }
    recentWrap.innerHTML = html;
  }

  function fileCardHTML(it) {
    return `
      <button class="recent-file-card hover-lift press${it.exists ? "" : " missing"}" type="button" data-path="${esc(it.path)}" title="${esc(it.path)}" aria-label="打开最近文件：${esc(it.name)}">
        <span class="rf-icon">${window.ICONS.fileText}</span>
        <span class="rf-name">${esc(it.name)}</span>
        <span class="rf-path">${esc(it.path)}</span>
      </button>`;
  }

  function recentRowHTML(it) {
    return `
      <div class="recent-row${it.exists ? "" : " missing"}" data-path="${esc(it.path)}" title="${esc(it.path)}">
        <button class="recent-open" type="button" aria-label="打开最近${it.kind === "folder" ? "文件夹" : "文件"}：${esc(it.name)}">
          <span class="rr-icon">${it.kind === "folder" ? window.ICONS.folder : window.ICONS.fileText}</span>
          <span class="rr-name">${esc(it.name)}</span>
          <span class="rr-path">${esc(it.path)}</span>
        </button>
        ${it.kind === "folder" && it.exists ? `<button class="rr-save" title="保存为仓库" aria-label="将 ${esc(it.name)} 保存为仓库">${window.ICONS.plus}存为仓库</button>` : ""}
      </div>`;
  }

  // ---------------------------------------------------------------
  // 事件（容器级委托，仅绑定一次）
  // ---------------------------------------------------------------
  function bindStatic() {
    // 快速操作
    on("hq-new", () => { handlers.newDoc && handlers.newDoc(); });
    on("hq-open-file", () => handlers.openFileDialog && handlers.openFileDialog());
    on("hq-add-repo", () => { closeRepoMenu(); addRepo(); });
    on("hq-create-repo", () => { closeRepoMenu(); openCreateModal("repo"); });
    on("hq-create-folder", () => { closeRepoMenu(); openCreateModal("folder"); });
    on("hq-tutorial", () => { closeRepoMenu(); handlers.openTutorial && handlers.openTutorial(); });
    on("repo-empty-add", addRepo);
    on("home-scratch-save", saveScratch);
    const plus = document.getElementById("home-repo-plus");
    if (plus) plus.addEventListener("click", (e) => { e.stopPropagation(); toggleRepoMenu(); });
    document.addEventListener("click", (e) => {
      const wrap = document.querySelector(".home-repo-add");
      if (wrap && !wrap.contains(e.target)) closeRepoMenu();
    });

    // 视图切换
    document.querySelectorAll("#repo-view-toggle button").forEach((btn) => {
      btn.addEventListener("click", () => {
        if (btn.dataset.view === view) return;
        view = btn.dataset.view;
        syncViewButtons();
        renderRepos(repoItems);
        handlers.applyConfig && handlers.applyConfig({ home_view: view });
      });
    });

    // 仓库容器：卡片/行点击与操作按钮
    repoWrap.addEventListener("click", (e) => {
      const actBtn = e.target.closest(".repo-actions button");
      const item = e.target.closest("[data-id]");
      if (!item) return;
      const id = item.dataset.id;
      if (actBtn) {
        e.stopPropagation();
        handleAction(actBtn.dataset.act, id);
        return;
      }
      openRepo(id);
    });

    // 最近列表（文件卡片与「其他」行共用同一委托）
    recentWrap.addEventListener("click", async (e) => {
      const row = e.target.closest(".recent-row, .recent-file-card");
      if (!row) return;
      const saveBtn = e.target.closest(".rr-save");
      if (saveBtn) {
        e.stopPropagation();
        const res = await api().add_project(row.dataset.path, "");
        if (res && res.ok) {
          toast(res.existed ? "该目录已在仓库列表中" : "已保存为仓库");
          refresh();
        } else toast("保存失败：" + ((res && res.error) || ""), { type: "error" });
        return;
      }
      handlers.openPath && handlers.openPath(row.dataset.path);
    });

    on("home-clear-recent", async () => {
      const a = api();
      if (!a) return;
      await a.clear_recent();
      refresh();
    });

    // 重命名弹窗遮罩点击关闭
    modalMask.addEventListener("click", (e) => {
      if (e.target === modalMask) closeModal();
    });
  }

  async function openRepo(id) {
    const a = api();
    if (!a) return;
    const res = await a.open_project(id);
    if (!res.ok) {
      toast("无法打开仓库：" + (res.error || ""));
      return;
    }
    hide();
    if (handlers.openFolderResult) await handlers.openFolderResult(res);
  }

  async function addRepo() {
    const a = api();
    if (!a) return;
    const res = await a.add_project_dialog();
    if (res && res.ok) {
      toast(res.existed ? "该目录已在仓库列表中" : "已添加仓库：" + res.project.name);
      refresh();
    } else if (res && !res.cancelled) {
      toast("添加失败：" + (res.error || ""), { type: "error" });
    }
  }

  async function handleAction(act, id) {
    const a = api();
    if (!a) return;
    const repo = repoItems.find((r) => r.id === id);
    if (!repo) return;

    if (act === "pin") {
      await a.pin_project(id, !repo.pinned);
      refresh();
    } else if (act === "cloud") {
      const cs = cloudCfg();
      if (!cs.enabled) { toast("请先在设置中启用并配置云同步"); return; }
      if (cs.sync_all_projects) { toast("已勾选「同步全部仓库」，无需逐个开启"); return; }
      const next = !repo.cloud_enabled;
      await a.set_project_cloud(id, next);
      toast(next ? "已开启该仓库云同步" : "已关闭该仓库云同步");
      refresh();
    } else if (act === "new-window") {
      const res = await a.open_project_new_window(id);
      if (!res.ok) toast("打开失败：" + (res.error || ""), { type: "error" });
    } else if (act === "rename") {
      openRenameModal(repo);
    } else if (act === "reveal") {
      const res = await a.reveal_in_explorer(repo.path);
      if (!res.ok) toast(res.error || "无法打开资源管理器");
    } else if (act === "remove") {
      const ok = window.App && window.App.confirm
        ? await window.App.confirm({
            title: "移除仓库",
            message: `把「${repo.name}」从仓库列表移除？\n（仅移除记录，不会删除磁盘文件）`,
            okText: "移除",
            cancelText: "取消",
          })
        : window.confirm(`把「${repo.name}」从仓库列表移除？\n（仅移除记录，不会删除磁盘文件）`);
      if (!ok) return;
      await a.remove_project(id);
      toast("已移除");
      refresh();
    }
  }

  function toggleRepoMenu() {
    const menu = document.getElementById("home-repo-menu");
    const plus = document.getElementById("home-repo-plus");
    if (!menu) return;
    const next = menu.hidden;
    menu.hidden = !next;
    if (plus) plus.setAttribute("aria-expanded", next ? "true" : "false");
  }

  function closeRepoMenu() {
    const menu = document.getElementById("home-repo-menu");
    const plus = document.getElementById("home-repo-plus");
    if (menu) menu.hidden = true;
    if (plus) plus.setAttribute("aria-expanded", "false");
  }

  async function saveScratch() {
    const a = api();
    const el = document.getElementById("home-scratch-input");
    if (!a || !el) return;
    const res = await a.write_scratch(el.value);
    if (res && res.ok) toast("随手记已保存");
    else toast("保存失败：" + ((res && res.error) || ""), { type: "error" });
  }

  function openCreateModal(kind) {
    if (isModalOpen()) closeModal();
    modalReturnFocus = document.activeElement;
    const isRepo = kind === "repo";
    modalMask.innerHTML = `
      <div class="modal mini-modal" role="dialog" aria-modal="true" aria-label="${isRepo ? "新建仓库" : "新建文件夹"}">
        <div class="modal-head">
          <span class="badge">${window.ICONS.folder}</span>
          <div><h2>${isRepo ? "新建仓库" : "新建文件夹"}</h2><p>填写父目录与名称，会在磁盘上创建目录${isRepo ? "并登记为仓库" : ""}</p></div>
        </div>
        <div class="modal-body">
          <div class="mm-path-row">
            <input class="mm-input" id="mm-parent" aria-label="父目录" placeholder="父目录完整路径" spellcheck="false" />
            <button type="button" class="btn" id="mm-browse">浏览</button>
          </div>
          <input class="mm-input" id="mm-new-name" aria-label="名称" placeholder="${isRepo ? "仓库名称" : "文件夹名称"}" maxlength="60" style="margin-top:8px" />
        </div>
        <div class="modal-foot">
          <button class="btn btn--text" id="mm-cancel">取消</button>
          <button class="btn btn--primary press" id="mm-ok">创建</button>
        </div>
      </div>`;
    modalMask.classList.add("open");
    if (window.App && window.App.registerEscape) window.App.registerEscape(closeModal);
    const parentEl = document.getElementById("mm-parent");
    const nameEl = document.getElementById("mm-new-name");
    const cfg = handlers.getConfig && handlers.getConfig();
    if (cfg && cfg.last_folder) parentEl.value = cfg.last_folder;
    parentEl.focus();
    parentEl.addEventListener("keydown", (e) => {
      if (e.key === "Enter") { e.preventDefault(); nameEl.focus(); }
    });
    const browseBtn = document.getElementById("mm-browse");
    if (browseBtn) {
      browseBtn.addEventListener("click", async () => {
        const a = api();
        if (!a || !a.open_folder_dialog) return;
        const picked = await a.open_folder_dialog();
        if (picked && picked.ok && picked.root) parentEl.value = picked.root;
      });
    }
    const submit = async () => {
      const parent = parentEl.value.trim();
      const name = nameEl.value.trim();
      if (!parent || !name) { toast("请填写父目录和名称"); return; }
      const res = isRepo
        ? await api().create_project(parent, name)
        : await api().create_directory(parent, name);
      if (res && res.ok) {
        closeModal();
        toast(isRepo ? "已新建仓库：" + ((res.project && res.project.name) || name) : "已新建文件夹");
        refresh();
      } else toast("创建失败：" + ((res && res.error) || ""), { type: "error" });
    };
    document.getElementById("mm-ok").addEventListener("click", submit);
    document.getElementById("mm-cancel").addEventListener("click", closeModal);
    nameEl.addEventListener("keydown", (e) => {
      if (e.key === "Enter") submit();
      else if (e.key === "Escape") { e.stopPropagation(); closeModal(); }
    });
  }

  // ---------------------------------------------------------------
  // 重命名小弹窗
  // ---------------------------------------------------------------
  function openRenameModal(repo) {
    if (isModalOpen()) closeModal();
    modalReturnFocus = document.activeElement;
    modalMask.innerHTML = `
      <div class="modal mini-modal" role="dialog" aria-modal="true" aria-label="重命名仓库">
        <div class="modal-head">
          <span class="badge">${window.ICONS.edit}</span>
          <div><h2>重命名仓库</h2><p>${esc(repo.path)}</p></div>
        </div>
        <div class="modal-body">
          <input class="mm-input" id="mm-name" aria-label="仓库名称" value="${esc(repo.name)}" maxlength="60" />
        </div>
        <div class="modal-foot">
          <button class="btn btn--text" id="mm-cancel">取消</button>
          <button class="btn btn--primary" id="mm-ok">确定</button>
        </div>
      </div>`;
    modalMask.classList.add("open");
    if (window.App && window.App.registerEscape) window.App.registerEscape(closeModal);
    const input = document.getElementById("mm-name");
    input.focus();
    input.select();

    const submit = async () => {
      const name = input.value.trim();
      if (!name) { toast("名称不能为空"); return; }
      const res = await api().rename_project(repo.id, name);
      if (res && res.ok) { closeModal(); refresh(); }
      else toast("重命名失败：" + ((res && res.error) || ""), { type: "error" });
    };
    document.getElementById("mm-ok").addEventListener("click", submit);
    document.getElementById("mm-cancel").addEventListener("click", closeModal);
    input.addEventListener("keydown", (e) => {
      if (e.key === "Enter") submit();
      else if (e.key === "Escape") { e.stopPropagation(); closeModal(); }  // 防冒泡触发首页 Esc 关闭
    });
  }

  function isModalOpen() {
    return modalMask.classList.contains("open");
  }

  function closeModal() {
    if (window.App && window.App.unregisterEscape) window.App.unregisterEscape(closeModal);
    modalMask.classList.remove("open");
    modalMask.innerHTML = "";
    if (modalReturnFocus && typeof modalReturnFocus.focus === "function") {
      try { modalReturnFocus.focus(); } catch (e) { /* 触发节点可能已被列表刷新替换 */ }
    }
    modalReturnFocus = null;
  }

  // ---------------------------------------------------------------
  // 工具
  // ---------------------------------------------------------------
  function renderGreeting() {
    const h = new Date().getHours();
    const word =
      h < 5 ? "夜深了" : h < 9 ? "早上好" : h < 12 ? "上午好" :
      h < 14 ? "中午好" : h < 18 ? "下午好" : h < 23 ? "晚上好" : "夜深了";
    const el = document.getElementById("home-greet");
    if (el) el.textContent = word;
  }

  function syncViewButtons() {
    document.querySelectorAll("#repo-view-toggle button").forEach((b) => {
      const active = b.dataset.view === view;
      b.classList.toggle("active", active);
      b.setAttribute("aria-pressed", active ? "true" : "false");
    });
  }

  function relTime(ts) {
    if (!ts) return "尚未打开";
    const diff = Math.floor(Date.now() / 1000) - ts;
    if (diff < 60) return "刚刚打开";
    if (diff < 3600) return Math.floor(diff / 60) + " 分钟前";
    if (diff < 86400) return Math.floor(diff / 3600) + " 小时前";
    if (diff < 86400 * 30) return Math.floor(diff / 86400) + " 天前";
    const d = new Date(ts * 1000);
    return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
  }

  function initial(name) {
    return (name || "?").trim().charAt(0).toUpperCase() || "?";
  }

  function esc(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function toast(msg, opts) {
    if (handlers.toast) handlers.toast(msg, opts);
  }

  function on(id, fn) {
    const el = document.getElementById(id);
    if (el) el.addEventListener("click", fn);
  }

  window.Home = { init, show, hide, toggle, isOpen, refresh, isModalOpen, closeModal };
})();
