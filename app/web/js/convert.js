/* 段落块级格式转换 + 表格/代码块/数学块上下文操作（右键菜单），对标 Typora。

   段落「转换为」：正文 / 标题1~6 / 无序列表 / 有序列表 / 待办列表 / 引用 / 代码块。
   块级操作（按光标所在块类型动态出现）：
     表格  —— 向上/下插入行、向左/右插入列、删除行/列、删除表格；
     代码块 —— 复制代码、转换为普通文本、删除代码块；
     数学块 —— 复制公式源码、转换为普通文本、删除公式块。

   IR 模式：以顶层块为粒度。IR 的标记符是 width:0 的真实文本节点，
   故 textContent 即含 ** 等行内标记的完整 md 源码，逐块提取→变换→整段替换。
   替换路径 = 选中块内容 → execCommand("delete") → insertValue：
   与用户键入/斜杠插入同一条 Vditor 同步管线（input 回调、脏标记、撤销栈都正常），
   避免只改 DOM 不改内部 model 导致被重渲染刷回（历史 checkbox 教训）。
   表格/代码/数学等容器块：Vditor 会拦截 delete/insertText 并重建块壳（实测取证），
   DOM 手术不可靠，统一走全文文本区间替换 + setValue 重载（见下文块操作区注释）。

   sv 模式：段落转换按选区整行做同一套文本变换；块操作不开放（纯文本场景意义不大）。

   粒度说明（KISS）：选区跨块时逐块独立转换；光标落在列表/引用内时转换整个
   顶层列表/引用块（Typora 只转当前项，此处刻意简化，嵌套列表只剥一层标记）。
   不支持段落转换的块（表格/代码/数学块/分割线等）有各自的上下文操作组。 */
