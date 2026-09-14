/* 欢迎窗口：应用特性介绍 + 操作风格选择 + 赞助侧栏。
   布局参考 WeekRepo 欢迎界面：徽章标题 + WELCOME 字标 → 问候语 →
   功能卡片 → 灯泡提示条 → 右侧「扫码支持作者」卡片 → 底部「不再显示 / 开始使用」；
   RyuuMD 额外保留「选择操作风格」一节。
   首次启动自动弹出（app.js），也可从「设置 → 欢迎页」重新打开。 */
(function () {
  const mask = document.getElementById("welcome-mask");
  let chosenStyle = "notion";
  let chosenEdit = "source";
  let resolveFn = null;

  const FEATURES = [
    { icon: "zap", title: "极速即时渲染", desc: "Typora 式所见即所得，输入即排版" },
    { icon: "slash", title: "斜杠命令", desc: "输入 / 插入标题、表格、公式" },
    { icon: "lock", title: "全本地 · 隐私安全", desc: "无需账号，文件始终留在本机" },
    { icon: "toc", title: "文件树与大纲", desc: "侧栏浏览文件夹，大纲一点即达" },
    { icon: "palette", title: "五套色板", desc: "霜靛、藤色、柳染、水浅葱、樱花" },
    { icon: "drag", title: "拖拽即开", desc: "拖入 md 或文件夹即可打开" },
  ];

  function buildFeatureCards() {
    return FEATURES.map(
      (f) => `
      <div class="feature-card">
        <span class="fc-icon">${window.ICONS[f.icon] || ""}</span>
        <div>
          <h4>${f.title}</h4>
          <p>${f.desc}</p>
        </div>
      </div>`
    ).join("");
  }

  function buildStylePicker() {
    return `
      <div class="style-picker">
        <div class="style-opt selected" data-style="notion">
          <h4>Typora + Notion</h4>
          <p>英文 / 符号触发：<code>/h1</code> <code>#</code> <code>/table</code>。默认推荐，符合多数人习惯。</p>
        </div>
        <div class="style-opt" data-style="wolai">
          <h4>Typora + Wolai</h4>
          <p>拼音缩写触发：<code>/bt1</code> <code>/dmk</code> <code>/wxlb</code>。中文用户的高效之选。</p>
        </div>
      </div>`;
  }

  function buildEditModePicker() {
    return `
      <div class="mode-picker" id="welcome-edit-mode">
        <div class="mode-opt selected" data-mode="source">
          <h4>直改源文件</h4>
          <p>打开即读写磁盘上的原文件。适合自己的仓库、即时保存。</p>
        </div>
        <div class="mode-opt" data-mode="workdir">
          <h4>工作副本</h4>
          <p>进入仓库时生成副本，只改副本；用「保存至源 / 合并源」再与原件同步。</p>
        </div>
      </div>`;
  }

  function render() {
    mask.innerHTML = `
      <div class="modal welcome" role="dialog" aria-modal="true" aria-label="欢迎使用 RyuuMD">
        <div class="modal-head">
          <span class="badge">md</span>
          <div>
            <h2>欢迎使用 RyuuMD</h2>
            <p>轻量、全本地、极速的 Markdown 编辑与阅读工具</p>
          </div>
          <span class="welcome-word">WELCOME</span>
        </div>
        <div class="modal-body">
          <div class="welcome-grid">
            <div class="welcome-main">
              <p class="welcome-greet">你好，我是 RyuuJi。愿读写 Markdown 这件事，始终轻快、专注、不被打扰。</p>
              <div class="feature-grid">${buildFeatureCards()}</div>
              <div class="welcome-tip">
                ${window.ICONS.lightbulb}
                <span>超大文档会自动以源码模式打开保持流畅，右下角可随时切换渲染 / 源码。</span>
              </div>
              <div class="style-sec">
                <div class="label">选择操作风格</div>
                <div class="desc">决定斜杠命令的触发词，随时可在「设置」中更改</div>
                ${buildStylePicker()}
              </div>
              <div class="style-sec">
                <div class="label">选择编辑方式</div>
                <div class="desc">直改源文件立即落盘；工作副本先改副本，再决定是否写回源文件。随时可在「设置」中更改</div>
                ${buildEditModePicker()}
              </div>
            </div>
            <aside class="sponsor">
              <h4>${window.ICONS.coffee}<span>扫码支持作者</span></h4>
              <div class="qr" id="welcome-qr">赞助二维码</div>
              <div class="link">
                <span class="link-head">${window.ICONS.tv}<span>B 站主页</span></span>
                <span class="link-url">space.bilibili.com/445111</span>
              </div>
            </aside>
          </div>
        </div>
        <div class="modal-foot">
          <label class="checkbox"><input type="checkbox" id="welcome-dontshow" checked /> 不再显示</label>
          <button class="btn btn--primary" id="welcome-start">${window.ICONS.sparkles}<span>开始使用</span></button>
        </div>
      </div>`;

    mask.querySelectorAll(".style-opt").forEach((el) => {
      el.addEventListener("click", () => {
        mask.querySelectorAll(".style-opt").forEach((o) => o.classList.remove("selected"));
        el.classList.add("selected");
        chosenStyle = el.dataset.style;
      });
    });
    mask.querySelectorAll(".mode-opt").forEach((el) => {
      el.addEventListener("click", () => {
        mask.querySelectorAll(".mode-opt").forEach((o) => o.classList.remove("selected"));
        el.classList.add("selected");
        chosenEdit = el.dataset.mode || "source";
      });
    });
    tryLoadQR();
    document.getElementById("welcome-start").addEventListener("click", finish);
  }

  /* 有图则展示（白底保证暗色主题下可扫码），无图保留虚线占位。
     约定：二维码放置于 assets/QRCode.png（见 README）。 */
  function tryLoadQR() {
    const box = document.getElementById("welcome-qr");
    const img = new Image();
    img.onload = () => { box.classList.add("has-img"); box.innerHTML = ""; box.appendChild(img); };
    img.onerror = () => {};
    img.src = "assets/QRCode.png";
  }

  function isOpen() {
    return mask.classList.contains("open");
  }

  function finish() {
    if (window.App && window.App.unregisterEscape) window.App.unregisterEscape(close);
    const box = document.getElementById("welcome-dontshow");
    const dontShow = box ? box.checked : true;
    mask.classList.remove("open");
    const result = { style: chosenStyle, dontShow, edit_mode: chosenEdit };
    if (resolveFn) resolveFn(result);
    resolveFn = null;
  }

  function close() {
    if (!isOpen()) return;
    finish();
  }

  /* 返回 Promise<{style, dontShow}> */
  function show(defaults) {
    if (typeof defaults === "string" || defaults == null) {
      chosenStyle = defaults || "notion";
      chosenEdit = "source";
    } else {
      chosenStyle = defaults.operation_style || defaults.style || "notion";
      chosenEdit = defaults.edit_mode || "source";
    }
    render();
    mask.querySelectorAll(".style-opt").forEach((o) =>
      o.classList.toggle("selected", o.dataset.style === chosenStyle)
    );
    mask.querySelectorAll(".mode-opt").forEach((o) =>
      o.classList.toggle("selected", o.dataset.mode === chosenEdit)
    );
    mask.classList.add("open");
    if (window.App && window.App.registerEscape) window.App.registerEscape(close);
    const start = document.getElementById("welcome-start");
    if (start) start.focus();
    return new Promise((res) => (resolveFn = res));
  }

  window.Welcome = { show, close, isOpen };
})();
