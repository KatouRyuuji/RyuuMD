/* 段落块级格式转换 + 表格/代码块/数学块上下文操作（右键菜单），对标 Typora。

   段落「转换为」：正文 / 标题1~6 / 无序列表 / 有序列表 / 待办列表 / 引用 / 代码块。
   块级操作（按光标所在块类型动态出现）：
     表格  —— 向上/下插入行、向左/右插入列、删除行/列、删除表格；
     代码块 —— 复制代码、转换为普通文本、删除代码块；
     数学块 —— 复制公式源码、转换为普通文本、删除公式块。

   IR 模式：操作范围按 Markdown 语义结构确定（Blocks 结构通道）——
     光标在列表项内 → 当前列表项（含续段与子列表）；选区跨块 → 逐块。
     每个目标块 locate 出源码行区间 → 行级变换 → commitRanges 一次 setValue
     提交（独立撤销边界；首行探针不符整体放弃）。
   列表↔列表/引用互转按行重写标记、保留全部缩进（嵌套不丢）；
   整列表→标题/正文/代码按深度优先文档序逐块输出；
   列表项→非列表目标：项自身段落转换，子列表整体上提一级填位。
   空块→正文/代码：替换为空段落行/空围栏壳，不再是失败。

   sv 模式：段落转换按选区整行做同一套文本变换；块操作不开放（纯文本场景意义不大）。
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

  /* 剥掉一行的全部前导块级标记（标题 #、引用 >、列表 -/1./- [x] 可叠加；
     空格式行（"##"、"-" 等无尾空格形态）同样剥净） */
  function stripLine(line) {
    let s = String(line || "");
    let prev;
    do {
      prev = s;
      s = s
        .replace(/^\s{0,3}#{1,6}(?:[ \t]+|$)/, "")
        .replace(/^\s{0,3}>\s?/, "")
        .replace(/^\s{0,3}[-*+][ \t]+\[[ xX]\](?:[ \t]+|$)/, "")
        .replace(/^\s{0,3}(?:[-*+]|\d{1,9}[.)])(?:[ \t]+|$)/, "");
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

  /* 把若干源行变换为目标格式的 md 文本；null = 未知目标。
     空块只给行级前缀（标题/列表/引用）产出可写壳；空块 → 正文/代码返回 ""，
     是有效结果而非失败——调用方按「替换为空段落行 / 空围栏壳」处理。 */
  function transform(lines, target) {
    const items = lines.map(stripLine).map((s) => s.trimEnd()).filter((s) => s !== "");
    if (!items.length) {
      if (/^h[1-6]$/.test(target)) return "#".repeat(parseInt(target[1], 10)) + " \n";
      if (target === "ul") return "- \n";
      if (target === "ol") return "1. \n";
      if (target === "todo") return "- [ ] \n";
      if (target === "quote") return "> \n";
      return "";
    }
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

  /* 元素文本还原为 md 行：<br> 还原换行，剔除零宽字符；
     嵌套容器（内层 UL/OL/BLOCKQUOTE）整棵子树跳过——其文本属于嵌套行，
     各嵌套行单独成行。 */
  function textWithBreaks(el) {
    let out = "";
    const walker = document.createTreeWalker(el, NodeFilter.SHOW_TEXT | NodeFilter.SHOW_ELEMENT, {
      acceptNode(n) {
        if (n.nodeType === Node.TEXT_NODE) return NodeFilter.FILTER_ACCEPT;
        if (n.tagName === "BR") return NodeFilter.FILTER_ACCEPT;
        if (n !== el && /^(UL|OL|BLOCKQUOTE)$/.test(n.tagName)) return NodeFilter.FILTER_REJECT;
        return NodeFilter.FILTER_SKIP;
      },
    });
    while (walker.nextNode()) {
      const n = walker.currentNode;
      out += n.nodeType === Node.TEXT_NODE ? n.nodeValue : "\n";
    }
    return out.replace(/\u200B/g, "");
  }

  /* 块 → md 行数组。列表：任意深度的每个 LI 贡献一行（直接文本；loose 项取首段 p），
     嵌套结构平铺为独立行——粒度说明的「只剥一层」指不保留缩进，但文本不丢不并。
     引用：直接子块逐行；其余块按 <br> 拆行 */
  function blockLines(el) {
    const tag = el.tagName;
    if (tag === "UL" || tag === "OL") {
      return [...el.querySelectorAll("li")].map((li) => {
        const own = textWithBreaks(li).trim();
        if (own) return own;
        const p = li.querySelector(":scope > p");
        return p ? textWithBreaks(p) : "";
      }).filter((s) => s !== "");
    }
    if (tag === "BLOCKQUOTE") {
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
  // 应用转换（统一走 Blocks 结构通道：locate → 行级变换 → commitRanges
  // 一次 setValue 提交，独立撤销边界；首行探针不符整体放弃）
  // ---------------------------------------------------------------

  function closestLi(node) {
    let el = node && (node.nodeType === Node.ELEMENT_NODE ? node : node.parentElement);
    while (el && el.tagName !== "LI") el = el.parentElement;
    return el || null;
  }

  const LIST_LINE = /^(\s*)([-*+]|\d{1,9}[.)])([ \t]+|$)(\[[ xX]\][ \t]+)?/;

  /* 列表↔列表/引用：按行重写标记、保留全部缩进（嵌套树不丢）。
     ol 按缩进层级的兄弟序重排编号；todo 统一 "- [ ] "；quote 逐行加 "> "。 */
  function transformListLines(lines, target) {
    const counts = {};
    const indents = [];
    return lines.map((l) => {
      if (target === "quote") return l.trim() === "" ? ">" : "> " + l;
      const m = l.match(LIST_LINE);
      if (!m) return l;
      const rest = l.slice(m[0].length);
      let marker;
      if (target === "ul") marker = "- ";
      else if (target === "todo") marker = "- [ ] ";
      else {
        const key = m[1].length;
        while (indents.length && indents[indents.length - 1] > key) delete counts[indents.pop()];
        if (indents[indents.length - 1] !== key) { indents.push(key); counts[key] = 0; }
        counts[key]++;
        marker = counts[key] + ". ";
      }
      return m[1] + marker + rest;
    });
  }

  /* 列表项级转换（光标在列表项内、无选区）：
     - 目标仍是列表（ul/ol/todo）：只重写该项自身的标记行，嵌套原样；
     - 目标标题/正文/引用/代码：项自身段落逐个转换，子列表整体上提一级填位
       （嵌套关系保留，不展平）。 */
  function convertListItem(li, target) {
    const loc = window.Blocks.locate(li);
    if (!loc || loc.type !== "item") return false;
    const seg = window.Editor.getValue().split("\n").slice(loc.start, loc.end);
    if (!seg.length) return false;
    let newLines;
    if (target === "ul" || target === "ol" || target === "todo") {
      const m = seg[0].match(LIST_LINE);
      if (!m) return false;
      const marker = target === "ul" ? "- " : target === "todo" ? "- [ ] " : "1. ";
      newLines = seg.slice();
      newLines[0] = m[1] + marker + seg[0].slice(m[0].length);
    } else {
      // 项自身段落 = 首个嵌套标记行之前的行；其余为子列表
      let ownEnd = seg.length;
      for (let k = 1; k < seg.length; k++) {
        if (LIST_LINE.test(seg[k])) { ownEnd = k; break; }
      }
      const own = seg.slice(0, ownEnd).map((l, i) => (i === 0 ? l.replace(LIST_LINE, "") : l.replace(/^\s+/, "")));
      const text = transform(own, target);
      let ownOut;
      if (!text) {
        if (target !== "paragraph" && target !== "code") return false;
        ownOut = target === "code" ? ["```", "```"] : [""];
      } else {
        ownOut = text.replace(/\n+$/, "").split("\n");
      }
      // 子列表整体上提一级填位：统一左移（首个嵌套标记的缩进 − 项缩进）
      const nested = seg.slice(ownEnd);
      let out = nested;
      const nm = nested.find((l) => LIST_LINE.test(l));
      if (nm) {
        const delta = nm.match(LIST_LINE)[1].length - (loc.node.md ? loc.node.md.indent : 0);
        if (delta > 0) out = nested.map((l) => l.replace(new RegExp("^ {0," + delta + "}"), ""));
      }
      newLines = ownOut.concat(out);
    }
    return window.Blocks.commit(loc.start, loc.end, newLines, { probe: window.Blocks.probeFirstLine(li) });
  }

  function applyIR(target, editor) {
    const panel = irPanel();
    const sel = window.getSelection();
    const blocks = collectBlocks(panel, sel);
    if (!blocks.length || !window.Blocks) return 0;
    // 无选区且光标在列表项内：粒度 = 当前列表项（含其续段与子列表）
    if (sel.isCollapsed && blocks.length === 1 && /^(UL|OL)$/.test(blocks[0].tagName)) {
      const li = closestLi(sel.anchorNode);
      if (li) return convertListItem(li, target) ? 1 : 0;
    }
    const lines = window.Editor.getValue().split("\n");
    const jobs = [];
    for (const b of blocks) {
      if (!kindOf(b)) return 0;
      const loc = window.Blocks.locate(b);
      if (!loc) return 0;
      let newLines;
      if (/^(UL|OL)$/.test(b.tagName) && (target === "ul" || target === "ol" || target === "todo" || target === "quote")) {
        newLines = transformListLines(lines.slice(loc.start, loc.end), target);
      } else {
        const text = transform(blockLines(b), target);
        if (!text) {
          // 空块 → 正文/代码：正文替换为空段落行，代码替换为空围栏壳
          if (target !== "paragraph" && target !== "code") return 0;
          newLines = target === "code" ? ["```", "```"] : [""];
        } else {
          newLines = text.replace(/\n+$/, "").split("\n");
        }
      }
      jobs.push({ start: loc.start, end: loc.end, newLines, probe: window.Blocks.probeFirstLine(b) });
    }
    return window.Blocks.commitRanges(jobs) ? jobs.length : 0;
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
      const n = window.Editor.getMode() === "sv" ? applySV(target, editor) : applyIR(target, editor);
      if (n && target !== "paragraph" && target !== "code") settleEmptyFormat();
      return n;
    } catch (e) {
      return 0;
    }
  }

  // ---------------------------------------------------------------
  // 空格式壳：光标落到标记之后，避免再输入掉回正文。
  // 空标题按 Enter/Backspace 一次退出标题格式（Blocks 事务替换为空段落，
  // 光标留在原行）；空列表项/空引用的 Enter 交还 Vditor 原生。
  // ---------------------------------------------------------------

  function prefixKind(k) {
    return !!(k && k !== "paragraph" && k !== "code");
  }

  function caretPrefixBlock() {
    const panel = irPanel();
    const sel = window.getSelection();
    if (!panel || !sel || !sel.rangeCount) return null;
    const block = closestBlock(panel, sel.anchorNode);
    const k = block && kindOf(block);
    if (!block || !prefixKind(k)) return null;
    return block;
  }

  /* \u9879\u7ea7\u5224\u7a7a\uff1a\u81ea\u8eab\u6587\u672c\u4e3a\u7a7a\uff0c\u4e14\u5d4c\u5957\u5b50\u5bb9\u5668\u4e5f\u5168\u90e8\u4e3a\u7a7a\u2014\u2014\u5b58\u5728\u5b50\u5185\u5bb9\u7684\u9879
     \u4e0d\u80fd\u88ab\u8bef\u5224\u4e3a\u7a7a\uff08\u7a7a\u5217\u8868\u9879 Enter \u9000\u51fa\u5217\u8868\u5c42\u7ea7\u7684\u524d\u63d0\uff09\u3002 */
  function liIsEmpty(li) {
    if (textWithBreaks(li).replace(/\u200b/g, "").trim() !== "") return false;
    const nested = li.querySelectorAll(":scope > ul, :scope > ol, :scope > blockquote");
    return [...nested].every((n) => isVisuallyEmpty(n));
  }

  /* \u8bed\u4e49\u5224\u7a7a\uff1a\u6392\u9664\u7f16\u8f91\u5668\u5360\u4f4d\u5b57\u7b26\uff08\u96f6\u5bbd\u7a7a\u683c\uff09\uff0c\u8bc6\u522b\u771f\u5b9e\u5185\u5bb9\u2014\u2014
     \u542b\u56fe\u7247\u7684\u6bb5\u843d\u5373\u4f7f textContent \u4e3a\u7a7a\u4e5f\u4e0d\u662f\u7a7a\u6bb5\u843d\uff1b\u4ee3\u7801/\u6570\u5b66\u5757\u7684\u7a7a\u767d\u662f\u6709\u6548\u5185\u5bb9\uff1b
     \u5217\u8868\u9010\u9879\u5224\u300c\u9879\u81ea\u8eab\u6587\u672c\u300d\uff08\u5d4c\u5957\u5b50\u5217\u8868\u5c5e\u4e8e\u5b50\u9879\u5185\u5bb9\uff0c\u6709\u5b50\u5185\u5bb9\u7684\u9879\u4e0d\u662f\u7a7a\u9879\uff09\u3002 */
  function isVisuallyEmpty(el) {
    if (el.querySelector && el.querySelector("img")) return false;
    const t = el.tagName;
    const dt = el.getAttribute && el.getAttribute("data-type");
    if (t === "PRE" || dt === "code-block" || dt === "math-block") return false;
    if (t === "UL" || t === "OL") {
      const lis = [...el.querySelectorAll(":scope > li")];
      if (!lis.length) return (el.textContent || "").replace(/\u200b/g, "").trim() === "";
      return lis.every((li) => liIsEmpty(li));
    }
    const raw = (el.textContent || "").replace(/\u200b/g, "");
    if (/^H[1-6]$/.test(t)) return raw.replace(/^#{1,6}\s*/, "").trim() === "";
    if (t === "BLOCKQUOTE") return raw.replace(/^>\s*/gm, "").trim() === "";
    return raw.trim() === "";
  }

  function placeCaretAfterMarker(block) {
    if (!block) return false;
    const sel = window.getSelection();
    if (!sel) return false;
    const headingMarker = /^H[1-6]$/.test(block.tagName)
      ? block.querySelector(":scope > .vditor-ir__marker--heading")
      : null;
    if (headingMarker) {
      let node = headingMarker.nextSibling;
      while (node && node.nodeType === Node.ELEMENT_NODE && node.tagName === "WBR") {
        node = node.nextSibling;
      }
      if (!node || node.nodeType !== Node.TEXT_NODE) {
        node = document.createTextNode("\u200b");
        headingMarker.after(node);
      } else if (node.textContent === "") {
        node.textContent = "\u200b";
      }
      const r = document.createRange();
      r.setStart(node, node.textContent.length);
      r.collapse(true);
      sel.removeAllRanges();
      sel.addRange(r);
      return true;
    }
    const inner = block.tagName === "UL" || block.tagName === "OL"
      ? (block.querySelector("li") || block)
      : (block.tagName === "BLOCKQUOTE" ? (block.querySelector("p") || block) : block);
    const r = document.createRange();
    r.selectNodeContents(inner);
    r.collapse(false);
    sel.removeAllRanges();
    sel.addRange(r);
    return true;
  }

  /* 斜杠/转换刚造出空标题、列表、引用后调用：光标放到标记后。
     用「当前选区所在空前缀块」判断，不捏 DOM 节点——IR spin 会换掉元素。 */
  function settleEmptyFormat() {
    const go = () => {
      const block = caretPrefixBlock();
      if (!block || !isVisuallyEmpty(block)) return;
      placeCaretAfterMarker(block);
    };
    requestAnimationFrame(() => setTimeout(go, 0));
  }

  /* 光标所在的语义为空前缀块（仅 IR 模式）；否则 null */
  function emptyPrefixBlock() {
    if (!window.Editor || window.Editor.getMode() === "sv") return null;
    const block = caretPrefixBlock();
    if (!block || !isVisuallyEmpty(block)) return null;
    return block;
  }

  /* 空标题 → 空段落：Blocks 区间替换（一次事务、独立撤销边界），光标留在原行 */
  function exitEmptyHeading(block) {
    if (!window.Blocks) return false;
    const loc = window.Blocks.locate(block);
    if (!loc) return false;
    return window.Blocks.replaceEl(block, "", null, { caretLine: loc.start });
  }

  /* 空标题按 Enter：退出标题格式为普通段落。
     斜杠菜单打开时的 Enter 由菜单消费，不会到达这里；组词期（isComposing）放行。
     空列表项/空引用返回 false，交还 Vditor 原生（退出列表层级/引用）。 */
  function handleEmptyEnter(e) {
    if (!e || e.key !== "Enter" || e.isComposing) return false;
    if (e.shiftKey || e.altKey || e.ctrlKey || e.metaKey) return false;
    const block = emptyPrefixBlock();
    if (!block || !/^H[1-6]$/.test(block.tagName)) return false;
    if (!exitEmptyHeading(block)) return false;
    e.preventDefault();
    e.stopPropagation();
    return true;
  }

  /* 空标题按 Backspace：一次退出标题格式。
     （placeCaretAfterMarker 在标记后补了零宽占位符，原生 Backspace 会先吃掉
     不可见字符而保留标题壳，因此这里显式处理。） */
  function handleEmptyBackspace(e) {
    if (!e || e.key !== "Backspace" || e.isComposing) return false;
    if (e.shiftKey || e.altKey || e.ctrlKey || e.metaKey) return false;
    const block = emptyPrefixBlock();
    if (!block || !/^H[1-6]$/.test(block.tagName)) return false;
    if (!exitEmptyHeading(block)) return false;
    e.preventDefault();
    e.stopPropagation();
    return true;
  }

  // ---------------------------------------------------------------
  // 表格 / 代码块 / 数学块：上下文块操作
  //
  // 容器块不走 DOM 手术：实测 Vditor 会拦截/重建——execCommand delete 对
  // table/math-block 只删内容、块壳残留（并复活为空块），insertText 被
  // beforeinput 吞掉。统一走 Blocks 结构通道：locate(el) 出源码行区间 →
  // probe 校验 → commit 一次 setValue 提交（独立撤销边界、保滚动、补脏标记）。
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

  // —— md 区间替换由 Blocks.replaceEl 承载（locate + probe + commit）——

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
    return window.Blocks && window.Blocks.replaceEl(table, newText, probe) ? 1 : 0;
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
          return window.Blocks && window.Blocks.replaceEl(el, sourceToParagraphs(src), probe) ? 1 : 0;
        case "del":
          return window.Blocks && window.Blocks.replaceEl(el, null, probe) ? 1 : 0;
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

  window.Convert = {
    apply, itemsForContext, blockItemsForContext, applyBlockOp,
    settleEmptyFormat, handleEmptyEnter, handleEmptyBackspace, isVisuallyEmpty,
    targets: () => TARGETS,
  };
})();
