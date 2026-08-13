/* 设置弹窗：操作风格切换、亮/暗主题、重开欢迎页。
   变更即时回调 App 应用并持久化。 */
(function () {
  const mask = document.getElementById("settings-mask");
  let cfg = null;
  let onApply = null; // (partialConfig) => void

  function render() {
    mask.innerHTML = `
      <div class="modal">
        <div class="modal-head">
          <span class="badge">${window.ICONS.gear}</span>
          <div>
            <h2>设置</h2>
            <p>偏好将自动保存</p>
          </div>
        </div>
        <div class="modal-body">
          <div class="setting-row">
            <div>
              <div class="label">操作风格</div>
              <div class="desc">斜杠命令的触发词风格</div>
            </div>
            <div class="segmented" id="set-style">
              <button data-v="notion">Typora + Notion</button>
              <button data-v="wolai">Typora + Wolai</button>
            </div>
          </div>
          <div class="setting-row">
            <div>
              <div class="label">主题外观</div>
              <div class="desc">亮色 phycat sky / 暗色 phycat vampire</div>
            </div>
            <div class="segmented" id="set-theme">
              <button data-v="light">亮色</button>
              <button data-v="dark">暗色</button>
            </div>
          </div>
          <div class="setting-row">
            <div>
              <div class="label">启动时显示</div>
              <div class="desc">上次会话 = 自动恢复退出前的文件夹与文档</div>
            </div>
            <div class="segmented" id="set-startup">
              <button data-v="restore">上次会话</button>
              <button data-v="home">首页</button>
            </div>
          </div>
          <div class="setting-row">
            <div>
              <div class="label">默认 Markdown 应用</div>
              <div class="desc">双击 .md 文件直接用 RyuuMD 打开</div>
            </div>
            <button class="btn" id="set-default-app">设为默认</button>
          </div>
          <div class="setting-row">
            <div>
              <div class="label">欢迎页</div>
              <div class="desc">重看功能介绍与支持作者</div>
            </div>
            <button class="btn" id="set-welcome">打开</button>
          </div>
          <div class="setting-row">
            <div>
              <div class="label">启用云同步</div>
              <div class="desc">官方不提供云端，勾选后使用你自己的 WebDAV</div>
            </div>
            <button type="button" class="toggle" id="cloud-enabled" title="启用云同步"></button>
          </div>
          <div class="cloud-panel" id="cloud-panel">
            <p class="cloud-hint">适用于坚果云、Nextcloud、群晖、AList、Seafile 等 WebDAV。笔记仍保存在本地，云端只做同步副本。</p>
            <div class="field-grid">
              <label for="cloud-url">服务器地址</label>
              <input class="field-input" id="cloud-url" placeholder="https://dav.jianguoyun.com/dav/" spellcheck="false" />
              <label for="cloud-user">用户名</label>
              <input class="field-input" id="cloud-user" placeholder="邮箱或账号" spellcheck="false" />
              <label for="cloud-pass">密码</label>
              <input class="field-input" id="cloud-pass" type="password" placeholder="" spellcheck="false" />
              <label for="cloud-root">远端目录</label>
              <input class="field-input" id="cloud-root" placeholder="RyuuMD" spellcheck="false" />
            </div>
            <label class="check-line"><input type="checkbox" id="cloud-auto-save" /> 保存后自动上传当前文件</label>
            <label class="check-line"><input type="checkbox" id="cloud-auto-start" /> 启动时自动同步已开启的仓库</label>
            <label class="check-line"><input type="checkbox" id="cloud-sync-all" /> 同步全部仓库（否则请在首页为单个仓库打开云同步）</label>
            <label class="check-line"><input type="checkbox" id="cloud-insecure" /> 忽略 SSL 证书错误（仅内网 NAS 自签证书时勾选）</label>
            <div class="cloud-actions">
              <button class="btn" id="cloud-test">测试连接</button>
              <button class="btn btn-primary" id="cloud-sync-now">立即同步</button>
            </div>
            <div id="cloud-status"></div>
          </div>
        </div>
        <div class="modal-foot">
          <button class="btn btn-primary" id="set-close">完成</button>
        </div>
      </div>`;

    bindSeg("set-style", "operation_style");
    bindSeg("set-theme", "theme");
    bindSeg("set-startup", "startup_page");
    document.getElementById("set-close").addEventListener("click", close);
    // 重开欢迎页：关闭设置后走 App 的欢迎流程（含风格选择与持久化）
    document.getElementById("set-welcome").addEventListener("click", () => {
      close();
      if (window.App && window.App.showWelcome) window.App.showWelcome();
    });
    bindDefaultApp();
    bindCloud();
    syncActive();
  }

  // —— 默认 Markdown 应用（Windows 文件关联）——
  // 点击后：注册 ProgID + 弹系统「打开方式」对话框，用户勾选「始终」完成设置。
  // 确认后系统会用默认应用打开验证文档，RyuuMD 单实例机制将其接进新窗口。
  function bindDefaultApp() {
    const btn = document.getElementById("set-default-app");
    const a = () => window.pywebview && window.pywebview.api;

    async function refreshState() {
      try {
        const st = await a().get_md_assoc_status();
        if (!st.supported) {
          btn.disabled = true;
          btn.textContent = "仅支持 Windows";
          return;
        }
        if (st.is_default) {
          btn.textContent = "已是默认 ✓";
          btn.classList.add("btn-ghost");
        } else {
          btn.textContent = "设为默认";
          btn.classList.remove("btn-ghost");
        }
      } catch (e) { /* 后端不可用时保持默认文案 */ }
    }

    btn.addEventListener("click", async () => {
      if (!a()) return;
      btn.disabled = true;
      btn.textContent = "请在系统对话框中选择 RyuuMD…";
      try {
        const res = await a().set_default_md_app();
        if (!res.ok) {
          if (window.App) window.App.toast("设置失败：" + (res.error || ""));
        } else if (res.status && res.status.is_default) {
          if (window.App) window.App.toast("已设为默认 Markdown 应用");
        }
      } finally {
        btn.disabled = false;
        refreshState();
      }
    });

    refreshState();
  }

  function cloudCfg() {
    return cfg.cloud_sync || (cfg.cloud_sync = {
      enabled: false, url: "", username: "", password: "", remote_root: "RyuuMD",
      auto_on_save: false, auto_on_start: false, insecure_ssl: false,
      sync_all_projects: false, password_set: false,
    });
  }

  function bindCloud() {
    const a = () => window.pywebview && window.pywebview.api;
    const cs = cloudCfg();
    const panel = document.getElementById("cloud-panel");
    const tog = document.getElementById("cloud-enabled");
    const status = document.getElementById("cloud-status");
    const pass = document.getElementById("cloud-pass");

    function setToggle(on) {
      tog.classList.toggle("on", on);
      tog.setAttribute("aria-pressed", on ? "true" : "false");
      panel.classList.toggle("show", on);
    }

    document.getElementById("cloud-url").value = cs.url || "";
    document.getElementById("cloud-user").value = cs.username || "";
    document.getElementById("cloud-root").value = cs.remote_root || "RyuuMD";
    pass.placeholder = cs.password_set ? "已保存，留空则不修改" : "应用密码或账号密码";
    pass.value = "";
    document.getElementById("cloud-auto-save").checked = !!cs.auto_on_save;
    document.getElementById("cloud-auto-start").checked = !!cs.auto_on_start;
    document.getElementById("cloud-sync-all").checked = !!cs.sync_all_projects;
    document.getElementById("cloud-insecure").checked = !!cs.insecure_ssl;
    setToggle(!!cs.enabled);

    async function persist(extra) {
      const payload = {
        enabled: tog.classList.contains("on"),
        url: document.getElementById("cloud-url").value.trim(),
        username: document.getElementById("cloud-user").value.trim(),
        remote_root: document.getElementById("cloud-root").value.trim() || "RyuuMD",
        auto_on_save: document.getElementById("cloud-auto-save").checked,
        auto_on_start: document.getElementById("cloud-auto-start").checked,
        sync_all_projects: document.getElementById("cloud-sync-all").checked,
        insecure_ssl: document.getElementById("cloud-insecure").checked,
        provider: "webdav",
      };
      const pwd = pass.value;
      if (pwd) payload.password = pwd;
      Object.assign(payload, extra || {});
      const api = a();
      if (api && api.save_cloud_settings) {
        const res = await api.save_cloud_settings(payload);
        if (res && res.ok && res.cloud_sync) {
          cfg.cloud_sync = res.cloud_sync;
          if (onApply) onApply({ cloud_sync: res.cloud_sync });
          if (pwd) { pass.value = ""; pass.placeholder = "已保存，留空则不修改"; }
          return res.cloud_sync;
        }
        if (res && !res.ok) {
          if (window.App) window.App.toast("保存云设置失败：" + (res.error || ""));
        }
        return null;
      }
      cfg.cloud_sync = Object.assign(cloudCfg(), payload, { password: "", password_set: !!(pwd || cs.password_set) });
      if (onApply) onApply({ cloud_sync: cfg.cloud_sync });
      return cfg.cloud_sync;
    }

    tog.addEventListener("click", () => {
      setToggle(!tog.classList.contains("on"));
      persist();
    });
    ["cloud-url", "cloud-user", "cloud-root", "cloud-pass",
     "cloud-auto-save", "cloud-auto-start", "cloud-sync-all", "cloud-insecure"].forEach((id) => {
      const el = document.getElementById(id);
      const ev = el.type === "checkbox" || el.type === "password" ? "change" : "change";
      el.addEventListener(ev, () => persist());
    });

    document.getElementById("cloud-test").addEventListener("click", async () => {
      await persist();
      const api = a();
      if (!api || !api.test_cloud) return;
      status.textContent = "正在测试连接…";
      const res = await api.test_cloud();
      status.textContent = res.ok ? (res.message || "连接成功") : ("失败：" + (res.error || ""));
      if (window.App) window.App.toast(res.ok ? "WebDAV 连接成功" : "连接失败：" + (res.error || ""));
    });

    document.getElementById("cloud-sync-now").addEventListener("click", async () => {
      await persist();
      const api = a();
      if (!api || !api.sync_cloud) return;
      status.textContent = "正在同步…";
      const res = await api.sync_cloud("");
      status.textContent = res.message || (res.ok ? "同步完成" : (res.error || "同步失败"));
      if (window.App) window.App.toast(res.ok ? (res.message || "同步完成") : "同步失败：" + (res.error || ""));
    });
  }

  function bindSeg(id, key, numeric) {
    document.getElementById(id).querySelectorAll("button").forEach((btn) => {
      btn.addEventListener("click", () => {
        const v = numeric ? parseInt(btn.dataset.v, 10) : btn.dataset.v;
        cfg[key] = v;
        syncActive();
        if (onApply) onApply({ [key]: v });
      });
    });
  }

  function syncActive() {
    setSeg("set-style", String(cfg.operation_style));
    setSeg("set-theme", String(cfg.theme));
    setSeg("set-startup", String(cfg.startup_page || "restore"));
  }

  function setSeg(id, val) {
    const el = document.getElementById(id);
    if (!el) return;
    el.querySelectorAll("button").forEach((b) =>
      b.classList.toggle("active", b.dataset.v === val)
    );
  }

  function open(config, applyFn) {
    cfg = Object.assign({}, config);
    onApply = applyFn;
    render();
    mask.classList.add("open");
  }

  function close() {
    mask.classList.remove("open");
  }

  mask.addEventListener("click", (e) => {
    if (e.target === mask) close();
  });

  window.Settings = { open, close };
})();
