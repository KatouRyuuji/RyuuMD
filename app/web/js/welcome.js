/* 欢迎窗口：问候 + 操作风格 / 编辑方式；右侧为安静可扫的打赏图章。
   首次启动自动弹出（app.js），也可从「设置 → 欢迎页」重新打开。
   打赏卡同时给设置「支持作者」和命令面板复用（勾选不再显示后仍可找到）。 */
(function () {
  const BILI_URL = "https://space.bilibili.com/445111";
  const BILI_HOST = "space.bilibili.com/445111";
  const mask = document.getElementById("welcome-mask");
  const sponsorMask = document.getElementById("sponsor-mask");
  let chosenStyle = "notion";
  let chosenEdit = "source";
  let resolveFn = null;
  let sponsorFocus = null;

  function fillQr(box) {
    if (!box) return;
    const img = new Image();
    img.onload = () => { box.classList.add("has-img"); box.innerHTML = ""; box.appendChild(img); };
    img.onerror = () => {};
    img.alt = "打赏二维码";
    img.src = "assets/QRCode.png";
  }

  function bindBili(root) {
    if (!root) return;
    root.querySelectorAll("[data-bili]").forEach((el) => {
      el.addEventListener("click", (e) => {
        e.preventDefault();
        if (window.App && window.App.openExternal) window.App.openExternal(BILI_URL);
      });
    });
  }

  function sponsorCardHtml(qrId) {
    const tv = (window.ICONS && window.ICONS.tv) || "";
    return `
      <aside class="sponsor" aria-label="支持作者">
        <p class="sponsor-kicker">支持作者</p>
        <div class="qr" id="${qrId}">赞助二维码</div>
        <p class="sponsor-lead">请一杯咖啡</p>
        <button type="button" class="sponsor-bili" data-bili aria-label="打开 B 站主页 ${BILI_HOST}">
          ${tv}<span>B 站主页</span>
        </button>
      </aside>`;
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
        </div>
        <div class="modal-body">
          <div class="welcome-grid">
            <div class="welcome-main">
              <p class="welcome-greet">你好，我是 RyuuJi。愿读写 Markdown 这件事，始终轻快、专注、不被打扰。</p>
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
            ${sponsorCardHtml("welcome-qr")}
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
    fillQr(document.getElementById("welcome-qr"));
    bindBili(mask);
    document.getElementById("welcome-start").addEventListener("click", finish);
  }

  function closeSponsor() {
    if (!sponsorMask || !sponsorMask.classList.contains("open")) return;
    if (window.App && window.App.unregisterEscape) window.App.unregisterEscape(closeSponsor);
    sponsorMask.classList.remove("open");
    sponsorMask.innerHTML = "";
    if (sponsorFocus && typeof sponsorFocus.focus === "function") {
      try { sponsorFocus.focus(); } catch (e) { /* 原焦点可能已卸 */ }
    }
    sponsorFocus = null;
  }

  function showSponsor() {
    if (!sponsorMask) return;
    sponsorFocus = document.activeElement;
    sponsorMask.innerHTML = `
      <div class="modal sponsor-dialog" role="dialog" aria-modal="true" aria-label="支持作者">
        <div class="modal-head">
          <span class="badge">${(window.ICONS && window.ICONS.coffee) || ""}</span>
          <div><h2>支持作者</h2><p>扫码请一杯咖啡，或去 B 站看看</p></div>
        </div>
        <div class="modal-body">${sponsorCardHtml("sponsor-qr")}</div>
        <div class="modal-foot">
          <button class="btn btn--primary" id="sponsor-close" type="button">关闭</button>
        </div>
      </div>`;
    fillQr(document.getElementById("sponsor-qr"));
    bindBili(sponsorMask);
    sponsorMask.classList.add("open");
    if (window.App && window.App.registerEscape) window.App.registerEscape(closeSponsor);
    const closer = document.getElementById("sponsor-close");
    if (closer) {
      closer.addEventListener("click", closeSponsor);
      closer.focus();
    }
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

  if (sponsorMask) {
    sponsorMask.addEventListener("click", (e) => {
      if (e.target === sponsorMask) closeSponsor();
    });
  }

  window.Welcome = { show, close, isOpen };
  window.Sponsor = { show: showSponsor, close: closeSponsor };
})();
