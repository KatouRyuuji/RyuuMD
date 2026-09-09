/* 设置弹窗：通用 / 外观 / 云同步 / AI 四个分组标签页。
   切换标签只显隐、不重渲染，避免云同步与 AI 输入丢失。
   变更即时回调 App 应用并持久化（无确定/取消）。 */
(function () {
  const mask = document.getElementById("settings-mask");
  let cfg = null;
  let onApply = null; // (partialConfig) => void

  /* RyuujiDesign v6.1 A 语言色板：每板自带明暗双态（palettes.css），
     色板选择不再分亮暗两组；色值为各板亮色命名主色（供色块展示）。 */
  const PALETTES = [
    { id: "a1", name: "A-1 霜纸靛蓝", color: "#4a51e8" },
    { id: "a2", name: "A-2 和红", color: "#e3253f" },
    { id: "a3", name: "A-3 藤色", color: "#9550e0" },
    { id: "a4", name: "A-4 柳染", color: "#85c91e" },
    { id: "a5", name: "A-5 水浅葱", color: "#17bec8" },
    { id: "a6", name: "A-6 樱花", color: "#f04572" },
  ];
  const FONT_UI_PRESETS = [
    { value: "", label: "默认（霞鹜文楷）" },
    { value: "Noto Sans SC", label: "Noto Sans SC" },
    { value: "Microsoft YaHei", label: "微软雅黑" },
    { value: "SimSun", label: "宋体" },
    { value: "KaiTi", label: "楷体" },
  ];
  const FONT_MONO_PRESETS = [
    { value: "", label: "默认（Cascadia Code）" },
    { value: "Consolas", label: "Consolas" },
    { value: "JetBrains Mono", label: "JetBrains Mono" },
    { value: "Courier New", label: "Courier New" },
    { value: "__custom__", label: "自定义…" },
  ];

  function swatchHtml(list) {
    return list.map((p) =>
      `<button type="button" class="palette-swatch" data-v="${p.id}" title="${p.name}" aria-label="${p.name}" style="background:${p.color}"></button>`
    ).join("");
  }

  function optionHtml(list) {
    return list.map((p) => `<option value="${escapeAttr(p.value)}">${p.label}</option>`).join("");
  }

  function escapeAttr(s) {
    return String(s).replace(/&/g, "&amp;").replace(/"/g, "&quot;").replace(/</g, "&lt;");
  }

  function render() {
    mask.innerHTML = `
      <div class="modal settings-modal" role="dialog" aria-modal="true" aria-label="设置">
        <div class="modal-head">
          <span class="badge badge--plain">${window.ICONS.gear}</span>
          <div>
            <h2>设置</h2>
            <p>偏好将自动保存</p>
          </div>
        </div>
        <div class="settings-tabs" role="tablist">
          <button type="button" class="settings-tab active" data-tab="general" role="tab">通用</button>
          <button type="button" class="settings-tab" data-tab="appearance" role="tab">外观</button>
          <button type="button" class="settings-tab" data-tab="cloud" role="tab">云同步</button>
          <button type="button" class="settings-tab" data-tab="ai" role="tab">AI</button>
        </div>
        <div class="modal-body">
          <div class="settings-pane active" data-pane="general">
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
                <div class="label">启动时显示</div>
                <div class="desc">首页 = 每次启动进入首页（默认）；上次会话 = 恢复退出前的文件夹与文档</div>
              </div>
              <div class="segmented" id="set-startup">
                <button data-v="home">首页</button>
                <button data-v="restore">上次会话</button>
              </div>
            </div>
            <div class="setting-row">
              <div>
                <div class="label">再次启动程序</div>
                <div class="desc">已有窗口在运行时再次启动（双击图标）：新开窗口或仅激活当前窗口；双击 md 文件始终开新窗口</div>
              </div>
              <div class="segmented" id="set-second-launch">
                <button data-v="new">新开窗口</button>
                <button data-v="focus">激活当前窗口</button>
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
                <div class="label">学习仓库</div>
                <div class="desc">内置交互教程仓库，随意涂改不影响内置原件</div>
              </div>
              <button class="btn" id="set-tutorial">打开</button>
            </div>
            <div class="setting-row">
              <div>
                <div class="label">公式引擎</div>
                <div class="desc">KaTeX 渲染更快；MathJax 兼容更多 LaTeX 宏与语法。切换后编辑器自动重建</div>
              </div>
              <div class="segmented" id="set-math-engine">
                <button data-v="katex">KaTeX</button>
                <button data-v="mathjax">MathJax</button>
              </div>
            </div>
            <div class="setting-row">
              <div>
                <div class="label">自动保存</div>
                <div class="desc">已保存的文档在停止输入后自动写盘</div>
              </div>
              <button type="button" class="toggle" id="set-autosave" title="自动保存"></button>
            </div>
            <div class="setting-row">
              <div>
                <div class="label">编辑方式</div>
                <div class="desc">直改源文件：打开即改磁盘原件。工作副本：进入仓库时生成副本，只在副本上读写，用「保存至源 / 合并源」与原件同步</div>
              </div>
              <div class="segmented" id="set-edit-mode">
                <button type="button" data-v="source">直改源文件</button>
                <button type="button" data-v="workdir">工作副本</button>
              </div>
            </div>
            <div class="write-panel">
              <div class="label">日记目录</div>
              <div class="desc">相对当前仓库，每日笔记保存为 YYYY-MM-DD.md</div>
              <input class="field-input" id="set-daily-folder" placeholder="日记" spellcheck="false" />
            </div>
          </div>
          <div class="settings-pane" data-pane="appearance">
            <div class="setting-row">
              <div>
                <div class="label">主题外观</div>
                <div class="desc">工具栏按钮在亮 / 暗之间对切；配色方案每板自带明暗双态</div>
              </div>
              <div class="segmented" id="set-theme">
                <button data-v="light">亮色</button>
                <button data-v="dark">暗色</button>
              </div>
            </div>
            <div class="setting-row">
              <div>
                <div class="label">配色方案</div>
              </div>
              <div class="palette-swatches" id="swatch-palette">${swatchHtml(PALETTES)}</div>
            </div>
            <div class="setting-row">
              <div>
                <div class="label">正文字体</div>
                <div class="desc">只影响编辑器正文；外壳固定 Noto Sans SC</div>
              </div>
              <div class="font-field">
                <select class="field-input" id="set-font-ui">${optionHtml(FONT_UI_PRESETS)}</select>
              </div>
            </div>
            <div class="setting-row">
              <div>
                <div class="label">等宽字体</div>
                <div class="desc">源码模式与代码块；默认 Cascadia Code</div>
              </div>
              <div class="font-field">
                <select class="field-input" id="set-font-mono">${optionHtml(FONT_MONO_PRESETS)}</select>
                <input class="field-input" id="set-font-mono-custom" placeholder="本机字体名" spellcheck="false" hidden />
              </div>
            </div>
          </div>
          <div class="settings-pane" data-pane="cloud">
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
                <button class="btn btn--primary" id="cloud-sync-now">立即同步</button>
              </div>
              <div id="cloud-status"></div>
            </div>
          </div>
          <div class="settings-pane" data-pane="ai">
            <p class="cloud-hint">使用 Anthropic Messages 协议（官方或兼容中转）。笔记摘录与提问会发往你填写的 Base URL；API Key 保存在本机配置且不回显。语义搜索与 <code>ask</code> 提问需按 Enter 才会请求。</p>
            <div class="field-grid ai-fields">
              <label for="ai-base-url">Base URL</label>
              <input class="field-input" id="ai-base-url" placeholder="https://api.anthropic.com" spellcheck="false" />
              <label for="ai-api-key">API Key</label>
              <input class="field-input" id="ai-api-key" type="password" placeholder="sk-…" spellcheck="false" autocomplete="off" />
              <label for="ai-model">模型</label>
              <input class="field-input" id="ai-model" placeholder="claude-sonnet-4-20250514" spellcheck="false" />
            </div>
          </div>
        </div>
        <div class="modal-foot">
          <button class="btn btn--primary" id="set-close">完成</button>
        </div>
      </div>`;

    bindTabs();
    bindSeg("set-style", "operation_style");
    bindSeg("set-theme", "theme");
    bindSeg("set-startup", "startup_page");
    bindSeg("set-second-launch", "second_launch");
    bindSeg("set-math-engine", "math_engine");
    bindSeg("set-edit-mode", "edit_mode");
    bindSwatches("swatch-palette", "palette");
    bindFontSimple("set-font-ui", "font_ui", FONT_UI_PRESETS);
    bindFont("set-font-mono", "set-font-mono-custom", "font_mono", FONT_MONO_PRESETS);
    document.getElementById("set-close").addEventListener("click", close);
    // 重开欢迎页：关闭设置后走 App 的欢迎流程（含风格选择与持久化）
    document.getElementById("set-welcome").addEventListener("click", () => {
      close();
      if (window.App && window.App.showWelcome) window.App.showWelcome();
    });
    // 学习仓库：关闭设置后走 App 的打开流程（含幂等复制与导读加载）
    document.getElementById("set-tutorial").addEventListener("click", () => {
      close();
      if (window.App && window.App.openTutorial) window.App.openTutorial();
    });
    bindDefaultApp();
    bindAutoSave();
    bindCloud();
    bindAi();
    syncActive();
  }

  function bindTabs() {
    const tabs = mask.querySelectorAll(".settings-tab");
    const panes = mask.querySelectorAll(".settings-pane");
    tabs.forEach((tab) => {
      tab.addEventListener("click", () => {
        const id = tab.dataset.tab;
        tabs.forEach((t) => t.classList.toggle("active", t.dataset.tab === id));
        panes.forEach((p) => p.classList.toggle("active", p.dataset.pane === id));
      });
    });
  }

  function bindSwatches(id, key) {
    document.getElementById(id).querySelectorAll(".palette-swatch").forEach((btn) => {
      btn.addEventListener("click", () => {
        cfg[key] = btn.dataset.v;
        syncSwatches();
        if (onApply) onApply({ [key]: btn.dataset.v });
      });
    });
  }

  /* 正文字体：纯预设下拉（无自定义输入）；旧配置中的自定义值不在预设里时回显默认项，
     用户重新选择后才覆盖，不会静默丢设置。 */
  function bindFontSimple(selectId, key, presets) {
    const sel = document.getElementById(selectId);
    const cur = cfg[key] || "";
    sel.value = presets.some((p) => p.value === cur) ? cur : "";
    sel.addEventListener("change", () => {
      cfg[key] = sel.value;
      if (onApply) onApply({ [key]: sel.value });
    });
  }

  function bindFont(selectId, customId, key, presets) {
    const sel = document.getElementById(selectId);
    const custom = document.getElementById(customId);
    const presetVals = presets.map((p) => p.value).filter((v) => v !== "__custom__");

    function reflect() {
      const cur = cfg[key] || "";
      if (cur && presetVals.indexOf(cur) < 0) {
        sel.value = "__custom__";
        custom.value = cur;
        custom.hidden = false;
      } else {
        sel.value = cur;
        custom.hidden = true;
      }
    }

    sel.addEventListener("change", () => {
      if (sel.value === "__custom__") {
        custom.hidden = false;
        custom.focus();
        return;
      }
      custom.hidden = true;
      cfg[key] = sel.value;
      if (onApply) onApply({ [key]: sel.value });
    });
    custom.addEventListener("change", () => {
      const v = custom.value.trim();
      cfg[key] = v;
      if (onApply) onApply({ [key]: v });
    });
    reflect();
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
          btn.classList.add("btn--text");
        } else {
          btn.textContent = "设为默认";
          btn.classList.remove("btn--text");
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
          if (window.App) window.App.toast("设置失败：" + (res.error || ""), { type: "error" });
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

  function bindAutoSave() {
    const tog = document.getElementById("set-autosave");
    const daily = document.getElementById("set-daily-folder");
    function setOn(on) {
      tog.classList.toggle("on", on);
      tog.setAttribute("aria-pressed", on ? "true" : "false");
    }
    setOn(cfg.auto_save !== false);
    tog.addEventListener("click", () => {
      const next = !tog.classList.contains("on");
      setOn(next);
      cfg.auto_save = next;
      if (onApply) onApply({ auto_save: next });
    });
    daily.value = cfg.daily_note_folder || "日记";
    daily.addEventListener("change", () => {
      const v = daily.value.trim() || "日记";
      daily.value = v;
      cfg.daily_note_folder = v;
      if (onApply) onApply({ daily_note_folder: v });
    });
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
          if (window.App) window.App.toast("保存云设置失败：" + (res.error || ""), { type: "error" });
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
      el.addEventListener("change", () => persist());
    });

    document.getElementById("cloud-test").addEventListener("click", async () => {
      await persist();
      const api = a();
      if (!api || !api.test_cloud) return;
      status.textContent = "正在测试连接…";
      const res = await api.test_cloud();
      status.textContent = res.ok ? (res.message || "连接成功") : ("失败：" + (res.error || ""));
      if (window.App) window.App.toast(res.ok ? "WebDAV 连接成功" : "连接失败：" + (res.error || ""), res.ok ? { type: "success" } : { type: "error" });
    });

    document.getElementById("cloud-sync-now").addEventListener("click", async () => {
      await persist();
      const api = a();
      if (!api || !api.sync_cloud) return;
      status.textContent = "正在同步…";
      const res = await api.sync_cloud("");
      status.textContent = res.message || (res.ok ? "同步完成" : (res.error || "同步失败"));
      if (window.App) window.App.toast(res.ok ? (res.message || "同步完成") : "同步失败：" + (res.error || ""), res.ok ? undefined : { type: "error" });
    });
  }

  function aiCfg() {
    return cfg.ai || (cfg.ai = {
      base_url: "",
      api_key: "",
      model: "",
      api_key_set: false,
    });
  }

  function bindAi() {
    const a = () => window.pywebview && window.pywebview.api;
    const ai = aiCfg();
    const urlEl = document.getElementById("ai-base-url");
    const keyEl = document.getElementById("ai-api-key");
    const modelEl = document.getElementById("ai-model");
    urlEl.value = ai.base_url || "";
    keyEl.value = "";
    keyEl.placeholder = ai.api_key_set ? "已保存，留空则不修改" : "sk-…";
    modelEl.value = ai.model || "";

    async function persist() {
      const payload = {
        base_url: urlEl.value.trim(),
        api_key: keyEl.value.trim(),
        model: modelEl.value.trim(),
      };
      cfg.ai = Object.assign(aiCfg(), payload, {
        api_key: "",
        api_key_set: !!(payload.api_key || ai.api_key_set),
      });
      keyEl.placeholder = cfg.ai.api_key_set ? "已保存，留空则不修改" : "sk-…";
      const api = a();
      if (api && api.update_config) {
        const res = await api.update_config({ ai: payload });
        if (res && res.ok) {
          if (onApply) onApply({ ai: cfg.ai });
          return;
        }
        if (res && !res.ok && window.App) {
          window.App.toast("保存 AI 设置失败：" + (res.error || ""), { type: "error" });
        }
        return;
      }
      if (onApply) onApply({ ai: cfg.ai });
    }

    [urlEl, keyEl, modelEl].forEach((el) => {
      el.addEventListener("change", () => persist());
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
    setSeg("set-startup", String(cfg.startup_page || "home"));
    setSeg("set-second-launch", String(cfg.second_launch || "new"));
    setSeg("set-math-engine", String(cfg.math_engine || "katex"));
    setSeg("set-edit-mode", String(cfg.edit_mode || "source"));
    syncSwatches();
  }

  function syncSwatches() {
    setSwatch("swatch-palette", cfg.palette || "a1");
  }

  function setSeg(id, val) {
    const el = document.getElementById(id);
    if (!el) return;
    el.querySelectorAll("button").forEach((b) =>
      b.classList.toggle("active", b.dataset.v === val)
    );
  }

  function setSwatch(id, val) {
    const el = document.getElementById(id);
    if (!el) return;
    el.querySelectorAll(".palette-swatch").forEach((b) =>
      b.classList.toggle("selected", b.dataset.v === val)
    );
  }

  let lastFocus = null;

  function isOpen() {
    return mask.classList.contains("open");
  }

  function open(config, applyFn) {
    cfg = Object.assign({}, config);
    onApply = applyFn;
    if (!isOpen()) {
      lastFocus = document.activeElement;
      if (window.App && window.App.registerEscape) window.App.registerEscape(close);
    }
    render();
    mask.classList.add("open");
    const closer = document.getElementById("set-close");
    if (closer) closer.focus();
  }

  function close() {
    if (window.App && window.App.unregisterEscape) window.App.unregisterEscape(close);
    mask.classList.remove("open");
    if (lastFocus && typeof lastFocus.focus === "function") {
      try { lastFocus.focus(); } catch (e) { /* 原焦点节点可能已卸 */ }
    }
    lastFocus = null;
  }

  mask.addEventListener("click", (e) => {
    if (e.target === mask) close();
  });

  window.Settings = { open, close, isOpen };
})();
