/* 结构操作交互层：操作柄、范围高亮、拖拽落点、柄菜单、键盘替代。
   只读 Blocks 的结构状态，绝不触碰 md 文本；所有提交走 Blocks/Convert 通道。

   交互边界（与正文严格分离）：
   - 正文保持连续编辑画布：鼠标在正文中拖动仍是选字，只有从操作柄开始的
     拖动才进入结构移动；overlay 元素全部挂 document.body（position:fixed），
     不插入编辑节点、不推挤正文、不进 md 序列化。
   - 通常只显示当前目标的一个柄：IR 模式悬停解析语义目标，双模式都跟随
     光标（selectionchange 防抖）；柄落在面板 padding 留白内。
   - 点击柄打开针对当前范围的菜单，操作前高亮完整范围；嵌套位置可切换
     「当前段落 / 当前列表项 / 整个列表」，标题可「选择整节」。
   - 拖拽落点同时说明位置与归属（插入线 + 文案："放在 X 前/后"、
     "成为 X 的子项"、"移入引用"）；松手在编辑区外 = 取消，Esc = 取消。
   - 键盘替代（WCAG 拖拽要求）：柄是可聚焦 button（Enter/Space 开菜单），
     菜单含上移/下移/移入/移出/删除；Ctrl+Alt+↑/↓ 移动光标所在块。
   - 中文输入法组词期间：抑制拖拽启动与结构快捷键。 */
