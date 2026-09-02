/* 欢迎窗口：应用特性介绍 + 操作风格选择 + 赞助侧栏。
   布局参考 WeekRepo 欢迎界面：徽章标题 + WELCOME 字标 → 问候语 →
   功能卡片 → 灯泡提示条 → 右侧「扫码支持作者」卡片 → 底部「不再显示 / 开始使用」；
   RyuuMD 额外保留「选择操作风格」一节。
   首次启动自动弹出（app.js），也可从「设置 → 欢迎页」重新打开。 */
(function () {
  const mask = document.getElementById("welcome-mask");
  let chosenStyle = "notion";
  let resolveFn = null;

  const FEATURES = [
    { icon: "zap", title: "极速即时渲染", desc: "Typora 式所见即所得，输入即排版，读写一体" },
    { icon: "slash", title: "斜杠命令", desc: "输入 / 呼出菜单，标题、表格、公式一键插入" },
    { icon: "lock", title: "全本地 · 隐私安全", desc: "无需联网与账号，文件始终留在你的电脑里" },
    { icon: "toc", title: "文件树与大纲", desc: "侧栏浏览文件夹，大纲一点即达，长文不迷路" },
    { icon: "palette", title: "RyuujiDesign 配色", desc: "A 语言六套色板（明暗双态自适应），还可自选界面与等宽字体" },
    { icon: "drag", title: "拖拽即开", desc: "拖入 .md 或文件夹直接打开，记忆上次会话" },
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
    const result = { style: chosenStyle, dontShow };
    if (resolveFn) resolveFn(result);
    resolveFn = null;
  }

  function close() {
    if (!isOpen()) return;
    finish();
  }

  /* 返回 Promise<{style, dontShow}> */
  function show(defaultStyle) {
    chosenStyle = defaultStyle || "notion";
    render();
    if (chosenStyle === "wolai") {
      mask.querySelectorAll(".style-opt").forEach((o) =>
        o.classList.toggle("selected", o.dataset.style === "wolai")
      );
    }
    mask.classList.add("open");
    if (window.App && window.App.registerEscape) window.App.registerEscape(close);
    const start = document.getElementById("welcome-start");
    if (start) start.focus();
    return new Promise((res) => (resolveFn = res));
  }

  window.Welcome = { show, close, isOpen };
})();
