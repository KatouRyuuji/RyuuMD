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
