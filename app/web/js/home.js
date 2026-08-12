/* 首页：仓库列表（卡片/列表视图）+ 快速操作 + 最近打开。
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
  }

  // ---------------------------------------------------------------
  // 数据加载
  // ---------------------------------------------------------------
  async function refresh() {
    const a = api();
    if (!a) return;
    try {
      const [repos, recent] = await Promise.all([a.list_projects(), a.get_recent()]);
      if (repos && repos.ok) renderRepos(repos.items);
      if (recent && recent.ok) renderRecent(recent.items);
    } catch (e) {
      toast("首页数据加载失败");
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
        <button data-act="pin" class="${r.pinned ? "pinned" : ""}" title="${r.pinned ? "取消置顶" : "置顶"}">${window.ICONS.pin}</button>
        <button data-act="new-window" title="新窗口打开">${window.ICONS.newWindow}</button>
        <button data-act="rename" title="重命名">${window.ICONS.edit}</button>
        <button data-act="reveal" title="在资源管理器中显示">${window.ICONS.folderOpen}</button>
        <button data-act="remove" class="danger" title="从列表移除（不删除文件）">${window.ICONS.trash}</button>
      </div>`;
  }

  function metaHTML(r) {
    const parts = [];
    if (!r.exists) parts.push('<span class="repo-missing-tag">目录不存在</span>');
    else if (r.md_count != null)
      parts.push(`<span>${r.md_count}${r.md_count_capped ? "+" : ""} 篇</span>`);
    const t = relTime(r.last_opened_at);
    if (t) parts.push('<span class="dot">·</span>', `<span>${t}</span>`);
    return parts.join("");
  }

  function nameHTML(r) {
    const pin = r.pinned ? `<span class="pin-flag">${window.ICONS.pin}</span>` : "";
    return `${pin}${esc(r.name)}`;
  }

  function cardHTML(r) {
    return `
      <div class="repo-card${r.exists ? "" : " missing"}" data-id="${esc(r.id)}">
        <div class="repo-card-top">
          <span class="repo-avatar" style="--repo-hue:${hue(r.path)}">${esc(initial(r.name))}</span>
        </div>
        <div class="repo-name" title="${esc(r.name)}">${nameHTML(r)}</div>
        <div class="repo-path" title="${esc(r.path)}">${esc(r.path)}</div>
        <div class="repo-meta">${metaHTML(r)}</div>
        ${actionsHTML(r)}
      </div>`;
  }

  function rowHTML(r) {
    return `
      <div class="repo-row${r.exists ? "" : " missing"}" data-id="${esc(r.id)}">
        <span class="repo-avatar" style="--repo-hue:${hue(r.path)}">${esc(initial(r.name))}</span>
        <span class="repo-name" title="${esc(r.name)}">${nameHTML(r)}</span>
        <span class="repo-path" title="${esc(r.path)}">${esc(r.path)}</span>
        <span class="repo-meta">${metaHTML(r)}</span>
        ${actionsHTML(r)}
      </div>`;
  }

  // ---------------------------------------------------------------
  // 最近打开
  // ---------------------------------------------------------------
  function renderRecent(items) {
    items = items || [];
    if (!items.length) {
      recentEmpty.style.display = "block";
      recentWrap.innerHTML = "";
      return;
    }
    recentEmpty.style.display = "none";
    recentWrap.innerHTML = items
      .map(
        (it) => `
        <div class="recent-row${it.exists ? "" : " missing"}" data-path="${esc(it.path)}" title="${esc(it.path)}">
          <span class="rr-icon">${it.kind === "folder" ? window.ICONS.folder : window.ICONS.fileText}</span>
          <span class="rr-name">${esc(it.name)}</span>
          <span class="rr-path">${esc(it.path)}</span>
          ${it.kind === "folder" && it.exists ? `<button class="rr-save" title="保存为仓库">${window.ICONS.plus}存为仓库</button>` : ""}
        </div>`
      )
      .join("");
  }

  // ---------------------------------------------------------------
  // 事件（容器级委托，仅绑定一次）
  // ---------------------------------------------------------------
  function bindStatic() {
    // 快速操作
    on("hq-new", () => { hide(); handlers.newDoc && handlers.newDoc(); });
    on("hq-open-file", () => handlers.openFileDialog && handlers.openFileDialog());
    on("hq-add-repo", addRepo);
    on("repo-empty-add", addRepo);

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

    // 最近列表
    recentWrap.addEventListener("click", async (e) => {
      const row = e.target.closest(".recent-row");
      if (!row) return;
      const saveBtn = e.target.closest(".rr-save");
      if (saveBtn) {
        e.stopPropagation();
        const res = await api().add_project(row.dataset.path, "");
        if (res && res.ok) {
          toast(res.existed ? "该目录已在仓库列表中" : "已保存为仓库");
          refresh();
        } else toast("保存失败：" + ((res && res.error) || ""));
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
    handlers.openFolderResult && handlers.openFolderResult(res);
  }

  async function addRepo() {
    const a = api();
    if (!a) return;
    const res = await a.add_project_dialog();
    if (res && res.ok) {
      toast(res.existed ? "该目录已在仓库列表中" : "已添加仓库：" + res.project.name);
      refresh();
    } else if (res && !res.cancelled) {
      toast("添加失败：" + (res.error || ""));
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
    } else if (act === "new-window") {
      const res = await a.open_project_new_window(id);
      if (!res.ok) toast("打开失败：" + (res.error || ""));
    } else if (act === "rename") {
      openRenameModal(repo);
    } else if (act === "reveal") {
      const res = await a.reveal_in_explorer(repo.path);
      if (!res.ok) toast(res.error || "无法打开资源管理器");
    } else if (act === "remove") {
      if (!window.confirm(`把「${repo.name}」从仓库列表移除？\n（仅移除记录，不会删除磁盘文件）`)) return;
      await a.remove_project(id);
      toast("已移除");
      refresh();
    }
  }

  // ---------------------------------------------------------------
  // 重命名小弹窗
  // ---------------------------------------------------------------
  function openRenameModal(repo) {
    modalMask.innerHTML = `
      <div class="modal mini-modal">
        <div class="modal-head">
          <span class="badge">${window.ICONS.edit}</span>
          <div><h2>重命名仓库</h2><p>${esc(repo.path)}</p></div>
        </div>
        <div class="modal-body">
          <input class="mm-input" id="mm-name" value="${esc(repo.name)}" maxlength="60" />
        </div>
        <div class="modal-foot">
          <button class="btn btn-ghost" id="mm-cancel">取消</button>
          <button class="btn btn-primary" id="mm-ok">确定</button>
        </div>
      </div>`;
    modalMask.classList.add("open");
    const input = document.getElementById("mm-name");
    input.focus();
    input.select();

    const submit = async () => {
      const name = input.value.trim();
      if (!name) { toast("名称不能为空"); return; }
      const res = await api().rename_project(repo.id, name);
      if (res && res.ok) { closeModal(); refresh(); }
      else toast("重命名失败：" + ((res && res.error) || ""));
    };
    document.getElementById("mm-ok").addEventListener("click", submit);
    document.getElementById("mm-cancel").addEventListener("click", closeModal);
    input.addEventListener("keydown", (e) => {
      if (e.key === "Enter") submit();
      else if (e.key === "Escape") { e.stopPropagation(); closeModal(); }  // 防冒泡触发首页 Esc 关闭
    });
  }

  function closeModal() {
    modalMask.classList.remove("open");
    modalMask.innerHTML = "";
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
    document.querySelectorAll("#repo-view-toggle button").forEach((b) =>
      b.classList.toggle("active", b.dataset.view === view)
    );
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

  // 路径稳定映射到色相：同一仓库颜色恒定
  function hue(path) {
    let h = 0;
    const s = String(path || "");
    for (let i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) >>> 0;
    return h % 360;
  }

  function esc(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function toast(msg) {
    if (handlers.toast) handlers.toast(msg);
  }

  function on(id, fn) {
    const el = document.getElementById(id);
    if (el) el.addEventListener("click", fn);
  }

  window.Home = { init, show, hide, toggle, isOpen, refresh };
})();