(function () {
  /* 转换目标定义：id 即内部标识；h1~h6 动态生成 */
  const TARGETS = [
    { id: "paragraph", title: "正文", icon: "fileText" },
    { id: "h1", title: "一级标题", icon: "h1" },
    { id: "h2", title: "二级标题", icon: "h2" },
    { id: "h3", title: "三级标题", icon: "h3" },
    { id: "h4", title: "四级标题", icon: "heading" },
    { id: "h5", title: "五级标题", icon: "heading" },
    { id: "h6", title: "六级标题", icon: "heading" },
    { id: "ul", title: "无序列表", icon: "listUnordered" },
    { id: "ol", title: "有序列表", icon: "listOrdered" },
    { id: "todo", title: "待办列表", icon: "todo" },
    { id: "quote", title: "引用", icon: "quote" },
    { id: "code", title: "代码块", icon: "code" },
  ];

  // ---------------------------------------------------------------
  // 行级变换（纯函数，ir/sv 两模式共用）
  // ---------------------------------------------------------------

  /* 剥掉一行的全部前导块级标记（标题 #、引用 >、列表 -/1./- [x] 可叠加） */
  function stripLine(line) {
    let s = String(line || "");
    let prev;
    do {
      prev = s;
      s = s
        .replace(/^\s{0,3}#{1,6}\s+/, "")
        .replace(/^\s{0,3}>\s?/, "")
        .replace(/^\s{0,3}[-*+]\s+\[[ xX]\]\s+/, "")
        .replace(/^\s{0,3}(?:[-*+]|\d{1,9}[.)])\s+/, "");
    } while (s !== prev);
    return s;
  }

  /* 剥行内标记（仅转换为代码块时用：代码里的 md 标记会原样显示，先剥干净） */
  function stripInline(s) {
    return String(s || "")
      .replace(/!\[([^\]]*)\]\([^)]*\)/g, "$1")
      .replace(/\[([^\]]*)\]\([^)]*\)/g, "$1")
      .replace(/\[\[([^\]|]*)(?:\|[^\]]*)?\]\]/g, "$1")
      .replace(/(\*\*|__)(.+?)\1/g, "$2")
      .replace(/(^|[\s(])(\*|_)([^*_]+?)\2/g, "$1$3")
      .replace(/~~([^~]+?)~~/g, "$1")
      .replace(/==([^=]+?)==/g, "$1")
      .replace(/`([^`]*)`/g, "$1");
  }

  /* 把若干源行变换为目标格式的 md 文本；null = 未知目标 */
  function transform(lines, target) {
    const items = lines.map(stripLine).map((s) => s.trimEnd()).filter((s) => s !== "");
    if (!items.length) return "";
    if (target === "code") {
      return "```\n" + items.map(stripInline).join("\n") + "\n```\n";
    }
    if (target === "paragraph") return items.join("\n\n") + "\n";
    if (/^h[1-6]$/.test(target)) {
      const marks = "#".repeat(parseInt(target[1], 10));
      return items.map((s) => marks + " " + s).join("\n\n") + "\n";
    }
    if (target === "ul") return items.map((s) => "- " + s).join("\n") + "\n";
    if (target === "ol") return items.map((s, i) => (i + 1) + ". " + s).join("\n") + "\n";
    if (target === "todo") return items.map((s) => "- [ ] " + s).join("\n") + "\n";
    if (target === "quote") return items.map((s) => "> " + s).join("\n") + "\n";
    return null;
  }

  // ---------------------------------------------------------------
  // IR 模式：DOM 块提取
  // ---------------------------------------------------------------

  function irPanel() {
    return document.querySelector("#editor .vditor-ir .vditor-reset");
  }

  /* 顶层块判定：Lute IR 输出的顶层块带 data-block="0"（p、标题、blockquote、
     ul/ol、table、div[data-type] 等）。保险起见再按标签白名单过滤一次。 */
  function isTopBlock(el) {
    if (!el || el.nodeType !== Node.ELEMENT_NODE) return false;
    if (el.getAttribute("data-block") === "0") return true;
    return /^(P|H[1-6]|BLOCKQUOTE|UL|OL|PRE|TABLE|HR)$/.test(el.tagName);
  }

  function closestBlock(panel, node) {
    let el = node && (node.nodeType === Node.ELEMENT_NODE ? node : node.parentElement);
    while (el && el !== panel) {
      if (el.parentElement === panel && isTopBlock(el)) return el;
      el = el.parentElement;
    }
    return null;
  }

  /* 选区覆盖的顶层块（文档序）；光标无选区时返回所在块 */
  function collectBlocks(panel, sel) {
    if (!panel || !sel || !sel.rangeCount) return [];
    if (sel.isCollapsed) {
      const b = closestBlock(panel, sel.anchorNode);
      return b ? [b] : [];
    }
    const range = sel.getRangeAt(0);
    const out = [];
    panel.childNodes.forEach((el) => {
      if (!isTopBlock(el)) return;
      try { if (range.intersectsNode(el)) out.push(el); } catch (e) { /* ignore */ }
    });
    return out;
  }

  /* 元素文本还原为 md 行：<br> 还原换行，剔除零宽字符 */
  function textWithBreaks(el) {
    let out = "";
    const walker = document.createTreeWalker(el, NodeFilter.SHOW_TEXT | NodeFilter.SHOW_ELEMENT, {
      acceptNode(n) {
        if (n.nodeType === Node.TEXT_NODE) return NodeFilter.FILTER_ACCEPT;
        if (n.tagName === "BR") return NodeFilter.FILTER_ACCEPT;
        return NodeFilter.FILTER_SKIP;
      },
    });
    while (walker.nextNode()) {
      const n = walker.currentNode;
      out += n.nodeType === Node.TEXT_NODE ? n.nodeValue : "\n";
    }
    return out.replace(/\u200B/g, "");
  }

  /* 块 → md 行数组。容器块（引用/列表）按直接子块逐行；其余按 <br> 拆行 */
  function blockLines(el) {
    const tag = el.tagName;
    if (tag === "BLOCKQUOTE" || tag === "UL" || tag === "OL") {
      const kids = [...el.children].filter((c) => /^(P|LI|H[1-6])$/.test(c.tagName));
      if (kids.length) return kids.map((k) => textWithBreaks(k));
    }
    return textWithBreaks(el).split("\n");
  }

  /* 块 → 目标 id（用于禁用「已是该格式」项）；null = 不支持转换的块 */
  function kindOf(el) {
    const t = el.tagName;
    if (/^H[1-6]$/.test(t)) return t.toLowerCase();
    if (t === "P") return "paragraph";
    if (t === "BLOCKQUOTE") return "quote";
    if (t === "UL") return /^\s*[-*+]\s+\[[ xX]\]/.test(el.textContent || "") ? "todo" : "ul";
    if (t === "OL") return "ol";
    return null; // PRE/TABLE/div[data-type]（代码/数学/html 块）等不支持
  }

  // ---------------------------------------------------------------
  // 应用转换
  // ---------------------------------------------------------------

  function applyIR(target, editor) {
    const panel = irPanel();
    const sel = window.getSelection();
    const blocks = collectBlocks(panel, sel);
    let done = 0;
    // 倒序处理：每次替换只重渲染当前块，倒序保证其余块的元素引用不被波及
    for (let i = blocks.length - 1; i >= 0; i--) {
      const b = blocks[i];
      if (!b.isConnected) continue; // 前一次重渲染波及则跳过
      if (!kindOf(b)) continue;
      const text = transform(blockLines(b), target);
      if (!text) continue;
      editor.focus();
      const range = document.createRange();
      range.selectNodeContents(b);
      sel.removeAllRanges();
      sel.addRange(range);
      document.execCommand("delete");
      editor.insertValue(text, true);
      done++;
    }
    return done;
  }

  function applySV(target, editor) {
    const sel = window.getSelection();
    if (!sel || !sel.rangeCount) return 0;
    if (sel.isCollapsed) {
      // 无选区：选中光标所在行的全部内容。sv 面板结构 = PRE > 行 DIV（实测取证）；
      // 不用 sel.modify(lineboundary)：WebView2 contenteditable 上它不扩展选区。
      const svEl = document.querySelector("#editor .vditor-sv");
      let el = sel.anchorNode;
      if (!svEl || !el || !svEl.contains(el)) return 0;
      if (el.nodeType !== Node.ELEMENT_NODE) el = el.parentElement;
      while (el && el.parentElement !== svEl) el = el.parentElement;
      if (!el) return 0;
      const r = document.createRange();
      r.selectNodeContents(el);
      sel.removeAllRanges();
      sel.addRange(r);
    }
    const text = sel.toString();
    if (!text) return 0;
    const out = transform(text.split("\n"), target);
    if (!out) return 0;
    editor.focus();
    document.execCommand("delete");
    editor.insertValue(out, true);
    return 1;
  }

  /* 入口：target 为 TARGETS 的 id；返回转换成功的块数 */
  function apply(target, editor) {
    if (!editor || !window.Editor) return 0;
    try {
      return window.Editor.getMode() === "sv" ? applySV(target, editor) : applyIR(target, editor);
    } catch (e) {
      return 0;
    }
  }

  // ---------------------------------------------------------------
  // 表格 / 代码块 / 数学块：上下文块操作
  //
  // 容器块不走 DOM 手术：实测 Vditor 会拦截/重建——execCommand delete 对
  // table/math-block 只删内容、块壳残留（并复活为空块），insertText 被
  // beforeinput 吞掉。因此容器块统一走「全文文本替换」：
  //   定位该块在 md 全文中的行区间（同类块第 N 个，DOM 序 ↔ 全文序对齐）
  //   → 替换/删除区间 → Editor.setValue 重载（保滚动）→ notifyChange 补脏标记。
  // 代价：Ctrl+Z 撤销栈被 setValue 重置、光标回滚到滚动比例位置——容器块操作
  // 低频，用撤销能力换可靠性（段落转换仍走 DOM 路径，不受影响）。
  // ---------------------------------------------------------------

  /* 光标处的特殊块：table 元素 或 div[data-type=code-block|math-block]；否则 null */
  function caretSpecialBlock() {
    const sel = window.getSelection();
    if (!sel || !sel.rangeCount) return null;
    const panel = irPanel();
    const b = panel && closestBlock(panel, sel.anchorNode);
    if (!b) return null;
    if (b.tagName === "TABLE") return { kind: "table", el: b };
    const dtype = b.getAttribute("data-type");
    if (dtype === "code-block") return { kind: "code", el: b };
    if (dtype === "math-block") return { kind: "math", el: b };
    return null;
  }

  /* 复制文本到剪贴板：textarea + execCommand（file:// 下 navigator.clipboard 可能无权）。
     会临时劫持页面选区，完成后恢复原选区。 */
  function copyText(text) {
    const sel = window.getSelection();
    const saved = [];
    if (sel) for (let i = 0; i < sel.rangeCount; i++) saved.push(sel.getRangeAt(i).cloneRange());
    const ta = document.createElement("textarea");
    ta.value = text;
    ta.style.cssText = "position:fixed;top:0;left:0;opacity:0";
    document.body.appendChild(ta);
    ta.select();
    let ok = false;
    try { ok = document.execCommand("copy"); } catch (e) { /* ignore */ }
    ta.remove();
    if (sel && saved.length) {
      sel.removeAllRanges();
      saved.forEach((r) => sel.addRange(r));
    }
    return ok;
  }

  // —— md 全文区间定位（同类块第 N 个，与 DOM 顶层块序对齐）——

  /* 全文表格区间：[start, end) 行号。判定比「连续 | 行」更严：首行是 | 行、
     次行必须是对齐行（|:---|---:| 形态），与 md 表格语法（表头+分隔行必需）一致，
     防止正文中形如 | 的伪表格行造成 DOM 序 ↔ 全文序错位（错位会误改其他内容）。 */
  function mdTableRanges(lines) {
    const ROW = /^\s*\|.*\|\s*$/;
    const ALIGN = /^\s*\|[\s:|-]*-[\s:|-]*\|\s*$/; // 只含 | : - 空格且至少一个 -
    const ranges = [];
    let inFence = false;
    let i = 0;
    while (i < lines.length) {
      if (/^\s*(```|~~~)/.test(lines[i])) { inFence = !inFence; i++; continue; }
      if (!inFence && ROW.test(lines[i]) && i + 1 < lines.length && ALIGN.test(lines[i + 1])) {
        let j = i + 2;
        while (j < lines.length && ROW.test(lines[j])) j++;
        ranges.push([i, j]);
        i = j;
        continue;
      }
      i++;
    }
    return ranges;
  }

  /* 全文 fence 块区间：``` 配对（代码块）或 $$ 独占行配对（数学块）。
     数学块扫描跳过 ``` 围栏内部（代码里的 $$ 行不算）。 */
  function mdFenceRanges(lines, kind) {
    const ranges = [];
    let open = -1;
    let inCode = false;
    lines.forEach((l, i) => {
      if (/^\s*(```|~~~)/.test(l)) {
        if (kind === "code") {
          if (open < 0) open = i;
          else { ranges.push([open, i + 1]); open = -1; }
        } else {
          inCode = !inCode; // 数学扫描只维护代码围栏状态
        }
        return;
      }
      if (kind === "math" && !inCode && /^\s*\$\$\s*$/.test(l)) {
        if (open < 0) open = i;
        else { ranges.push([open, i + 1]); open = -1; }
      }
    });
    return ranges;
  }

  /* 目标块在同类块中的序号（DOM 顶层块序） */
  function siblingIndex(kind, el) {
    const panel = irPanel();
    if (!panel) return -1;
    const same = [...panel.children].filter((c) => {
      if (kind === "table") return c.tagName === "TABLE";
      return c.getAttribute && c.getAttribute("data-type") === (kind === "code" ? "code-block" : "math-block");
    });
    return same.indexOf(el);
  }

  /* 全文替换目标块区间：newText=null 表示删除整块。保滚动比例、补脏标记。
     probe(lines 区间) 可选：替换前校验 md 区间与 DOM 块确实是同一块
     （DOM 序 ↔ 全文序错位时宁可放弃，也不能误删其他内容）。 */
  function replaceBlockMd(kind, el, newText, probe) {
    const k = siblingIndex(kind, el);
    if (k < 0) return false;
    const lines = window.Editor.getValue().split("\n");
    const ranges = kind === "table" ? mdTableRanges(lines) : mdFenceRanges(lines, kind);
    if (k >= ranges.length) return false;
    const [s, e] = ranges[k];
    if (probe && !probe(lines.slice(s, e))) return false;
    const repl = newText == null ? [] : newText.replace(/\n+$/, "").split("\n");
    const ratio = window.Editor.getScrollRatio();
    lines.splice(s, e - s, ...repl);
    window.Editor.setValue(lines.join("\n"));
    window.Editor.notifyChange();
    // 渲染异步撑高内容，滚动比例延迟恢复
    setTimeout(() => window.Editor.setScrollRatio(ratio), 120);
    return true;
  }

  // —— 表格 ——

  /* 光标所在单元格上下文：行号（表头为 -1）/列号/是否表头 */
  function caretCell(table) {
    const sel = window.getSelection();
    if (!sel || !sel.rangeCount) return null;
    let node = sel.anchorNode;
    if (!node) return null;
    if (node.nodeType !== Node.ELEMENT_NODE) node = node.parentElement;
    const cell = node && node.closest ? node.closest("td,th") : null;
    if (!cell || !table.contains(cell)) return null;
    const tr = cell.parentElement;
    const inHeader = cell.tagName === "TH" || !!cell.closest("thead");
    return {
      inHeader,
      rowIndex: inHeader ? -1 : [...table.tBodies[0].rows].indexOf(tr),
      colIndex: [...tr.cells].indexOf(cell),
    };
  }

  /* 表格 → 结构化数据。单元格 textContent 保留行内标记（** 等），| 转义、换行转 <br>；
     对齐从 th 的 align 属性取（Lute IR 实测：align=left/center/right）。 */
  function cellText(c) {
    return (c.textContent || "")
      .replace(/\u200B/g, "")
      .replace(/\|/g, "\\|")
      .replace(/\n+/g, "<br>")
      .trim();
  }

  function readTable(table) {
    const headCells = table.tHead && table.tHead.rows.length ? [...table.tHead.rows[0].cells] : [];
    return {
      header: headCells.map(cellText),
      aligns: headCells.map((c) => c.getAttribute("align") || ""),
      rows: table.tBodies[0] ? [...table.tBodies[0].rows].map((r) => [...r.cells].map(cellText)) : [],
    };
  }

  function alignMd(a) {
    return a === "center" ? ":---:" : a === "right" ? "---:" : a === "left" ? ":---" : "---";
  }

  function mdTable(d) {
    const line = (cells) => "| " + cells.join(" | ") + " |";
    const out = [line(d.header), line(d.aligns.map(alignMd))];
    d.rows.forEach((r) => out.push(line(r)));
    return out.join("\n") + "\n";
  }

  function applyTableOp(op, editor, table) {
    const ctx = caretCell(table);
    if (!ctx) return 0;
    const d = readTable(table);
    const cols = d.header.length;
    // probe 校验必须用操作前的表头快照：col-del 等分支会就地 splice d.header，
    // 闭包里再读 d.header 已是新值，与旧 md 区间比对必然错位（实测踩坑）
    const origHeader = d.header.slice();
    const blankRow = () => Array(cols).fill("");
    const insertCol = (i) => {
      d.header.splice(i, 0, "");
      d.aligns.splice(i, 0, "");
      d.rows.forEach((r) => r.splice(i, 0, ""));
    };
    let newText;
    switch (op) {
      case "row-above":
        d.rows.splice(ctx.inHeader ? 0 : ctx.rowIndex, 0, blankRow());
        newText = mdTable(d);
        break;
      case "row-below":
        d.rows.splice(ctx.inHeader ? 0 : ctx.rowIndex + 1, 0, blankRow());
        newText = mdTable(d);
        break;
      case "col-left":
        insertCol(ctx.colIndex);
        newText = mdTable(d);
        break;
      case "col-right":
        insertCol(ctx.colIndex + 1);
        newText = mdTable(d);
        break;
      case "row-del":
        // 表头行不可单独删除（md 表格必须有表头）= 删除整个表格；删到无数据行同理
        if (ctx.inHeader) newText = null;
        else {
          d.rows.splice(ctx.rowIndex, 1);
          newText = d.rows.length ? mdTable(d) : null;
        }
        break;
      case "col-del":
        if (cols <= 1) newText = null;
        else {
          d.header.splice(ctx.colIndex, 1);
          d.aligns.splice(ctx.colIndex, 1);
          d.rows.forEach((r) => r.splice(ctx.colIndex, 1));
          newText = mdTable(d);
        }
        break;
      case "table-del":
        newText = null;
        break;
      default:
        return 0;
    }
    // 对齐校验：md 区间首行的单元格数/首格文本须与 DOM 表头一致，否则放弃操作
    const probe = (seg) => {
      const cells = (seg[0] || "").split("|").map((c) => c.trim()).filter(Boolean);
      if (cells.length !== origHeader.length) return false;
      return !origHeader[0] || seg[0].indexOf(origHeader[0]) >= 0;
    };
    return replaceBlockMd("table", table, newText, probe) ? 1 : 0;
  }

  // —— 代码块 / 数学块 ——

  /* 块源码提取（复制用）：代码块 pre.textContent 为纯源码；数学块源码在
     pre > code.language-math（围栏行 ```/$$ 都在 pre 之外的 marker span，实测取证） */
  function blockSource(kind, el) {
    const node = kind === "math"
      ? el.querySelector("pre code.language-math") || el.querySelector("pre")
      : el.querySelector("pre");
    let src = node ? (node.textContent || "") : "";
    src = src.replace(/\u200B/g, "");
    const fenced = src.match(/^\s*(?:```[^\n]*|\$\$)\n([\s\S]*?)\n?(?:```|\$\$)\s*$/);
    if (fenced) src = fenced[1];
    return src.replace(/\n+$/, "");
  }

  /* 源码 → 普通文本段落（每行一个段落；空行跳过） */
  function sourceToParagraphs(src) {
    const lines = src.split("\n").map((s) => s.trimEnd()).filter((s) => s !== "");
    return lines.join("\n\n") + "\n";
  }

  function applyBlockOp(op, editor) {
    if (!editor || !window.Editor || window.Editor.getMode() === "sv") return 0;
    const blk = caretSpecialBlock();
    if (!blk) return 0;
    const { kind, el } = blk;
    try {
      if (kind === "table") return applyTableOp(op, editor, el);
      const src = blockSource(kind, el);
      // 对齐校验：md 区间须包含源码首行，防止序号错位误改其他块
      const firstLine = src.split("\n").map((s) => s.trim()).find(Boolean) || "";
      const probe = firstLine ? (seg) => seg.join("\n").indexOf(firstLine) >= 0 : null;
      switch (op) {
        case "copy":
          return copyText(src) ? 1 : 0;
        case "to-text":
          if (!src.trim()) return 0;
          return replaceBlockMd(kind, el, sourceToParagraphs(src), probe) ? 1 : 0;
        case "del":
          return replaceBlockMd(kind, el, null, probe) ? 1 : 0;
        default:
          return 0;
      }
    } catch (e) {
      return 0;
    }
  }

  /* 上下文块操作菜单项（仅 IR 模式且光标在表格/代码块/数学块内时非空） */
  const BLOCK_OPS = {
    table: [
      { op: "row-above", title: "向上插入行", icon: "plus" },
      { op: "row-below", title: "向下插入行", icon: "plus" },
      { op: "col-left", title: "左侧插入列", icon: "plus" },
      { op: "col-right", title: "右侧插入列", icon: "plus" },
      { op: "row-del", title: "删除行", icon: "trash" },
      { op: "col-del", title: "删除列", icon: "trash" },
      { op: "table-del", title: "删除表格", icon: "trash" },
    ],
    code: [
      { op: "copy", title: "复制代码", icon: "copy" },
      { op: "to-text", title: "转换为普通文本", icon: "fileText" },
      { op: "del", title: "删除代码块", icon: "trash" },
    ],
    math: [
      { op: "copy", title: "复制公式源码", icon: "copy" },
      { op: "to-text", title: "转换为普通文本", icon: "fileText" },
      { op: "del", title: "删除公式块", icon: "trash" },
    ],
  };
  const BLOCK_GROUP = { table: "表格", code: "代码块", math: "公式块" };

  function blockItemsForContext() {
    if (!window.Editor || window.Editor.getMode() === "sv") return [];
    const blk = caretSpecialBlock();
    if (!blk) return [];
    return BLOCK_OPS[blk.kind].map((x) => ({
      id: "blk-" + blk.kind + "-" + x.op,
      blockop: x.op,
      icon: x.icon,
      title: x.title,
      desc: "",
      group: BLOCK_GROUP[blk.kind],
      keys: "",
    }));
  }

  /* 右键菜单项（仅 context 模式）。disabled 规则：
     - 无任何目标块（光标不在编辑器）→ 不显示转换组
     - 单块且已是该格式 → 禁用对应项
     - 单块为表格/代码/数学等不支持段落转换的类型 → 不显示转换组
       （这些块有各自的上下文操作组，由 blockItemsForContext 提供）
     sv 模式不做预判（纯文本行总是可转），保持可用。 */
  function itemsForContext() {
    let hide = false;
    let current = null;
    if (window.Editor && window.Editor.getMode() !== "sv") {
      const panel = irPanel();
      const blocks = collectBlocks(panel, window.getSelection());
      if (!blocks.length) hide = true;
      else if (blocks.length === 1) {
        const k = kindOf(blocks[0]);
        if (!k) hide = true;
        else current = k;
      }
    }
    if (hide) return [];
    return TARGETS.map((t) => ({
      id: "conv-" + t.id,
      convert: t.id,
      icon: t.icon,
      title: t.title,
      desc: "",
      group: "转换为",
      keys: "",
      disabled: current === t.id,
    }));
  }

  window.Convert = { apply, itemsForContext, blockItemsForContext, applyBlockOp };
})();
