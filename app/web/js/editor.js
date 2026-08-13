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
  let pendingScroll = null;  // 模式切换时保存的阅读位置 { ratio, content }
  let queuedMode = null;     // 重建期间收到的目标模式（快速连切），重建完成后补切
  let curTheme = "light";
  let curMode = "ir";        // ir（渲染/即时渲染，Typora 式）| sv（源码）
  let everBuilt = false;     // 是否曾成功初始化（after 触发过）——重建看门狗的前提
  let changeCb = null;
  let outlineCb = null;
  let onModeChange = null;

  function init({ theme, change, outline, onReady, mode, modeChange }) {
    onChange = change;
    onOutline = outline;
    changeCb = change;
    outlineCb = outline;
    onModeChange = modeChange;
    curTheme = theme === "dark" ? "dark" : "light";
    curMode = mode === "sv" ? "sv" : "ir";
    build(onReady);
  }

  function build(onReady) {
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
        math: { engine: "KaTeX" },
        markdown: { toc: true, footnotes: true, autoSpace: true },
      },
      after: () => {
        ready = true;
        everBuilt = true;
        setTheme(curTheme);
        if (window.SlashMenu) {
          window.SlashMenu.setEditor(vditor);
          window.SlashMenu.attach();
        }
        bindCheckboxGuard();
        if (onReady) onReady();
      },
      input: () => {
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
        if (ready) { clearInterval(guard); return; }
        // 就绪信号:当前模式面板已存在于 DOM。setMode 已 destroy 旧实例并清空容器,
        // 新面板出现即代表重建推进;不要求 offsetHeight(后台/重建布局可能滞后为 0)。
        const panel = document.querySelector(
          curMode === "sv" ? "#editor .vditor-sv" : "#editor .vditor-ir .vditor-reset"
        );
        if (vditor && panel) {
          clearInterval(guard);
          ready = true;
          if (pendingValue != null) {
            const pv = pendingValue;
            pendingValue = null;
            try { vditor.setValue(pv); } catch (e) { /* ignore */ }
          }
          bindCheckboxGuard();
          if (onReady) onReady();
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

  /* 待办勾选交给 Vditor 原生处理。
     历史教训:曾在此自绘守卫(捕获阶段 stopPropagation + 手动翻转 DOM checked 以
     避免 Vditor 整篇 Lute 重渲染),但守卫只改了 DOM、没更新 Vditor 内部 model,
     下一帧 Vditor 从 model 重渲染会把 checkbox 刷回原状——勾选完全失效。
     原生处理会正确同步源码 [ ]<->[x] 并重渲染,是唯一可靠路径,不要再加守卫。 */
  function bindCheckboxGuard() { /* 已废弃:保留空实现以免改动所有调用点 */ }

  /* 当前操作风格（notion / wolai）——交由 slash.js 处理，这里仅保留接口兼容。 */
  function setOpStyle(s) {
    if (window.SlashMenu) window.SlashMenu.setStyle(s);
  }

  /* 切换渲染/源码模式：保留内容与阅读位置，重建实例（Vditor 不支持运行时切 mode）。 */
  function setMode(nextMode) {
    const target = nextMode === "sv" ? "sv" : "ir";
    // 目标就是当前模式（含正在重建前往的模式）：撤销补切队列，无需动作
    if (target === curMode) { queuedMode = null; return; }
    if (!vditor || !ready) {
      // 重建进行中：只记录目标模式，不动 curMode（保持与在建实例一致），完成后补切。
      // 直接改 curMode 会让按钮状态/面板选择器与真实面板错位（快速连切实测可复现）。
      queuedMode = target;
      return;
    }
    const content = getValue();
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
  }

  function getValue() {
    // 用户编辑期间以 Vditor 为准;setValue 刚注入而 Vditor 尚未 settle(常见于
    // setValue 后立即 setMode)时,Vditor 可能仍返回旧值/空,此时回退到缓存,
    // 避免「切文件后立刻切模式丢内容」。
    const v = vditor && ready ? vditor.getValue() : "";
    return v === "" && cachedValue ? cachedValue : v;
  }

  function setValue(md, restoreScroll) {
    cachedValue = md || "";
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

  function jumpTo(id) {
    const el = document.querySelector(`[data-ryuu-id="${id}"]`);
    if (el) el.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  function focus() {
    if (vditor && ready) vditor.focus();
  }

  window.Editor = {
    init,
    setTheme,
    setOpStyle,
    setMode,
    getMode,
    getValue,
    setValue,
    jumpTo,
    focus,
    isReady: () => ready,
  };
})();