(function () {
  let handleEl = null;   // 操作柄（button）
  let dropLine = null;   // 落点插入线
  let dropBadge = null;  // 落点归属提示
  let ghostEl = null;    // 拖拽残影
  let cur = null;        // 当前目标 {el, kind, loc, scope}
  let drag = null;       // 拖拽状态 {startX, startY, active, loc, probeText, drop}
  let composing = false;
  let hoverRaf = 0;
  let selTimer = 0;

  // ---------------------------------------------------------------
  // 目标解析
  // ---------------------------------------------------------------

  function panel() {
    return window.Editor && window.Editor.panel ? window.Editor.panel() : null;
  }

  function irPanel() {
    const p = panel();
    return p && p.classList.contains("vditor-reset") ? p : null;
  }

  function climb(el, stop) {
    let n = el && (el.nodeType === Node.ELEMENT_NODE ? el : el.parentElement);
    while (n && n !== stop) n = n.parentElement;
    return n === stop ? n : null;
  }

  /* 语义目标（严格按 item 边界表）：
     li → 当前列表项（含续段与子列表）；顶层 blockquote → 整个引用；
     h1-6 → 标题本身；table/code/math/hr → 整体；含图片的 p → 图片段落；
     行内元素永不设柄；普通空行（无文字无图片的空段落）不设柄。 */
  function targetForElement(node) {
    const p = irPanel();
    if (!p || !node) return null;
    let el = node.nodeType === Node.ELEMENT_NODE ? node : node.parentElement;
    if (!el || !p.contains(el)) return null;
    const li = el.closest && el.closest("li");
    if (li && p.contains(li)) return { el: li, kind: "item" };
    while (el && el.parentElement !== p) el = el.parentElement;
    if (!el) return null;
    const t = el.tagName;
    if (/^H[1-6]$/.test(t)) return { el, kind: "heading" };
    if (t === "BLOCKQUOTE") return { el, kind: "quote" };
    if (t === "TABLE" || t === "HR") return { el, kind: "special" };
    const dt = el.getAttribute && el.getAttribute("data-type");
    if (dt === "code-block" || dt === "math-block") return { el, kind: "special" };
    if (t === "P") {
      const empty = !(el.textContent || "").replace(/​/g, "").trim() && !el.querySelector("img");
      if (empty) return null; // 普通空行不包装成持久 item
      return { el, kind: "block" };
    }
    return null;
  }

  /* 目标身份：源码区间 + 首行剥标记文本（Vditor 重渲染换元素后按身份重定位） */
  function identityOf(t) {
    const loc = window.Blocks.locate(t.el);
    if (!loc) return null;
    return { kind: t.kind, el: t.el, loc, probe: window.Blocks.probeFirstLine(t.el) };
  }

  // ---------------------------------------------------------------
  // 操作柄与范围高亮
  // ---------------------------------------------------------------

  function ensureOverlays() {
    if (handleEl) return;
    handleEl = document.createElement("button");
    handleEl.className = "block-handle";
    handleEl.type = "button";
    handleEl.title = "结构操作（点击打开菜单，拖动移动）";
    handleEl.innerHTML = window.ICONS && window.ICONS.grip ? window.ICONS.grip : "⋮⋮";
    handleEl.addEventListener("mousedown", onHandleDown);
    handleEl.addEventListener("click", onHandleClick);
    handleEl.addEventListener("keydown", (e) => {
      if (e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        openMenu();
      }
    });
    document.body.appendChild(handleEl);

    dropLine = document.createElement("div");
    dropLine.className = "block-drop-line";
    document.body.appendChild(dropLine);
    dropBadge = document.createElement("div");
    dropBadge.className = "block-drop-badge";
    document.body.appendChild(dropBadge);
  }

  function positionHandle() {
    if (!handleEl || !cur) return;
    const r = cur.el.getBoundingClientRect();
    const p = panel();
    const pr = p.getBoundingClientRect();
    // x 对齐目标所在顶层块的左缘（列表项取整个列表的左缘），
    // 落在面板 padding 留白里，不与列表 marker 重叠
    let top = cur.el;
    while (top.parentElement && top.parentElement !== p) top = top.parentElement;
    const tr = top.getBoundingClientRect();
    const x = Math.max(pr.left + 4, tr.left - 28);
    handleEl.style.left = x + "px";
    handleEl.style.top = Math.max(r.top + 2, pr.top + 2) + "px";
  }

  function showHandle(t) {
    if (!t) { hideHandle(); return; }
    ensureOverlays();
    const id = identityOf(t);
    if (!id) { hideHandle(); return; }
    if (!cur || cur.loc.start !== id.loc.start || cur.kind !== id.kind) clearHighlight();
    cur = { el: t.el, kind: t.kind, loc: id.loc, probe: id.probe, scope: defaultScope(t) };
    positionHandle();
    handleEl.classList.add("on");
  }

  function hideHandle() {
    if (drag && drag.active) return;
    if (handleEl) handleEl.classList.remove("on");
    clearHighlight();
    cur = null;
  }

  function defaultScope(t) {
    if (t.kind === "item") return "item";
    if (t.kind === "heading") return "heading";
    if (t.kind === "quote") return "quote";
    return "block";
  }

  /* 包含 el 的顶层列表元素（panel 直接子级的 UL/OL）；无则 null */
  function topListOf(el) {
    const p = irPanel();
    if (!p) return null;
    let n = el;
    while (n && n.parentElement !== p) n = n.parentElement;
    return n && /^(UL|OL)$/.test(n.tagName) ? n : null;
  }

  function clearHighlight() {
    document.querySelectorAll(".ryuu-block-hl").forEach((el) => el.classList.remove("ryuu-block-hl"));
  }

  /* 按当前 scope 高亮完整范围（背景 tint，不改布局、不进 md） */
  function applyHighlight() {
    clearHighlight();
    if (!cur) return;
    if (!cur.el) return; // sv：span 流无块级元素，暂以落点线/菜单为准
    if (cur.scope === "section") {
      const r = sectionLoc();
      if (!r) return;
      const p = irPanel();
      if (!p) return;
      [...p.children].forEach((el) => {
        const l = window.Blocks.locate(el);
        if (l && l.start >= r.start && l.start < r.end) el.classList.add("ryuu-block-hl");
      });
      return;
    }
    if (cur.scope === "list" && cur.kind === "item") {
      const top = topListOf(cur.el);
      if (top) top.classList.add("ryuu-block-hl");
      return;
    }
    cur.el.classList.add("ryuu-block-hl");
  }

  /* 当前 scope 对应的操作区间（loc） */
  function scopeLoc() {
    if (!cur) return null;
    if (cur.scope === "section") return sectionLoc();
    if (cur.scope === "list" && cur.kind === "item") {
      const top = topListOf(cur.el);
      return top ? window.Blocks.locate(top) : null;
    }
    return cur.loc;
  }

  function sectionLoc() {
    if (!cur) return null;
    const lines = window.Editor.getValue().split("\n");
    const tree = window.Blocks.scanDoc(lines);
    const h = tree.find((b) => b.type === "h" && b.start === cur.loc.start);
    if (!h) return null;
    const r = window.Blocks.sectionRange(tree, h);
    return r ? { start: r[0], end: r[1] } : null;
  }

  // ---------------------------------------------------------------
  // 柄菜单
  // ---------------------------------------------------------------

  function menuFor() {
    if (!cur) return [];
    const items = [];
    const scopeName = { item: "列表项", list: "整个列表", heading: "标题", section: "整节",
      quote: "整个引用", block: "段落" }[cur.scope] || "块";
    items.push({ id: "bh-up", struct: { type: "move", dir: -1 }, icon: "arrowUp",
      title: "上移", desc: "", group: "移动", keys: "Ctrl+Alt+↑" });
    items.push({ id: "bh-down", struct: { type: "move", dir: 1 }, icon: "arrowDown",
      title: "下移", desc: "", group: "移动", keys: "Ctrl+Alt+↓" });
    if (cur.scope === "item") {
      items.push({ id: "bh-indent", struct: { type: "indent" }, icon: "indent",
        title: "移入（成为上一项的子项）", desc: "", group: "移动", keys: "" });
      items.push({ id: "bh-outdent", struct: { type: "outdent" }, icon: "outdent",
        title: "移出（提升一级）", desc: "", group: "移动", keys: "" });
    }
    if (cur.kind === "item" && cur.scope === "item") {
      items.push({ id: "bh-scope-list", struct: { type: "scope", scope: "list" }, icon: "listUnordered",
        title: "改为操作整个列表", desc: "", group: "范围", keys: "" });
    }
    if (cur.kind === "item" && cur.scope === "list") {
      items.push({ id: "bh-scope-item", struct: { type: "scope", scope: "item" }, icon: "listUnordered",
        title: "改为操作当前列表项", desc: "", group: "范围", keys: "" });
    }
    if (cur.kind === "heading" && cur.scope !== "section") {
      items.push({ id: "bh-scope-section", struct: { type: "scope", scope: "section" }, icon: "heading",
        title: "选择整节", desc: "含正文与子节", group: "范围", keys: "" });
    }
    if (cur.scope === "section") {
      items.push({ id: "bh-scope-heading", struct: { type: "scope", scope: "heading" }, icon: "heading",
        title: "改为只操作标题", desc: "", group: "范围", keys: "" });
      items.push({ id: "bh-promote", struct: { type: "shift-section", delta: -1 }, icon: "outdent",
        title: "整节升级", desc: "子标题保持相对等级", group: "章节", keys: "" });
      items.push({ id: "bh-demote", struct: { type: "shift-section", delta: 1 }, icon: "indent",
        title: "整节降级", desc: "子标题保持相对等级", group: "章节", keys: "" });
    }
    if (cur.scope !== "section" && cur.kind !== "special" && cur.kind !== "sv") {
      // 转换组：复用 Convert.TARGETS；已是该格式则禁用
      const curKind = currentKindOf();
      (window.Convert ? window.Convert.targets() : []).forEach((t) => {
        items.push({ id: "bh-conv-" + t.id, struct: { type: "convert", target: t.id },
          icon: t.icon, title: t.title, desc: "", group: "转换为", keys: "",
          disabled: curKind === t.id });
      });
    }
    items.push({ id: "bh-del", struct: { type: "delete" }, icon: "trash",
      title: "删除" + scopeName, desc: "", group: "删除", keys: "" });
    return items;
  }

  /* 当前范围对应的 Convert 目标 id（用于禁用「已是该格式」） */
  function currentKindOf() {
    if (!cur) return null;
    if (cur.kind === "heading") return cur.el.tagName.toLowerCase();
    if (cur.kind === "quote") return "quote";
    if (cur.kind === "item" || cur.scope === "list") {
      const listEl = topListOf(cur.el);
      if (!listEl) return null;
      if (listEl.tagName === "OL") return "ol";
      return /^\s*[-*+]\s+\[[ xX]\]/.test(listEl.textContent || "") ? "todo" : "ul";
    }
    if (cur.kind === "block") return "paragraph";
    return null;
  }

  function openMenu() {
    if (!cur) return;
    applyHighlight();
    const r = handleEl.getBoundingClientRect();
    window.SlashMenu.showItems(menuFor(), r.right + 6, r.top);
  }

  /* 菜单动作统一入口（slash.js choose 的 cmd.struct 分发到这里） */
  function applyMenuAction(s) {
    if (!cur || !s) return;
    if (s.type === "scope") {
      cur.scope = s.scope;
      applyHighlight();
      openMenu();
      return;
    }
    const loc = scopeLoc();
    if (!loc) { toast("定位失败，操作已取消"); return; }
    let ok = false;
    switch (s.type) {
      case "move":
        ok = cur.scope === "item" ? window.Blocks.moveItem(loc, s.dir) : window.Blocks.moveBlock(loc, s.dir, cur.probe);
        break;
      case "indent":
        ok = window.Blocks.indentItem(loc);
        break;
      case "outdent":
        ok = window.Blocks.outdentItem(loc);
        break;
      case "delete":
        ok = window.Blocks.deleteBlock(loc, cur.probe);
        break;
      case "convert": {
        // 转换复用 Convert 通道：先把选区设到当前范围元素
        const el = cur.scope === "list" && cur.kind === "item" ? topListOf(cur.el) : cur.el;
        if (!el) { toast("定位失败，操作已取消"); return; }
        const sel = window.getSelection();
        const r = document.createRange();
        r.selectNodeContents(el);
        r.collapse(cur.kind === "item");
        sel.removeAllRanges();
        sel.addRange(r);
        ok = window.Convert.apply(s.target, window.Editor) > 0;
        break;
      }
      case "shift-section": {
        const res = window.Blocks.shiftSectionLevel(loc, s.delta);
        if (!res.ok) { toast(res.reason || "无法调整标题等级"); return; }
        ok = true;
        break;
      }
      default:
        return;
    }
    if (!ok) toast("操作未完成");
    else maybeRetarget(s, loc.start);
  }

  /* 操作成功后把柄重定位到被操作的块（Vditor 已重建 DOM）：按 probe 在全文找
     首行相同、距原位置最近的顶层块，柄留在它上面，可连续上移/下移/转换
     （Notion 操作后块保持选中同款）。列表项/整列表/删除/范围切换不重定位：
     项级重定位易错配，删除无块可留。 */
  function maybeRetarget(s, oldStart) {
    if (!cur || !cur.probe || s.type === "delete" || s.type === "scope" ||
        cur.kind === "item" || cur.kind === "sv" || cur.scope === "list") {
      hideHandle();
      return;
    }
    const probe = cur.probe;
    hideHandle(); // 先清旧引用与高亮（el 已被重建换掉）
    setTimeout(() => {
      const E = window.Editor;
      if (!E || E.getMode() === "sv") return;
      const lines = E.getValue().split("\n");
      let best = null;
      window.Blocks.scanDoc(lines).forEach((b) => {
        if (!probe(lines.slice(b.start, b.end))) return;
        const d = Math.abs(b.start - oldStart);
        if (!best || d < best.d) best = { b, d };
      });
      if (!best) return;
      const loc = window.Blocks.locateLine(best.b.start);
      if (!loc || !loc.node || !loc.node.el) return;
      const kind = loc.type === "h" ? "heading" : loc.type === "quote" ? "quote" : "block";
      showHandle({ el: loc.node.el, kind });
    }, 90);
  }

  function toast(msg) {
    if (window.App && window.App.toast) window.App.toast(msg);
  }

  // ---------------------------------------------------------------
  // 拖拽：仅从柄启动；落点 = 位置（前/后）+ 归属（子项/引用）
  // ---------------------------------------------------------------

  function onHandleDown(e) {
    if (e.button !== 0 || !cur || composing) return;
    e.preventDefault();
    if (window.SlashMenu && window.SlashMenu.isOpen && window.SlashMenu.isOpen()) window.SlashMenu.close();
    drag = {
      startX: e.clientX, startY: e.clientY, active: false,
      loc: cur.loc, probe: cur.probe, scope: cur.scope,
    };
    document.addEventListener("mousemove", onDragMove, true);
    document.addEventListener("mouseup", onDragUp, true);
  }

  function onHandleClick(e) {
    e.preventDefault();
    e.stopPropagation(); // 面板的「点击外部关闭」在 document 冒泡阶段，不能让它立刻关掉刚开的菜单
    if (drag && drag.active) return; // 拖拽结束的 click 不开菜单
    openMenu();
  }

  function startDrag() {
    drag.active = true;
    document.body.classList.add("ryuu-block-dragging");
    applyHighlight();
    ghostEl = document.createElement("div");
    ghostEl.className = "block-drag-ghost";
    ghostEl.textContent = cur.el
      ? (cur.el.textContent || "").replace(/​/g, "").trim().slice(0, 40) || "块"
      : (window.Editor.getValue().split("\n")[cur.loc.start] || "").trim().slice(0, 40) || "块";
    document.body.appendChild(ghostEl);
    if (window.App && window.App.registerEscape) {
      drag.escFn = () => cancelDrag();
      window.App.registerEscape(drag.escFn);
    }
  }

  function cancelDrag() {
    endDrag();
    clearHighlight();
    toast("已取消移动");
  }

  function endDrag() {
    if (ghostEl) { ghostEl.remove(); ghostEl = null; }
    if (dropLine) dropLine.classList.remove("on");
    if (dropBadge) dropBadge.classList.remove("on");
    document.body.classList.remove("ryuu-block-dragging");
    document.removeEventListener("mousemove", onDragMove, true);
    document.removeEventListener("mouseup", onDragUp, true);
    if (drag && drag.escFn && window.App && window.App.unregisterEscape) window.App.unregisterEscape(drag.escFn);
    drag = null;
  }

  /* 落点计算：y 中线分前/后；li 中段或深缩进 → 子项；引用居中 → 移入。
     代码块/表格/数学内部不接收「成为子项」。 */
  function computeDrop(x, y) {
    if (window.Editor.getMode() === "sv") return computeDropSV(x, y);
    const p = irPanel();
    if (!p) return null;
    const hit = document.elementFromPoint(x, y);
    if (!hit || !p.contains(hit)) return null;
    let el = hit.nodeType === Node.ELEMENT_NODE ? hit : hit.parentElement;
    const li = el.closest && el.closest("li");
    let block = el;
    while (block && block.parentElement !== p) block = block.parentElement;
    if (!block) return null;
    const rect = (li || block).getBoundingClientRect();
    const rel = (y - rect.top) / Math.max(rect.height, 1);
    const dt = block.getAttribute && block.getAttribute("data-type");
    const leaf = dt === "code-block" || dt === "math-block" || block.tagName === "TABLE";
    if (li && !leaf) {
      // 中段高度带（0.3~0.7）或明显深缩进 → 成为该项的子项
      if ((rel > 0.3 && rel < 0.7) || x > rect.left + 24 + (liDepth(li) * 12)) {
        return { kind: "child-of", li, text: "成为「" + brief(li) + "」的子项" };
      }
    }
    if (block.tagName === "BLOCKQUOTE" && rel > 0.3 && rel < 0.7) {
      return { kind: "into-quote", block, text: "移入引用" };
    }
    const before = rel <= 0.5;
    return { kind: before ? "before" : "after", block, before, text: "放在「" + brief(block) + "」" + (before ? "前" : "后") };
  }

  /* sv 落点：等行高面板，按 y 直接算行号 → 顶层块前/后。 */
  function computeDropSV(x, y) {
    const p = panel();
    if (!p) return null;
    const pr = p.getBoundingClientRect();
    if (y < pr.top || y > pr.bottom) return null;
    const cs = getComputedStyle(p);
    const lh = parseFloat(cs.lineHeight) || 20;
    const padTop = parseFloat(cs.paddingTop) || 0;
    const off = y - pr.top - padTop + p.scrollTop;
    const line = Math.max(0, Math.floor(off / lh));
    const before = (off % lh) < lh / 2;
    const loc = window.Blocks.locateLine(line);
    if (!loc) return null;
    const firstLine = (window.Editor.getValue().split("\n")[loc.start] || "").trim().slice(0, 12);
    return { kind: before ? "before" : "after", loc, before, text: "放在「" + (firstLine || "块") + "」" + (before ? "前" : "后"), svY: pr.top + padTop + (before ? loc.start : loc.end) * lh - p.scrollTop };
  }

  function liDepth(li) {
    let d = 0, n = li.parentElement;
    while (n) { if (n.tagName === "UL" || n.tagName === "OL") d++; n = n.parentElement; }
    return d;
  }

  function brief(el) {
    return ((el.textContent || "").replace(/​/g, "").trim().split("\n")[0] || "块").slice(0, 12);
  }

  function paintDrop(drop) {
    if (!drop) {
      dropLine.classList.remove("on");
      dropBadge.classList.remove("on");
      return;
    }
    if (drop.svY != null) {
      // sv：行边界已由 computeDropSV 折算为视口坐标
      const pr = panel().getBoundingClientRect();
      dropLine.style.top = drop.svY + "px";
      dropLine.style.left = pr.left + 20 + "px";
      dropLine.style.width = pr.width - 40 + "px";
      dropLine.classList.add("on");
      dropBadge.textContent = drop.text;
      dropBadge.style.top = drop.svY - 26 + "px";
      dropBadge.style.left = pr.left + 28 + "px";
      dropBadge.classList.add("on");
      return;
    }
    let y;
    if (drop.kind === "before") y = drop.block.getBoundingClientRect().top;
    else if (drop.kind === "after") y = drop.block.getBoundingClientRect().bottom;
    else y = (drop.li || drop.block).getBoundingClientRect().top + (drop.li || drop.block).getBoundingClientRect().height / 2;
    const p = irPanel();
    const pr = p.getBoundingClientRect();
    dropLine.style.top = y + "px";
    dropLine.style.left = pr.left + 20 + "px";
    dropLine.style.width = pr.width - 40 + "px";
    dropLine.classList.add("on");
    dropBadge.textContent = drop.text;
    dropBadge.style.top = y - 26 + "px";
    dropBadge.style.left = pr.left + 28 + "px";
    dropBadge.classList.add("on");
  }

  function onDragMove(e) {
    if (!drag) return;
    if (!drag.active) {
      if (Math.abs(e.clientX - drag.startX) + Math.abs(e.clientY - drag.startY) < 5) return;
      startDrag();
    }
    e.preventDefault();
    if (ghostEl) {
      ghostEl.style.left = e.clientX + 10 + "px";
      ghostEl.style.top = e.clientY + 8 + "px";
    }
    drag.drop = computeDrop(e.clientX, e.clientY);
    paintDrop(drag.drop);
  }

  function onDragUp(e) {
    if (!drag) return;
    const wasActive = drag.active;
    const drop = drag.drop;
    const loc = drag.loc;
    const probe = drag.probe;
    const scope = drag.scope;
    endDrag();
    clearHighlight();
    if (!wasActive || !drop) return; // 未达拖拽阈值 = 点击；编辑区外松手 = 取消
    // 提交前重新校验：文档可能在拖动期间变化
    const lines = window.Editor.getValue().split("\n");
    if (loc.start >= lines.length || (probe && !probe(lines.slice(loc.start, loc.end)))) {
      toast("内容已变化，移动已取消");
      return;
    }
    let ok = false;
    if (drop.kind === "before" || drop.kind === "after") {
      if (drop.loc) {
        // sv 落点：目标是行号解析出的区间
        ok = window.Blocks.moveBlockTo(loc, drop.loc, drop.kind === "before", false);
      } else {
        const target = window.Blocks.locate(drop.block);
        if (target && !(target.start === loc.start && target.end === loc.end)) {
          ok = window.Blocks.moveBlockTo(loc, target, drop.kind === "before", scope === "item");
        }
      }
    } else if (drop.kind === "child-of") {
      const target = window.Blocks.locate(drop.li);
      if (target) ok = window.Blocks.moveItemToChild(loc, target, scope === "item");
    } else if (drop.kind === "into-quote") {
      const target = window.Blocks.locate(drop.block);
      if (target) ok = window.Blocks.moveIntoQuote(loc, target);
    }
    if (!ok) toast("移动未完成");
    hideHandle();
  }

  // ---------------------------------------------------------------
  // 大纲拖拽：拖动默认移动整节（含正文与子节）；行上半/下半 = 移到该节前/后，
  // 行中段 = 成为该节子节（根标题 level+1，相对深度保持，越 H6 阻止）。
  // 拖拽期间经 outlineDragging() 抑制大纲重渲染（防 innerHTML 杀死拖拽中的行）。
  // ---------------------------------------------------------------

  let oDrag = null; // {item, startY, active, loc}

  function outlineLoc(item) {
    // 大纲行 → 整节区间。ir 用 data-ryuu-id 定位标题元素；sv 用 data-line 行号
    if (window.Editor.getMode() === "sv") {
      const line = parseInt(item.dataset.line || "-1", 10);
      if (line < 0) return null;
      return window.Blocks.sectionAt(line);
    }
    const id = item.dataset.id;
    const h = id && document.querySelector(`[data-ryuu-id="${id}"]`);
    if (!h) return null;
    const loc = window.Blocks.locate(h);
    return loc ? window.Blocks.sectionAt(loc.start) : null;
  }

  function onOutlineDown(e) {
    const item = e.target.closest && e.target.closest(".outline-item");
    if (!item || e.button !== 0) return;
    oDrag = { item, startX: e.clientX, startY: e.clientY, active: false, loc: null };
    document.addEventListener("mousemove", onOutlineMove, true);
    document.addEventListener("mouseup", onOutlineUp, true);
  }

  function onOutlineMove(e) {
    if (!oDrag) return;
    if (!oDrag.active) {
      if (Math.abs(e.clientX - oDrag.startX) + Math.abs(e.clientY - oDrag.startY) < 5) return;
      oDrag.loc = outlineLoc(oDrag.item);
      if (!oDrag.loc) { endOutlineDrag(); return; }
      oDrag.active = true;
      ensureOverlays();
      ghostEl = document.createElement("div");
      ghostEl.className = "block-drag-ghost";
      ghostEl.textContent = oDrag.item.textContent || "节";
      document.body.appendChild(ghostEl);
      if (window.App && window.App.registerEscape) {
        oDrag.escFn = () => { endOutlineDrag(); toast("已取消移动"); };
        window.App.registerEscape(oDrag.escFn);
      }
    }
    e.preventDefault();
    if (ghostEl) {
      ghostEl.style.left = e.clientX + 10 + "px";
      ghostEl.style.top = e.clientY + 8 + "px";
    }
    oDrag.drop = outlineDrop(e.clientX, e.clientY);
    paintOutlineDrop(oDrag.drop);
  }

  function outlineDrop(x, y) {
    const list = document.getElementById("outline-list");
    const hit = document.elementFromPoint(x, y);
    const item = hit && hit.closest ? hit.closest(".outline-item") : null;
    if (!item || !list || !list.contains(item) || item === oDrag.item) return null;
    const r = item.getBoundingClientRect();
    const rel = (y - r.top) / Math.max(r.height, 1);
    if (rel > 0.35 && rel < 0.65) return { kind: "child", item, text: "成为「" + item.textContent + "」的子节" };
    const before = rel <= 0.35;
    return { kind: before ? "before" : "after", item, before, text: "放在「" + item.textContent + "」" + (before ? "前" : "后") };
  }

  function paintOutlineDrop(drop) {
    if (!drop) {
      dropLine.classList.remove("on");
      dropBadge.classList.remove("on");
      return;
    }
    const r = drop.item.getBoundingClientRect();
    const y = drop.kind === "before" ? r.top : drop.kind === "after" ? r.bottom : r.top + r.height / 2;
    dropLine.style.top = y + "px";
    dropLine.style.left = r.left + "px";
    dropLine.style.width = r.width + "px";
    dropLine.classList.add("on");
    dropBadge.textContent = drop.text;
    dropBadge.style.top = y - 26 + "px";
    dropBadge.style.left = r.left + 8 + "px";
    dropBadge.classList.add("on");
  }

  function endOutlineDrag() {
    if (ghostEl) { ghostEl.remove(); ghostEl = null; }
    if (dropLine) dropLine.classList.remove("on");
    if (dropBadge) dropBadge.classList.remove("on");
    document.removeEventListener("mousemove", onOutlineMove, true);
    document.removeEventListener("mouseup", onOutlineUp, true);
    if (oDrag && oDrag.escFn && window.App && window.App.unregisterEscape) window.App.unregisterEscape(oDrag.escFn);
    oDrag = null;
    // 拖拽期被抑制的大纲重渲染在结束后补一次
    if (window.Editor && window.Editor.refreshOutline) window.Editor.refreshOutline();
  }

  function onOutlineUp(e) {
    if (!oDrag) return;
    const wasActive = oDrag.active;
    const drop = oDrag.drop;
    const srcLoc = oDrag.loc;
    endOutlineDrag();
    if (!wasActive || !drop) return;
    const dstLoc = outlineLoc(drop.item);
    if (!dstLoc) { toast("定位失败，移动已取消"); return; }
    if (drop.kind === "child") {
      const res = window.Blocks.moveSectionToChild(srcLoc, dstLoc);
      if (!res.ok) { toast(res.reason || "移动未完成"); return; }
    } else {
      // 前/后移动整节：区间剪切 + 目标节边界插入
      if (!window.Blocks.moveBlockTo(srcLoc, dstLoc, drop.kind === "before", false)) {
        toast("移动未完成");
        return;
      }
    }
  }

  // ---------------------------------------------------------------
  // 事件绑定（document 委托，编辑器重建免疫）
  // ---------------------------------------------------------------

  function homeOpen() {
    return !!(window.Home && window.Home.isOpen && window.Home.isOpen());
  }

  function onHover(e) {
    hoverRaf = 0; // 先解除节流锁：任何分支都不再返回前遗漏
    if (drag && drag.active) return;
    if (window.Editor.getMode() === "sv") return;
    if (homeOpen()) { hideHandle(); return; }
    const p = irPanel();
    if (!p) { hideHandle(); return; }
    if (handleEl && handleEl.contains(e.target)) return;
    if (window.SlashMenu && window.SlashMenu.isOpen && window.SlashMenu.isOpen()) return;
    const t = p.contains(e.target) ? targetForElement(e.target) : null;
    if (!t) {
      // 柄在面板 padding 留白内：鼠标移向柄（仍在面板内）时保持
      if (!p.contains(e.target)) hideHandle();
      return;
    }
    if (cur && cur.el === t.el && cur.kind === t.kind) { positionHandle(); return; }
    showHandle(t);
  }

  function onSelectionChange() {
    clearTimeout(selTimer);
    selTimer = setTimeout(() => {
      if (drag && drag.active) return;
      if (window.SlashMenu && window.SlashMenu.isOpen && window.SlashMenu.isOpen()) return;
      if (homeOpen()) { hideHandle(); return; }
      // 柄只跟随鼠标悬停（ir）与 sv 光标：打字/方向键移光标时弹出柄是视觉噪音，
      // 且每次 caret 移动都要付出 locate 对齐代价（Notion 亦仅悬停出现）。
      // sv 无悬停语义（span 流无块级元素），保留光标跟随作为唯一入口。
      if (window.Editor.getMode() !== "sv") return;
      const sel = window.getSelection();
      if (!sel || !sel.rangeCount) return;
      const p = panel();
      if (!p || !p.contains(sel.anchorNode)) return;
      // sv：柄只跟随光标所在顶层块（span 流无块结构，不做悬停解析）
      const line = caretLineSV(p, sel);
      if (line == null) return;
      const loc = window.Blocks.locateLine(line);
      if (!loc || loc.type === "html") return;
      const r = sel.getRangeAt(0).getClientRects()[0];
      ensureOverlays();
      cur = { el: null, kind: "sv", loc, probe: null, scope: "block", svRect: r };
      if (r) {
        handleEl.style.left = Math.max(p.getBoundingClientRect().left + 6, r.left - 30) + "px";
        handleEl.style.top = r.top + "px";
      }
      handleEl.classList.add("on");
    }, 120);
  }

  /* sv 光标行号：累加选区前的文本长度数 \n */
  function caretLineSV(p, sel) {
    const range = sel.getRangeAt(0);
    const pre = range.cloneRange();
    pre.selectNodeContents(p);
    pre.setEnd(range.startContainer, range.startOffset);
    const text = pre.toString();
    return (text.match(/\n/g) || []).length;
  }

  function onKeydown(e) {
    if (composing) return;
    if (!(e.ctrlKey && e.altKey) || (e.key !== "ArrowUp" && e.key !== "ArrowDown")) return;
    const sel = window.getSelection();
    if (!sel || !sel.rangeCount) return;
    const p = panel();
    if (!p || !p.contains(sel.anchorNode)) return;
    let loc = null;
    if (window.Editor.getMode() === "sv") {
      const line = caretLineSV(p, sel);
      if (line != null) loc = window.Blocks.locateLine(line);
    } else {
      const t = targetForElement(sel.anchorNode);
      if (t) {
        loc = window.Blocks.locate(t.el);
        if (t.kind === "item") {
          e.preventDefault();
          window.Blocks.moveItem(loc, e.key === "ArrowUp" ? -1 : 1);
          return;
        }
      }
    }
    if (!loc) return;
    e.preventDefault();
    window.Blocks.moveBlock(loc, e.key === "ArrowUp" ? -1 : 1);
  }

  function attach() {
    if (document.__blockUIBound) return;
    document.__blockUIBound = true;
    ensureOverlays();
    const outlineList = document.getElementById("outline-list");
    if (outlineList) outlineList.addEventListener("mousedown", onOutlineDown);
    document.addEventListener("mousemove", (e) => {
      if (hoverRaf) return;
      hoverRaf = requestAnimationFrame(() => onHover(e));
    }, { passive: true });
    document.addEventListener("selectionchange", onSelectionChange);
    document.addEventListener("keydown", onKeydown, true);
    document.addEventListener("compositionstart", () => { composing = true; });
    document.addEventListener("compositionend", () => { composing = false; });
    window.addEventListener("resize", () => { if (cur) positionHandle(); });
    document.addEventListener("scroll", (e) => {
      if (cur && e.target && e.target.contains && e.target.contains(panel())) positionHandle();
    }, true);
  }

  window.BlockUI = {
    attach,
    applyMenuAction,
    hideHandle,
    outlineDragging: () => !!(oDrag && oDrag.active),
    _debug: () => ({ cur, drag: !!drag, outline: !!oDrag }),
  };
})();
