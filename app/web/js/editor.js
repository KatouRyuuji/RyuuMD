/* Vditor 编辑器封装：初始化、内容存取、大纲提取、标题跳转、模式切换。
   - 渲染模式：ir（即时渲染，Typora 式，不显示 md 源码标记）
   - 源码模式：sv + preview.mode:"editor"（纯源码，不显示渲染预览分屏）
   命令菜单（"/" 与右键）由 slash.js 统一驱动，不使用 Vditor 原生 hint。 */
(function () {
  let vditor = null;
  let onChange = null;       // 内容变化回调（防抖后）
  let onOutline = null;      // 大纲更新回调
  let ready = false;
  let debounceTimer = null;
  let changeDelay = 250;     // 变更防抖间隔；大文档拉长以限流全文序列化/遍历
  let pendingValue = null;   // 模式切换重建期间提交的内容，重建完成后再应用
  let cachedValue = "";      // setValue 注入的原文缓存：Vditor 未 settle 时 getValue 的回退
  let editedSinceSet = false; // setValue 后是否有真实用户输入（input 回调置位）；
                             // 有则 getValue 信任 Vditor（含用户把文档清空返回 "" 的合法情形），
                             // 不再回退缓存——否则清空操作会被旧缓存「复活」，脏标记与保存全错。
                             // Vditor setValue 走 enableInput:false，不会触发 input，置位只来自真实编辑。
  let pendingScroll = null;  // 模式切换时保存的阅读位置 { ratio, content }
  let queuedMode = null;     // 重建期间收到的目标模式（快速连切），重建完成后补切
  let curTheme = "light";
  let curMode = "ir";        // ir（渲染/即时渲染，Typora 式）| sv（源码）
  let curMathEngine = "KaTeX"; // 公式引擎：KaTeX（默认，快）| MathJax（Typora 同款，宏兼容更广）
  let everBuilt = false;     // 是否曾成功初始化（after 触发过）——重建看门狗的前提
  let buildId = 0;           // 构建代数：销毁重建后作废旧实例的 after / 看门狗
  let changeCb = null;
  let outlineCb = null;
  let onModeChange = null;
  let getDocDir = null;    // () => 当前文档目录，用于相对图片转 file://

  /* Vditor 默认 flowchart.htmlLabels=true：测量用 foreignObject 会继承编辑区
     width（约 980px），每个节点被当成近千像素，viewBox 变成 2000×2000，
     图只占左上角。拦截 initialize，改用 SVG 文本标签。 */
  function patchMermaidInit() {
    const m = window.mermaid;
    if (!m || !m.initialize || m.initialize.__ryuu) return;
    const orig = m.initialize.bind(m);
    m.initialize = function (cfg) {
      cfg = Object.assign({}, cfg || {});
      cfg.flowchart = Object.assign({}, cfg.flowchart || {}, {
        htmlLabels: false,
        useMaxWidth: true,
      });
      return orig(cfg);
    };
    m.initialize.__ryuu = true;
  }
  if (window.mermaid) {
    patchMermaidInit();
  } else {
    try {
      let mermaidVal;
      Object.defineProperty(window, "mermaid", {
        configurable: true,
        enumerable: true,
        get() { return mermaidVal; },
        set(v) { mermaidVal = v; patchMermaidInit(); },
      });
    } catch (e) { /* 环境不允许再定义 mermaid 时走 getBBox 收缩 */ }
  }

  function fitMermaidSvg(svg) {
    if (!svg || !svg.getBBox) return;
    let box;
    try { box = svg.getBBox(); } catch (e) { return; }
    if (!box || box.width < 2 || box.height < 2) return;
    const pad = 12;
    const w = box.width + pad * 2;
    const h = box.height + pad * 2;
    svg.setAttribute("viewBox",
      (box.x - pad) + " " + (box.y - pad) + " " + w + " " + h);
    svg.setAttribute("width", String(Math.ceil(w)));
    svg.setAttribute("height", String(Math.ceil(h)));
    svg.style.width = "auto";
    svg.style.maxWidth = "100%";
    svg.style.height = "auto";
    svg.style.display = "inline-block";
  }

  function fitAllMermaid(root) {
    const el = root || document;
    if (!el.querySelectorAll) return;
    el.querySelectorAll(".language-mermaid svg").forEach(fitMermaidSvg);
  }

  function bindMermaidFit() {
    const host = document.getElementById("editor");
    if (!host || host._ryuuMermaidFit) return;
    host._ryuuMermaidFit = true;
    let t = 0;
    const mo = new MutationObserver(() => {
      clearTimeout(t);
      t = setTimeout(() => fitAllMermaid(host), 40);
    });
    mo.observe(host, { childList: true, subtree: true });
  }

  function init({ theme, change, outline, onReady, mode, modeChange, docDir, mathEngine }) {
    onChange = change;
    onOutline = outline;
    changeCb = change;
    outlineCb = outline;
    onModeChange = modeChange;
    getDocDir = docDir || null;
    curTheme = theme === "dark" ? "dark" : "light";
    curMode = mode === "sv" ? "sv" : "ir";
    curMathEngine = mathEngine === "mathjax" ? "MathJax" : "KaTeX";
    if (vditor) {
      rebuild(onReady);
      return;
    }
    build(onReady);
  }

  /* 销毁当前实例并重建。ensureEditor 看门狗在首次 after 丢失时走这条路径。 */
  function rebuild(onReady) {
    ready = false;
    if (vditor) {
      try { vditor.destroy(); } catch (e) { /* ignore */ }
      vditor = null;
    }
    const el = document.getElementById("editor");
    if (el) el.innerHTML = "";
    // 销毁重建时面板会很快出现，启用现有面板看门狗兜底 after/rAF 丢失
    everBuilt = true;
    build(onReady);
  }

  function build(onReady) {
    const myId = ++buildId;
    let readyNotified = false;
    // after 与面板看门狗可能先后触发：只通知一次。
    // 否则第二次 onReady 会用 setMode 闭包里的旧 content 再 setValue，盖掉新文档。
    function notifyReady() {
      if (myId !== buildId || readyNotified) return;
      readyNotified = true;
      ready = true;
      everBuilt = true;
      setTheme(curTheme);
      if (window.SlashMenu) {
        window.SlashMenu.setEditor(vditor);
        window.SlashMenu.attach();
      }
      bindCheckboxGuard();
      bindEditorClicks();
      bindMermaidFit();
      fitAllMermaid(document.getElementById("editor"));
      if (onReady) onReady();
    }
    vditor = new Vditor("editor", {
      mode: curMode,
      cdn: "vendor/vditor",
      cache: { enable: false },
      toolbar: [],
      toolbarConfig: { hide: true },
      counter: { enable: false },
      placeholder: "输入正文，按 / 唤起命令菜单，或右键插入……",
      preview: {
        // sv 模式默认 both（左源码右渲染预览分屏）；editor = 纯源码不显示渲染结果。
        // 隐藏后 preview.render 内部有 display:none 守卫，输入时不会白跑渲染。
        mode: "editor",
        maxWidth: 9999, // 内容区全宽设计；不设则 Vditor 默认 800px 并写行内 padding 居中
        theme: { current: curTheme === "dark" ? "dark" : "light" },
        // vendor 内置 hljs 主题不含 dracula（会 404 导致暗色无高亮），暗色用 github-dark
        hljs: { style: curTheme === "dark" ? "github-dark" : "github" },
        // inlineDigit 对标 Typora：$100$ 这类 $ 后紧跟数字的行内公式也渲染；
        // macros 预留给用户宏（Typora 无此 UI，暂不暴露设置项）
        math: { engine: curMathEngine, inlineDigit: true, macros: {} },
        markdown: { toc: true, footnotes: true, autoSpace: true },
      },
      after: () => { notifyReady(); },
      input: () => {
        editedSinceSet = true; // 真实编辑到达：Vditor model 已活，getValue 以它为准
        if (window.SlashMenu) window.SlashMenu.attach();
        scheduleChange();
      },
    });
    // 重建就绪看门狗:setMode 重建后,Vditor 的 after 回调依赖 rAF——窗口失焦/后台时
    // rAF 被抑制,after 可能永不触发,导致 ready 永 false(编辑器看似卡死、切模式无响应)。
    // 仅在重建(everBuilt=true,即编辑器曾成功初始化)时启用:此时面板结构必然已建成,
    // 以「当前模式面板已生成且有高度」为客观就绪信号兜底,不依赖 rAF。首启不启用,
    // 避免在编辑器尚未建成时误置 ready。
    if (everBuilt) {
      let n = 0;
      const guard = setInterval(() => {
        n++;
        if (myId !== buildId) { clearInterval(guard); return; }
        if (ready) { clearInterval(guard); return; }
        // 就绪信号:当前模式面板已存在于 DOM。setMode 已 destroy 旧实例并清空容器,
        // 新面板出现即代表重建推进;不要求 offsetHeight(后台/重建布局可能滞后为 0)。
        const panel = document.querySelector(
          curMode === "sv" ? "#editor .vditor-sv" : "#editor .vditor-ir .vditor-reset"
        );
        if (vditor && panel) {
          clearInterval(guard);
          // 与 after 共用 notifyReady：避免看门狗先就绪、after 再带旧 content 覆盖
          notifyReady();
        } else if (n > 150) { // ~15s 放弃,避免无限轮询
          clearInterval(guard);
        }
      }, 100);
    }
  }

  /* 当前模式的可见编辑面板（亦是滚动容器）。
     Vditor 会把 wysiwyg/sv/ir 三个面板的 DOM 全部创建、仅显示当前模式的那个，
     querySelector 首个 [contenteditable] 会命中排在前面的隐藏空面板——
     必须按模式定位，否则大纲提取/滚动控制全部作用在隐藏面板上失效。 */
  function activePanel() {
    return document.querySelector(
      curMode === "sv" ? "#editor .vditor-sv" : "#editor .vditor-ir .vditor-reset"
    );
  }

  /* 待办勾选由 Vditor 原生处理，与源码 [ ]/[x] 同步。 */
  function bindCheckboxGuard() { /* 空实现：调用点仍在，行为由 Vditor 原生勾选完成。 */ }

  /* 当前操作风格（notion / wolai）——交由 slash.js 处理，这里仅保留接口兼容。 */
  function setOpStyle(s) {
    if (window.SlashMenu) window.SlashMenu.setStyle(s);
  }

  /* 切换渲染/源码模式：保留内容与阅读位置，重建实例（Vditor 不支持运行时切 mode）。 */
  function setMode(nextMode) {
    const target = nextMode === "sv" ? "sv" : "ir";
    // 目标就是当前模式（含正在重建前往的模式）：撤销补切队列，无需动作
    if (target === curMode) { queuedMode = null; return; }
    if (!vditor) {
      // 尚未创建实例：只记下目标模式，ensureEditor/init 会用 curMode
      curMode = target;
      queuedMode = null;
      return;
    }
    if (!ready) {
      // 重建进行中：只记录目标模式，不动 curMode（保持与在建实例一致），完成后补切。
      // 直接改 curMode 会让按钮状态/面板选择器与真实面板错位（快速连切实测可复现）。
      queuedMode = target;
      return;
    }
    rebuildTo(target);
  }

  /* 保内容重建：内容、阅读位置（按滚动比例）都跨重建保留。
     setMode 与公式引擎切换（refresh）共用。 */
  function rebuildTo(target) {
    // setValue 刚写入时 Vditor 可能尚未 settle，getValue() 仍是旧全文。
    // 用户未再编辑则以 cachedValue 为准，避免大文档→小文档切模式把旧文带进重建。
    const content = !editedSinceSet ? cachedValue : getValue();
    // 记录当前阅读位置：两种模式内容高度不同（渲染 vs 源码），按滚动比例还原最稳。
    // 绑定 content 快照：仅当重建后装回的仍是同一内容（纯切换）才还原，
    // 装载新文档（如 loadDoc 大文档强制 sv）仍回文首。
    const sc = activePanel();
    if (sc) {
      const max = sc.scrollHeight - sc.clientHeight;
      pendingScroll = { ratio: max > 0 ? sc.scrollTop / max : 0, content };
    } else {
      pendingScroll = null;
    }
    curMode = target;
    ready = false;
    // destroy 前把携带内容写进缓存：重建窗口内 getValue(!ready) 以此为准
    cachedValue = content;
    editedSinceSet = false;
    try { vditor.destroy(); } catch (e) { /* ignore */ }
    document.getElementById("editor").innerHTML = "";
    build(() => {
      // 重建期间若有新内容提交（如切模式后立刻 setValue），以新内容为准
      const next = pendingValue != null ? pendingValue : content;
      pendingValue = null;
      const restore = pendingScroll && next === pendingScroll.content ? pendingScroll : null;
      pendingScroll = null;
      setValue(next, restore);
      if (onModeChange) onModeChange(curMode);
      // 重建期间又有切换请求 → 补切到最终目标模式
      const q = queuedMode;
      queuedMode = null;
      if (q && q !== curMode) setMode(q);
    });
  }

  /* 同模式强制重建：公式引擎等 preview 类配置只在 build 时生效，切换后需重建。 */
  function refresh() {
    if (!vditor || !ready) return; // 未建/重建中：build 已读最新配置，无需动作
    rebuildTo(curMode);
  }

  /* 切换公式引擎（KaTeX/MathJax）：只记配置；已就绪实例走保内容重建生效。 */
  function setMathEngine(engine) {
    const next = engine === "mathjax" || engine === "MathJax" ? "MathJax" : "KaTeX";
    if (next === curMathEngine) return;
    curMathEngine = next;
    refresh();
  }

  function getMode() { return curMode; }

  /* 按内容长度调整防抖间隔：每次回调都要全文序列化+遍历（字数/大纲），
     超长文档必须限流，否则连续输入会持续卡顿 */
  function noteSize(len) {
    changeDelay = len > 300000 ? 1200 : 250;
  }

  function scheduleChange() {
    clearTimeout(debounceTimer);
    debounceTimer = setTimeout(() => {
      const v = getValue();
      noteSize(v.length);
      // 把取到的值传给消费方，避免各处再各自 getValue 重复序列化
      if (onChange) onChange(v);
      updateOutline(v);
      enhanceRendered();
    }, changeDelay);
  }

  function setTheme(theme) {
    curTheme = theme === "dark" ? "dark" : "light";
    if (!vditor || !ready) return;
    const dark = curTheme === "dark";
    try {
      vditor.setTheme(
        dark ? "dark" : "classic",
        dark ? "dark" : "light",
        dark ? "github-dark" : "github"
      );
    } catch (e) { /* 忽略主题切换异常 */ }
    rerenderMermaid();
  }

  /* 主题切换后按新主题重渲染 mermaid 图。
     渲染产物是 svg；图源码在代码块的 pre 兄弟节点里（```mermaid 围栏行在 pre
     之外的 marker span，pre.textContent 即纯源码——实测取证）。把源码写回预览
     节点、清掉已渲染标记，再走 Vditor 自带渲染管线；结构对不上时跳过该图
     （下次输入会按新主题自然重渲染），不影响编辑。 */
  function rerenderMermaid() {
    if (curMode !== "ir" || !window.Vditor || !Vditor.mermaidRender) return;
    const el = activePanel();
    if (!el) return;
    let dirty = false;
    el.querySelectorAll(".vditor-ir__preview").forEach((pv) => {
      const inner = pv.querySelector(".language-mermaid");
      if (!inner || inner.getAttribute("data-processed") !== "true") return;
      const pre = pv.previousElementSibling;
      if (!pre) return;
      let src = (pre.textContent || "").replace(/\u200B/g, "");
      // 结构变体兜底：若 pre 里仍带 ``` 围栏行则剥掉
      const fenced = src.match(/^\s*```[^\n]*\n([\s\S]*?)\n?```\s*$/);
      if (fenced) src = fenced[1];
      if (!src.trim()) return;
      inner.textContent = src;
      inner.removeAttribute("data-processed");
      dirty = true;
    });
    if (dirty) {
      try { Vditor.mermaidRender(el, "vendor/vditor", curTheme); } catch (e) { /* ignore */ }
      setTimeout(() => fitAllMermaid(el), 80);
      setTimeout(() => fitAllMermaid(el), 400);
    }
  }

  function getValue() {
    // 三态取值：
    // 1) 重建进行中(!ready)：Vditor 不可用，唯一权威是 setMode 携带/暂存的内容
    //    （cachedValue 由 setMode 在 destroy 前刷新为切换前全文），此时绝不能
    //    把 "" 透传出去——否则重建窗口内的保存会把磁盘文件清空；
    // 2) 就绪且非空：以 Vditor 为准；
    // 3) 就绪但返回空：setValue 刚注入而 Vditor 尚未 settle(常见于 setValue 后
    //    立即 setMode)时回退缓存；但 editedSinceSet 置位后(用户真实编辑过)，
    //    空串是合法的「用户清空了文档」，必须原样返回，不能回退缓存复活旧内容。
    //    (Vditor setValue 走 enableInput:false 不触发 input，置位只来自真实编辑)
    if (!vditor) return cachedValue || "";
    if (!ready) return cachedValue;
    const v = vditor.getValue();
    return v === "" && cachedValue && !editedSinceSet ? cachedValue : v;
  }

  function setValue(md, restoreScroll) {
    cachedValue = md || "";
    editedSinceSet = false;
    if (!vditor || !ready) {
      // 编辑器正在重建（setMode 中）：先暂存，build 完成后由回调应用，不丢内容
      pendingValue = md || "";
      return;
    }
    noteSize((md || "").length);
    vditor.setValue(md || "");
    // 阅读位置：纯模式切换（restoreScroll 有值）按比例还原；装载新文档一律回文首
    // （切换文件不得停留在上一篇的滚动位置）
    const applyPos = () => {
      const sc = activePanel();
      if (!sc) return;
      if (restoreScroll) {
        const max = sc.scrollHeight - sc.clientHeight;
        sc.scrollTop = Math.round(restoreScroll.ratio * Math.max(max, 0));
      } else {
        sc.scrollTop = 0;
      }
    };
    applyPos();
    setTimeout(() => {
      applyPos(); // Vditor 渲染/聚焦可能异步改动滚动，再置一次
      updateOutline(md || "");
      enhanceRendered();
      if (window.SlashMenu) window.SlashMenu.attach();
    }, 60);
    // 图片/公式异步撑高内容后再校正一次（仅还原场景需要）
    if (restoreScroll) setTimeout(applyPos, 240);
  }

  /* 提取标题生成大纲。渲染模式从 DOM 取并补 id；源码模式解析 md 文本。
     md 可由调用方传入，省去一次全文序列化。 */
  function updateOutline(md) {
    const list = [];
    if (curMode === "sv") {
      const lines = (md != null ? md : getValue()).split("\n");
      let inFence = false;
      lines.forEach((line) => {
        if (/^\s*```/.test(line)) { inFence = !inFence; return; }
        if (inFence) return;
        const m = line.match(/^(#{1,6})\s+(.*\S)\s*$/);
        if (m) list.push({ id: "", level: m[1].length, text: m[2].trim() });
      });
    } else {
      const el = activePanel();
      if (!el) return;
      el.querySelectorAll("h1,h2,h3,h4,h5,h6").forEach((h, i) => {
        const id = "ryuu-h-" + i;
        h.setAttribute("data-ryuu-id", id);
        // textContent 会带上 IR 标记 span 的 "# " 前缀，剔除后再进大纲
        const text = h.textContent.trim().replace(/^#{1,6}\s+/, "");
        list.push({ id, level: parseInt(h.tagName[1], 10), text });
      });
    }
    if (onOutline) onOutline(list);
  }

  function scrollPanelTo(node) {
    const sc = activePanel();
    if (!sc || !node) return;
    const delta = node.getBoundingClientRect().top - sc.getBoundingClientRect().top;
    sc.scrollTop = Math.max(0, sc.scrollTop + delta - 10);
  }

  function jumpTo(id) {
    if (!id) return;
    let el = document.querySelector(`[data-ryuu-id="${id}"]`);
    if (!el) {
      updateOutline();
      el = document.querySelector(`[data-ryuu-id="${id}"]`);
    }
    if (el) scrollPanelTo(el);
  }

  function headingMatches(text, want) {
    const t = String(text || "").trim().replace(/^#{1,6}\s+/, "").toLowerCase();
    return t === want || (want.length >= 2 && t.indexOf(want) >= 0);
  }

  function jumpToHeading(text) {
    const want = String(text || "").trim().toLowerCase();
    if (!want) return false;
    // 先重盖 data-ryuu-id：Vditor 异步重渲染会把属性刷掉，大纲列表仍是旧 id
    updateOutline();
    const items = document.querySelectorAll("#outline-list .outline-item");
    for (let i = items.length - 1; i >= 0; i--) {
      if (!headingMatches(items[i].textContent, want)) continue;
      const id = items[i].dataset.id;
      if (id) {
        jumpTo(id);
        return true;
      }
      break;
    }
    const el = activePanel();
    if (!el) return false;
    if (curMode === "ir") {
      const hs = el.querySelectorAll("h1,h2,h3,h4,h5,h6");
      for (let i = hs.length - 1; i >= 0; i--) {
        if (headingMatches(hs[i].textContent, want)) {
          scrollPanelTo(hs[i]);
          return true;
        }
      }
    } else {
      // 源码模式无标题 DOM id，按 md 行号比例滚到对应标题
      const lines = getValue().split("\n");
      let idx = -1;
      for (let i = lines.length - 1; i >= 0; i--) {
        const m = lines[i].match(/^(#{1,6})\s+(.*\S)\s*$/);
        if (m && headingMatches(m[2], want)) { idx = i; break; }
      }
      if (idx >= 0) {
        const max = el.scrollHeight - el.clientHeight;
        if (max > 0) el.scrollTop = Math.round((idx / Math.max(lines.length - 1, 1)) * max);
        return true;
      }
    }
    return false;
  }

  function jumpToLine(line, snippet) {
    const n = parseInt(line, 10);
    const needle = (snippet || "").trim();
    if (needle) {
      try {
        const ok = window.find(needle.slice(0, 80), false, false, true, false, true, false);
        if (ok) return true;
      } catch (e) { /* ignore */ }
    }
    if (!n || n < 1) return false;
    const md = getValue();
    const lines = md.split("\n");
    const idx = Math.min(n, lines.length) - 1;
    const hint = (lines[idx] || "").trim();
    if (hint) {
      try {
        if (window.find(hint.slice(0, 80), false, false, true, false, true, false)) return true;
      } catch (e) { /* ignore */ }
    }
    const el = activePanel();
    if (!el) return false;
    const max = el.scrollHeight - el.clientHeight;
    if (max > 0) el.scrollTop = Math.round((idx / Math.max(lines.length - 1, 1)) * max);
    return true;
  }

  function getScrollRatio() {
    const sc = activePanel();
    if (!sc) return 0;
    const max = sc.scrollHeight - sc.clientHeight;
    return max > 0 ? sc.scrollTop / max : 0;
  }

  function setScrollRatio(ratio) {
    const sc = activePanel();
    if (!sc) return;
    const max = sc.scrollHeight - sc.clientHeight;
    sc.scrollTop = Math.round((ratio || 0) * Math.max(max, 0));
  }

  function focus() {
    if (vditor && ready) vditor.focus();
  }

  function insertValue(text) {
    if (!vditor || !ready || text == null) return;
    try {
      vditor.focus();
      vditor.insertValue(String(text), true);
    } catch (e) { /* ignore */ }
  }

  /* 程序化全文替换（convert.js 容器块操作）后手动走一遍变化管线：
     setValue 走 enableInput:false 不触发 input，不调本函数则脏标记缺失、
     自动保存与关闭保存提示都会漏掉这次修改。 */
  function notifyChange() {
    editedSinceSet = true;
    scheduleChange();
  }

  function getHTML() {
    if (!vditor || !ready) return "";
    try { return vditor.getHTML() || ""; } catch (e) { return ""; }
  }

  /* 相对路径图片在 file:// 应用页下会指到 app/web/，改写为当前文档目录的绝对 file URL。 */
  function toFileUrl(abs) {
    let p = String(abs || "").replace(/\\/g, "/");
    if (!p) return "";
    if (/^[a-zA-Z]:/.test(p)) p = "/" + p;
    return "file://" + encodeURI(p).replace(/#/g, "%23");
  }

  function resolveRel(baseDir, rel) {
    const left = String(baseDir || "").replace(/\\/g, "/").split("/").filter(Boolean);
    // Windows 盘符段（C:）必须保留
    const drive = String(baseDir || "").match(/^([a-zA-Z]:)/);
    String(rel || "").replace(/\\/g, "/").split("/").forEach((seg) => {
      if (!seg || seg === ".") return;
      if (seg === "..") { if (left.length && !/^[a-zA-Z]:$/.test(left[left.length - 1])) left.pop(); }
      else left.push(seg);
    });
    if (drive && left[0] !== drive[1]) left.unshift(drive[1]);
    return left.join("\\");
  }

  function enhanceRendered() {
    if (curMode !== "ir") return;
    const el = activePanel();
    if (!el) return;
    rewriteMedia(el);
    decorateWikilinks(el);
  }

  function rewriteMedia(el) {
    const dir = getDocDir && getDocDir();
    if (!dir) return;
    el.querySelectorAll("img").forEach((img) => {
      const src = img.getAttribute("src") || "";
      if (!src || /^(https?:|data:|file:|blob:)/i.test(src)) return;
      img.setAttribute("data-rel-src", src);
      img.src = toFileUrl(resolveRel(dir, src));
    });
  }

  function decorateWikilinks(root) {
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, {
      acceptNode(n) {
        const p = n.parentElement;
        if (!p) return NodeFilter.FILTER_REJECT;
        // 守卫：跳过代码/已有链接/已装饰节点。注意 Vditor IR 的内容根本身就是
        // <pre class="vditor-reset">（即 root），closest 必命中它——命中 root 不算，
        // 否则全篇永远被拒（该守卫曾致双链装饰整体失效）。
        const guard = p.closest("code, pre, a, .wiki-link");
        if (guard && guard !== root) return NodeFilter.FILTER_REJECT;
        if (!n.nodeValue || n.nodeValue.indexOf("[[") < 0) return NodeFilter.FILTER_REJECT;
        return NodeFilter.FILTER_ACCEPT;
      },
    });
    const nodes = [];
    while (walker.nextNode()) nodes.push(walker.currentNode);
    const re = /\[\[([^\]\n]+)\]\]/g;
    nodes.forEach((node) => {
      const text = node.nodeValue;
      re.lastIndex = 0;
      if (!re.test(text)) return;
      re.lastIndex = 0;
      const frag = document.createDocumentFragment();
      let last = 0, m;
      while ((m = re.exec(text))) {
        if (m.index > last) frag.appendChild(document.createTextNode(text.slice(last, m.index)));
        const span = document.createElement("span");
        span.className = "wiki-link";
        span.dataset.wiki = m[1].split("|")[0].split("#")[0].trim();
        span.textContent = m[0];
        frag.appendChild(span);
        last = m.index + m[0].length;
      }
      if (last < text.length) frag.appendChild(document.createTextNode(text.slice(last)));
      node.parentNode.replaceChild(frag, node);
    });
  }

  function wikiAtPoint(e) {
    const span = e.target.closest && e.target.closest(".wiki-link");
    if (span && span.dataset.wiki) return span.dataset.wiki;
    if (!(e.ctrlKey || e.metaKey)) return null;
    if (!document.caretRangeFromPoint) return null;
    const range = document.caretRangeFromPoint(e.clientX, e.clientY);
    if (!range || range.startContainer.nodeType !== Node.TEXT_NODE) return null;
    const node = range.startContainer;
    const text = node.textContent || "";
    const off = range.startOffset;
    const left = text.lastIndexOf("[[", off);
    const right = text.indexOf("]]", off);
    if (left < 0 || right < 0 || right < left) return null;
    return text.slice(left + 2, right).split("|")[0].split("#")[0].trim();
  }

  let clicksBound = false;
  function bindEditorClicks() {
    const host = document.getElementById("editor");
    if (!host || host.__ryuuClicks) return;
    host.__ryuuClicks = true;
    host.addEventListener("click", (e) => {
      const wiki = wikiAtPoint(e);
      if (wiki && window.App && window.App.openWikilink) {
        e.preventDefault();
        e.stopPropagation();
        window.App.openWikilink(wiki);
        return;
      }
      const a = e.target.closest && e.target.closest("a");
      if (!a) return;
      const href = a.getAttribute("href") || "";
      if (!href) return;
      e.preventDefault();
      e.stopPropagation();
      if (/^https?:/i.test(href) || href.indexOf("mailto:") === 0) {
        if (window.App && window.App.openExternal) window.App.openExternal(href);
        return;
      }
      if (window.App && window.App.openRelLink) window.App.openRelLink(href);
    });
  }

  /* 专注模式的「当前块」追踪：CSS 不能用 :focus-within 判定光标块——编辑器失焦
     （点状态栏/侧栏/切窗口）时全文会连正在编辑的块一起淡化。改为 selectionchange
     驱动的显式 .focus-current 标记，淡化规则只排除它（与 hover）。 */
  let focusCurrentEl = null;
  let focusTrackTimer = null;

  function clearFocusCurrent() {
    if (focusCurrentEl) focusCurrentEl.classList.remove("focus-current");
    focusCurrentEl = null;
  }

  function trackFocusBlock() {
    const wrap = document.getElementById("editor-wrap");
    if (!wrap || !wrap.classList.contains("focus-mode") || curMode !== "ir") {
      clearFocusCurrent();
      return;
    }
    const panel = activePanel();
    const sel = window.getSelection();
    let el = sel && sel.anchorNode;
    if (!panel || !el || !panel.contains(el)) { clearFocusCurrent(); return; }
    if (el.nodeType !== Node.ELEMENT_NODE) el = el.parentElement;
    while (el && el.parentElement !== panel) el = el.parentElement;
    if (el === panel || !el) { clearFocusCurrent(); return; }
    if (focusCurrentEl === el) return;
    clearFocusCurrent();
    focusCurrentEl = el;
    focusCurrentEl.classList.add("focus-current");
  }

  let focusTrackBound = false;
  function bindFocusTrack() {
    if (focusTrackBound) return;
    focusTrackBound = true;
    document.addEventListener("selectionchange", () => {
      clearTimeout(focusTrackTimer);
      focusTrackTimer = setTimeout(trackFocusBlock, 90);
    });
  }

  function toggleFocusMode(on) {
    const wrap = document.getElementById("editor-wrap");
    if (!wrap) return false;
    if (on == null) wrap.classList.toggle("focus-mode");
    else wrap.classList.toggle("focus-mode", !!on);
    const next = wrap.classList.contains("focus-mode");
    if (next) { bindFocusTrack(); trackFocusBlock(); }
    else clearFocusCurrent();
    return next;
  }

  window.Editor = {
    init,
    rebuild,
    setTheme,
    setOpStyle,
    setMode,
    getMode,
    setMathEngine,
    refresh,
    getValue,
    setValue,
    notifyChange,
    insertValue,
    getHTML,
    jumpTo,
    jumpToHeading,
    jumpToLine,
    getScrollRatio,
    setScrollRatio,
    focus,
    isReady: () => ready,
    toggleFocusMode,
    enhanceRendered,
  };
})();
