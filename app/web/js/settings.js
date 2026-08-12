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
    document.getElementById("set-close").addEventListener("click", close);
    // 重开欢迎页：关闭设置后走 App 的欢迎流程（含风格选择与持久化）
    document.getElementById("set-welcome").addEventListener("click", () => {
      close();
      if (window.App && window.App.showWelcome) window.App.showWelcome();
    });
    syncActive();
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
