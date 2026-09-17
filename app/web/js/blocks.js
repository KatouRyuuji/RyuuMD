/* 结构引擎：把 Lute 规范输出的 md 全文扫描成块树，与 IR 面板 DOM 逐层对齐，
   使任意深度的 DOM 元素都能获得源码行区间 [start,end)。所有结构操作
   （移动/删除/转换/升降级/整节）统一走「区间行替换 + setValue + notifyChange」
   事务通道 commit()，操作前后各记一次撤销边界，拥有独立撤销记录。

   定位原则：不靠「文字相同」或「同类第 N 个 DOM」，而是
     scanDoc(行扫描) → 块树 ⇄ domTree(面板镜像) → align 逐层平行对齐
     （数量相等且类型相容；h 校验 level）。对齐失败一律放弃操作。
   扫描规则按 Lute 规范输出形态设计：顶层块间空行分隔、引用内空行写作 ">"、
   setext 标题保留 setext 形态、松散列表续段后不补空行。容器（引用/列表）
   内部的标题是容器 children，不进顶层序列，整节推导天然不被其截断。

   sv 模式无块级 DOM，用 locateLine(行号) 直接定位顶层块。 */
(function () {
  // ---------------------------------------------------------------
  // 行模式
  // ---------------------------------------------------------------

  const RE = {
    fence: /^\s{0,3}(```+|~~~+)/,
    math: /^\s{0,3}\$\$\s*$/,
    heading: /^\s{0,3}(#{1,6})(?:\s+|$)/,
    quote: /^\s{0,3}>[ \t]?/,
    list: /^(\s*)([-*+]|\d{1,9}[.)])([ \t]+|$)(\[[ xX]\][ \t]+)?/,
    hr: /^\s{0,3}(?:([*_])[ \t]*){3,}$|^\s{0,3}(-[ \t]*){3,}$/,
    tableRow: /^\s*\|.*\|\s*$/,
    tableAlign: /^\s*\|[\s:|-]*-[\s:|-]*\|\s*$/, // 只含 | : - 空格且至少一个 -
    html: /^\s{0,3}<[a-zA-Z!/]/,
    toc: /^\s{0,3}\[toc\]\s*$/i,
    footnote: /^\s{0,3}\[\^[^\]]+\]:/,
    setext: /^\s{0,3}(=+|-+)[ \t]*$/,
    blank: /^\s*$/,
  };

  function isBlank(l) { return RE.blank.test(l); }
  function indentOf(l) { const m = /^[ \t]*/.exec(l); return m ? m[0].replace(/\t/g, "    ").length : 0; }

  /* 该行是否能中断段落/列表（标题、围栏、数学块、hr、表格头） */
  function interrupts(line, nextLine) {
    if (RE.fence.test(line) || RE.math.test(line) || RE.heading.test(line)) return true;
    if (RE.hr.test(line) && !RE.list.test(line)) return true;
    if (RE.tableRow.test(line) && nextLine != null && RE.tableAlign.test(nextLine)) return true;
    return false;
  }

  // ---------------------------------------------------------------
  // scanDoc：行扫描 → 块树。节点 {type,start,end,...}，行号为绝对行号（[start,end)）
  //   h{level} / p / list{items} / quote{children} / code / math / table / hr / html
  //   list.items: [{start,end,indent,contentCol,ordered,todo,items:[嵌套项]}]
  // ---------------------------------------------------------------

  function scanDoc(lines) { return scanRange(lines, 0); }

  function scanRange(lines, off) {
    const nodes = [];
    const n = lines.length;
    let i = 0;
    while (i < n) {
      const line = lines[i];
      if (isBlank(line)) { i++; continue; }
      let m = line.match(RE.fence);
      if (m) {
        const close = new RegExp("^\\s{0,3}" + (m[1][0] === "`" ? "`" : "~") + "{" + m[1].length + ",}\\s*$");
        let j = i + 1;
        while (j < n && !close.test(lines[j])) j++;
        nodes.push({ type: "code", start: off + i, end: off + Math.min(j + 1, n) });
        i = Math.min(j + 1, n);
        continue;
      }
      if (RE.math.test(line)) {
        let j = i + 1;
        while (j < n && !RE.math.test(lines[j])) j++;
        nodes.push({ type: "math", start: off + i, end: off + Math.min(j + 1, n) });
        i = Math.min(j + 1, n);
        continue;
      }
      if (RE.tableRow.test(line) && i + 1 < n && RE.tableAlign.test(lines[i + 1])) {
        let j = i + 2;
        while (j < n && RE.tableRow.test(lines[j])) j++;
        nodes.push({ type: "table", start: off + i, end: off + j });
        i = j;
        continue;
      }
      m = line.match(RE.heading);
      if (m) {
        nodes.push({ type: "h", level: m[1].length, start: off + i, end: off + i + 1 });
        i++;
        continue;
      }
      if (RE.hr.test(line) && !RE.list.test(line)) {
        nodes.push({ type: "hr", start: off + i, end: off + i + 1 });
        i++;
        continue;
      }
      if (RE.quote.test(line)) {
        const inner = [];
        let j = i;
        // 引用内空行在 Lute 规范输出中写作 ">"，纯空行即引用结束
        while (j < n && RE.quote.test(lines[j])) { inner.push(lines[j].replace(RE.quote, "")); j++; }
        nodes.push({ type: "quote", start: off + i, end: off + j, children: scanRange(inner, off + i) });
        i = j;
        continue;
      }
      if (RE.list.test(line)) {
        const res = scanList(lines, i, off);
        nodes.push(res.node);
        i = res.next;
        continue;
      }
      if (RE.html.test(line)) {
        let j = i;
        while (j < n && !isBlank(lines[j])) j++;
        nodes.push({ type: "html", start: off + i, end: off + j });
        i = j;
        continue;
      }
      if (RE.toc.test(line)) {
        nodes.push({ type: "toc", start: off + i, end: off + i + 1 });
        i++;
        continue;
      }
      if (RE.footnote.test(line)) {
        // 脚注定义块：定义行 + 缩进 ≥4 的续行；空行暂收，不续则不计入
        let j = i;
        let last = i;
        while (j + 1 < n) {
          const nxt = lines[j + 1];
          if (isBlank(nxt)) { j++; continue; }
          if (indentOf(nxt) >= 4) { j++; last = j; continue; }
          break;
        }
        nodes.push({ type: "footnote", start: off + i, end: off + last + 1 });
        i = last + 1;
        continue;
      }
      // 段落：消费到空行 / 块起始 / setext 下划线
      let j = i;
      let setext = 0;
      while (j + 1 < n) {
        const nxt = lines[j + 1];
        if (isBlank(nxt)) break;
        const sm = nxt.match(RE.setext);
        if (sm) { setext = sm[1][0] === "=" ? 1 : 2; j++; break; }
        if (interrupts(nxt, lines[j + 2])) break;
        if (RE.quote.test(nxt) || RE.list.test(nxt) || RE.toc.test(nxt) || RE.footnote.test(nxt)) break;
        j++;
      }
      if (setext) nodes.push({ type: "h", level: setext, start: off + i, end: off + j + 1 });
      else nodes.push({ type: "p", start: off + i, end: off + j + 1 });
      i = j + 1;
    }
    return nodes;
  }

  /* 列表块：从 indent ≤3 的标记行起。延续规则：标记行（indent ≥ baseIndent）、
     缩进续行（indent > baseIndent）、无空行间隔的 lazy 续行；空行暂收，
     若其后不再延续则不计入。标题/围栏/hr 等中断列表。 */
  function scanList(lines, i, off) {
    const baseIndent = indentOf(lines[i]);
    const baseM = lines[i].match(RE.list);
    const baseMarker = baseM[2];
    const baseOrdered = /\d/.test(baseMarker);
    const baseTodo = !!baseM[4];
    const n = lines.length;
    let j = i;
    let last = i;
    let sawBlank = false;
    while (j + 1 < n) {
      const nxt = lines[j + 1];
      if (isBlank(nxt)) { sawBlank = true; j++; continue; }
      const ind = indentOf(nxt);
      if (interrupts(nxt, lines[j + 2])) break;
      const lm = nxt.match(RE.list);
      if (lm && ind >= baseIndent) {
        // 同级标记类型切换（有序↔无序、bullet 字符变化、todo↔普通）开启新列表块
        const ordered = /\d/.test(lm[2]);
        if (ind === baseIndent &&
            (ordered !== baseOrdered || (!ordered && lm[2] !== baseMarker) || !!lm[4] !== baseTodo)) break;
        j++; last = j; sawBlank = false; continue;
      }
      if (ind > baseIndent) { j++; last = j; sawBlank = false; continue; }
      if (!sawBlank && ind >= baseIndent && !RE.quote.test(nxt)) { j++; last = j; continue; } // lazy 续行
      break;
    }
    const end = last + 1;
    const node = { type: "list", start: off + i, end: off + end, items: parseItems(lines, i, end, off) };
    return { node, next: end };
  }

  /* 项树：按「子项缩进 ≥ 父项内容列」的缩进栈构造。
     每个 item 的 end = 下一个不更深项的 start，或列表块末。 */
  function parseItems(lines, from, to, off) {
    const rootItems = [];
    const stack = [{ contentCol: -1, items: rootItems, item: null }];
    const closeTo = (k) => { while (stack.length > 1) { stack.pop().item.end = off + k; } };
    for (let k = from; k < to; k++) {
      const m = lines[k].match(RE.list);
      if (!m) continue;
      const ind = m[1].replace(/\t/g, "    ").length;
      const contentCol = m[0].replace(/\t/g, "    ").length;
      while (stack.length > 1 && ind < stack[stack.length - 1].contentCol) {
        stack.pop().item.end = off + k;
      }
      const item = {
        start: off + k,
        end: off + to,
        indent: ind,
        contentCol,
        ordered: /\d/.test(m[2]),
        todo: !!m[4],
        items: [],
      };
      stack[stack.length - 1].items.push(item);
      stack.push({ contentCol, items: item.items, item });
    }
    closeTo(to);
    return rootItems;
  }

  // ---------------------------------------------------------------
  // domTree：IR 面板镜像（顶层块 → 递归容器），节点 {el,type,...}
  // ---------------------------------------------------------------

  function isTopEl(el) {
    if (!el || el.nodeType !== Node.ELEMENT_NODE) return false;
    if (el.getAttribute("data-block") === "0") return true;
    return /^(P|H[1-6]|BLOCKQUOTE|UL|OL|PRE|TABLE|HR)$/.test(el.tagName);
  }

  function domNodeFor(el) {
    const t = el.tagName;
    if (/^H[1-6]$/.test(t)) return { el, type: "h", level: parseInt(t[1], 10) };
    if (t === "P") return { el, type: "p" };
    if (t === "BLOCKQUOTE") {
      return { el, type: "quote", children: [...el.children].filter(isTopEl).map(domNodeFor) };
    }
    if (t === "UL" || t === "OL") {
      return {
        el, type: "list",
        items: [...el.children].filter((c) => c.tagName === "LI").map((li) => ({
          el: li,
          nested: [...li.children].filter((c) => /^(UL|OL)$/.test(c.tagName)).map(domNodeFor),
        })),
      };
    }
    if (t === "TABLE") return { el, type: "table" };
    if (t === "HR") return { el, type: "hr" };
    const dt = el.getAttribute && el.getAttribute("data-type");
    if (dt === "code-block") return { el, type: "code" };
    if (dt === "math-block") return { el, type: "math" };
    return { el, type: "html" };
  }

  function domTree(panel) {
    return [...panel.children].filter(isTopEl).map(domNodeFor);
  }

  // ---------------------------------------------------------------
  // align：逐层平行对齐 DOM 镜像与扫描块树，成功后在 DOM 节点上挂 .md 反查。
  // 数量相等且类型相容（h 校验 level；ul/ol/todo 同为 list 相容）；
  // 任一不符返回 false——调用方放弃操作。
  // ---------------------------------------------------------------

  function compat(d, m) {
    if (!d || !m || d.type !== m.type) return false;
    if (d.type === "h") return d.level === m.level;
    return true;
  }

  /* 把一个 md 项的嵌套项按「连续同 indent」分组为若干子列表（与 DOM li 内
     的若干 :scope>ul/ol 一一对应） */
  function groupNested(items) {
    const groups = [];
    items.forEach((it) => {
      const g = groups[groups.length - 1];
      if (g && g.indent === it.indent) g.items.push(it);
      else groups.push({ indent: it.indent, items: [it] });
    });
    return groups;
  }

  function alignItems(domItems, mdItems) {
    if (domItems.length !== mdItems.length) return false;
    for (let k = 0; k < domItems.length; k++) {
      const d = domItems[k];
      const m = mdItems[k];
      d.md = m;
      const groups = groupNested(m.items);
      if (d.nested.length !== groups.length) return false;
      for (let g = 0; g < groups.length; g++) {
        d.nested[g].md = null; // 嵌套列表整体无独立 md 节点（区间由 items 覆盖）
        if (!alignItems(d.nested[g].items, groups[g].items)) return false;
      }
    }
    return true;
  }

  function align(domNodes, mdNodes) {
    if (domNodes.length !== mdNodes.length) return false;
    for (let k = 0; k < domNodes.length; k++) {
      const d = domNodes[k];
      const m = mdNodes[k];
      if (!compat(d, m)) return false;
      d.md = m;
      if (d.type === "quote" && !align(d.children, m.children)) return false;
      if (d.type === "list" && !alignItems(d.items, m.items)) return false;
    }
    return true;
  }

  // ---------------------------------------------------------------
  // locate：DOM 元素 → 源码区间；locateLine：行号 → 顶层块
  // ---------------------------------------------------------------

  function panel() {
    return window.Editor && window.Editor.panel ? window.Editor.panel() : null;
  }

  function alignedTree() {
    const p = panel();
    if (!p || !window.Editor || window.Editor.getMode() === "sv") return null;
    const mdNodes = scanDoc(window.Editor.getValue().split("\n"));
    const domNodes = domTree(p);
    return align(domNodes, mdNodes) ? domNodes : null;
  }

  function findEl(nodes, el) {
    for (const d of nodes) {
      if (d.el === el) return d;
      if (d.type === "quote") { const r = findEl(d.children, el); if (r) return r; }
      if (d.type === "list") {
        for (const it of d.items) {
          if (it.el === el) return it;
          for (const nl of it.nested) { const r = findEl([nl], el); if (r) return r; }
        }
      }
    }
    return null;
  }

  /* el → {type, start, end, node}；list item 节点也带 start/end（来自 .md） */
  function locate(el) {
    const tree = alignedTree();
    if (!tree) return null;
    const hit = findEl(tree, el);
    if (!hit) return null;
    if (hit.md) return { type: hit.type || "item", start: hit.md.start, end: hit.md.end, node: hit };
    if (hit.start != null) return { type: "item", start: hit.start, end: hit.end, node: hit };
    return null;
  }

  /* 行号 → 顶层块 {type, start, end, node?, md}（sv/ir 通用；ir 附带 DOM 节点） */
  function locateLine(lineIdx) {
    const mdNodes = scanDoc(window.Editor.getValue().split("\n"));
    const md = mdNodes.find((b) => b.start <= lineIdx && lineIdx < b.end);
    if (!md) return null;
    if (window.Editor.getMode() === "sv") return { type: md.type, start: md.start, end: md.end, md, node: null };
    const tree = alignedTree();
    const dom = tree ? tree.find((d) => d.md === md) : null;
    return { type: md.type, start: md.start, end: md.end, md, node: dom || null };
  }

  // ---------------------------------------------------------------
  // commit：结构操作事务通道
  //   前置撤销边界 → 行区间替换 → setValue → notifyChange（脏标记/大纲）→
  //   后置撤销边界 → 滚动比例恢复 → 可选光标安置。
  //   probe(segLines) 可选：替换前校验区间与目标确为同一块，不符整体放弃。
  // ---------------------------------------------------------------

  function commit(start, end, newLines, opts) {
    const E = window.Editor;
    if (!E || !E.isReady()) return false;
    const lines = E.getValue().split("\n");
    if (start < 0 || end > lines.length || start > end) return false;
    if (opts && opts.probe && !opts.probe(lines.slice(start, end))) return false;
    E.recordUndoBoundary();
    const ratio = E.getScrollRatio();
    lines.splice(start, end - start, ...(newLines || []));
    E.setValue(lines.join("\n"));
    E.notifyChange();
    E.recordUndoBoundary();
    setTimeout(() => E.setScrollRatio(ratio), 120);
    if (opts && opts.caretLine != null) setTimeout(() => placeCaretAtLine(opts.caretLine), 80);
    return true;
  }

  /* 多区间批量提交（选区跨块转换等）：先校验全部 probe，再一次 setValue。
     jobs: [{start, end, newLines, probe?}]，任一 probe 不符整体放弃。 */
  function commitRanges(jobs, opts) {
    const E = window.Editor;
    if (!E || !E.isReady() || !jobs.length) return false;
    const lines = E.getValue().split("\n");
    for (const j of jobs) {
      if (j.start < 0 || j.end > lines.length || j.start > j.end) return false;
      if (j.probe && !j.probe(lines.slice(j.start, j.end))) return false;
    }
    E.recordUndoBoundary();
    const ratio = E.getScrollRatio();
    const sorted = jobs.slice().sort((a, b) => b.start - a.start);
    for (const j of sorted) lines.splice(j.start, j.end - j.start, ...(j.newLines || []));
    E.setValue(lines.join("\n"));
    E.notifyChange();
    E.recordUndoBoundary();
    setTimeout(() => E.setScrollRatio(ratio), 120);
    if (opts && opts.caretLine != null) setTimeout(() => placeCaretAtLine(opts.caretLine), 80);
    return true;
  }

  /* 光标安置：ir 把光标折叠到包含该行的顶层块内容末尾；sv 按行首字符偏移
     走文本节点。找不到时只保滚动，不报错。 */
  function placeCaretAtLine(lineIdx) {
    const E = window.Editor;
    if (!E) return;
    E.focus();
    const sel = window.getSelection();
    if (!sel) return;
    if (E.getMode() === "sv") {
      const p = panel();
      if (!p) return;
      const lines = E.getValue().split("\n");
      let off = 0;
      for (let k = 0; k < Math.min(lineIdx, lines.length); k++) off += lines[k].length + 1;
      const walker = document.createTreeWalker(p, NodeFilter.SHOW_TEXT);
      let node;
      while ((node = walker.nextNode())) {
        if (off <= node.nodeValue.length) {
          const r = document.createRange();
          r.setStart(node, off);
          r.collapse(true);
          sel.removeAllRanges();
          sel.addRange(r);
          return;
        }
        off -= node.nodeValue.length;
      }
      return;
    }
    const loc = locateLine(lineIdx);
    if (!loc || !loc.node) {
      // 该行已不属于任何块（如全文只剩一个空行）：光标落到面板首个可编辑块
      const p = panel();
      const first = p && (p.querySelector("p,h1,h2,h3,h4,h5,h6,li,blockquote,table,pre,div[data-type]") || p.firstElementChild);
      if (first && sel) {
        const r = document.createRange();
        r.selectNodeContents(first);
        r.collapse(true);
        sel.removeAllRanges();
        sel.addRange(r);
      }
      return;
    }
    const r = document.createRange();
    r.selectNodeContents(loc.node.el);
    r.collapse(false);
    sel.removeAllRanges();
    sel.addRange(r);
  }

  /* 首行探针：md 区间首个非空行（剥块级标记）与 DOM 元素首行比对。
     供 commit 的 opts.probe 复用，对齐漂移时放弃操作。 */
  function stripMarks(line) {
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

  /* 元素文本还原：<br> 还原换行，剔除零宽字符；嵌套容器（内层 UL/OL/BLOCKQUOTE）
     整棵子树跳过——其文本属于嵌套行。 */
  function textLines(el) {
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
    return out.replace(/​/g, "").split("\n");
  }

  function probeFirstLine(el) {
    const t = el.tagName;
    let domFirst;
    if (t === "UL" || t === "OL") {
      const li = el.querySelector(":scope > li");
      domFirst = li ? stripMarks(textLines(li)[0] || "") : "";
    } else if (t === "LI") {
      domFirst = stripMarks(textLines(el)[0] || "");
    } else if (t === "BLOCKQUOTE") {
      const kid = [...el.children].find((c) => /^(P|LI|H[1-6])$/.test(c.tagName));
      domFirst = stripMarks(textLines(kid || el)[0] || "");
    } else {
      domFirst = stripMarks(textLines(el)[0] || "");
    }
    domFirst = domFirst.trim();
    return (seg) => {
      const segFirst = stripMarks((seg.find((l) => l.trim() !== "")) || "").trim();
      return segFirst === domFirst;
    };
  }

  /* 元素级整换：locate(el) + probe + commit。newText=null 删除整块。
     opts 透传给 commit（如 caretLine）。 */
  function replaceEl(el, newText, probe, opts) {
    const loc = locate(el);
    if (!loc) return false;
    const repl = newText == null ? [] : newText.replace(/\n+$/, "").split("\n");
    return commit(loc.start, loc.end, repl, Object.assign({ probe: probe || probeFirstLine(el) }, opts || {}));
  }

  // ---------------------------------------------------------------
  // 整节：顶层 h 到下一个同级或更高级顶层 h 之前
  // ---------------------------------------------------------------

  function sectionRange(mdNodes, hNode) {
    const idx = mdNodes.indexOf(hNode);
    if (idx < 0 || hNode.type !== "h") return null;
    let end = -1;
    for (let k = idx + 1; k < mdNodes.length; k++) {
      if (mdNodes[k].type === "h" && mdNodes[k].level <= hNode.level) { end = mdNodes[k].start; break; }
    }
    if (end < 0) {
      const total = window.Editor.getValue().split("\n").length;
      end = total;
      while (end > hNode.start && isBlank(window.Editor.getValue().split("\n")[end - 1])) end--;
    }
    return [hNode.start, end];
  }

  /* 整节标题等级平移（纯函数）：delta 应用于区间内全部顶层标题，保持相对
     等级；任一标题越出 1..6 整体拒绝。setext 标题重写为 ATX。
     返回 {ok, lines?, reason?}（lines 为区间内的新行数组）。 */
  function shiftSectionLines(segLines, delta) {
    const nodes = scanRange(segLines, 0);
    const out = segLines.slice();
    for (const b of nodes) {
      if (b.type !== "h") continue;
      const next = b.level + delta;
      if (next < 1 || next > 6) {
        return { ok: false, reason: "标题等级范围为 H1–H6，整节" + (delta > 0 ? "降级" : "升级") + "会超出边界" };
      }
    }
    const marks = (lv) => "#".repeat(lv);
    for (const b of nodes) {
      if (b.type !== "h") continue;
      // ATX 单行、setext 双行统一重写为 ATX；内容取剥标记后的首行文本
      const stripped = stripMarks(segLines[b.start]).trim();
      out.splice(b.start, b.end - b.start, marks(b.level + delta) + " " + stripped);
    }
    return { ok: true, lines: out };
  }

  /* 相邻移动：目标块与前/后一个顶层块（含块间空行分隔）互换位置。
     loc 为 locate/locateLine 结果；dir=-1 上移、+1 下移。越界返回 false。 */
  function moveBlock(loc, dir, probe) {
    const lines = window.Editor.getValue().split("\n");
    const tree = scanDoc(lines);
    const idx = tree.findIndex((b) => b.start === loc.start && b.end === loc.end);
    if (idx < 0) return false;
    const j = idx + (dir < 0 ? -1 : 1);
    if (j < 0 || j >= tree.length) return false;
    const a = tree[idx];
    const b = tree[j];
    const aLines = lines.slice(a.start, a.end);
    const bLines = lines.slice(b.start, b.end);
    let start, end, newLines;
    if (dir < 0) {
      const gap = lines.slice(b.end, a.start);
      start = b.start; end = a.end;
      newLines = aLines.concat(gap, bLines);
    } else {
      const gap = lines.slice(a.end, b.start);
      start = a.start; end = b.end;
      newLines = bLines.concat(gap, aLines);
    }
    return commit(start, end, newLines, { probe });
  }

  /* 删除顶层块（loc 区间替换为空）。 */
  function deleteBlock(loc, probe) {
    return commit(loc.start, loc.end, [], { probe });
  }

  // ---------------------------------------------------------------
  // 列表项与容器归属操作
  // ---------------------------------------------------------------

  /* 在项树中按 start 找 item，返回 {item, siblings, parent}（parent 为父项或 null） */
  function findItemIn(items, start, parent) {
    for (const it of items) {
      if (it.start === start) return { item: it, siblings: items, parent: parent || null };
      const r = findItemIn(it.items, start, it);
      if (r) return r;
    }
    return null;
  }

  function findListItem(tree, start) {
    for (const b of tree) {
      if (b.type !== "list" || !(b.start <= start && start < b.end)) continue;
      const r = findItemIn(b.items, start, null);
      return r ? Object.assign({ list: b }, r) : null;
    }
    return null;
  }

  /* 列表项同级移动（携带续段与子列表）：与上/下一个同级项互换区间。 */
  function moveItem(loc, dir) {
    const lines = window.Editor.getValue().split("\n");
    const found = findListItem(scanDoc(lines), loc.start);
    if (!found) return false;
    const idx = found.siblings.indexOf(found.item);
    const j = idx + (dir < 0 ? -1 : 1);
    if (j < 0 || j >= found.siblings.length) return false;
    const a = found.item;
    const b = found.siblings[j];
    let start, end, newLines;
    if (dir < 0) {
      start = b.start; end = a.end;
      newLines = lines.slice(a.start, a.end).concat(lines.slice(b.start, b.end));
    } else {
      start = a.start; end = b.end;
      newLines = lines.slice(b.start, b.end).concat(lines.slice(a.start, a.end));
    }
    return commit(start, end, newLines, {});
  }

  /* 移入：成为上一个同级项的子项（整体右移缩进，子树随动）。 */
  function indentItem(loc) {
    const lines = window.Editor.getValue().split("\n");
    const found = findListItem(scanDoc(lines), loc.start);
    if (!found) return false;
    const idx = found.siblings.indexOf(found.item);
    if (idx <= 0) return false;
    const prev = found.siblings[idx - 1];
    let delta = prev.contentCol - found.item.indent;
    if (delta < 2) delta = 2;
    const pad = " ".repeat(delta);
    const newLines = lines.slice(loc.start, loc.end).map((l) => (l.trim() === "" ? l : pad + l));
    return commit(loc.start, loc.end, newLines, {});
  }

  /* 移出：提升一级（整体左移；子项随动）。顶层项的移出 = 剥标记退为段落。 */
  function outdentItem(loc) {
    const lines = window.Editor.getValue().split("\n");
    const found = findListItem(scanDoc(lines), loc.start);
    if (!found) return false;
    const it = found.item;
    const seg = lines.slice(loc.start, loc.end);
    let newLines;
    if (found.parent) {
      const delta = Math.max(it.indent - found.parent.indent, 1);
      newLines = seg.map((l) => l.replace(new RegExp("^ {0," + delta + "}"), ""));
    } else {
      // 顶层项：首行剥标记成为段落，其余行左移 contentCol
      newLines = seg.slice();
      newLines[0] = seg[0].replace(RE.list, "");
      newLines = newLines.map((l, i2) => (i2 === 0 ? l : l.replace(new RegExp("^ {0," + it.contentCol + "}"), "")));
    }
    return commit(loc.start, loc.end, newLines, {});
  }

  /* 把源区间剪切、插入目标顶层块前/后（块间空行分隔自动维持）。 */
  function moveBlockTo(loc, target, before, isItem) {
    if (isItem) return moveItemNearBlock(loc, target, before);
    const lines = window.Editor.getValue().split("\n");
    const seg = lines.slice(loc.start, loc.end);
    const rest = lines.slice();
    rest.splice(loc.start, loc.end - loc.start);
    collapseBlankAt(rest, loc.start);
    let at = before ? target.start : target.end;
    if (at > loc.start) at -= loc.end - loc.start;
    const ins = seg.slice();
    if (at > 0 && rest[at - 1] && rest[at - 1].trim() !== "") ins.unshift("");
    if (at < rest.length && rest[at] && rest[at].trim() !== "") ins.push("");
    rest.splice(at, 0, ...ins);
    return commit(0, lines.length, rest, {});
  }

  /* 折叠 at 处因删除产生的连续空行为一个 */
  function collapseBlankAt(lines, at) {
    while (at > 0 && at < lines.length && isBlank(lines[at]) && isBlank(lines[at - 1])) lines.splice(at, 1);
  }

  /* 列表项搬到顶层块前/后：目标是列表则并入其首/尾（按列表基准缩进），
     否则作为独立列表块落在目标前/后。 */
  function moveItemNearBlock(loc, target, before) {
    const lines = window.Editor.getValue().split("\n");
    const seg = lines.slice(loc.start, loc.end);
    const rest = lines.slice();
    rest.splice(loc.start, loc.end - loc.start);
    collapseBlankAt(rest, loc.start);
    let at = before ? target.start : target.end;
    if (at > loc.start) at -= loc.end - loc.start;
    const tree = scanDoc(rest);
    const tb = tree.find((b) => b.start <= at && at <= b.end);
    let ins = seg;
    if (tb && tb.type === "list") {
      const base = indentOf(rest[tb.start]);
      const cur = indentOf(seg[0]);
      ins = seg.map((l) => {
        if (l.trim() === "") return l;
        const d = base - cur;
        return d >= 0 ? " ".repeat(d) + l : l.replace(new RegExp("^ {0," + -d + "}"), "");
      });
      at = before ? tb.start : tb.end;
    } else {
      if (at > 0 && rest[at - 1] && rest[at - 1].trim() !== "") ins = [""].concat(ins);
      if (at < rest.length && rest[at] && rest[at].trim() !== "") ins = ins.concat([""]);
    }
    rest.splice(at, 0, ...ins);
    return commit(0, lines.length, rest, {});
  }

  /* 源（列表项或段落块）成为目标项的最后一个子项：
     按目标项内容列重写源区间前导空白，保留内部嵌套关系。 */
  function moveItemToChild(loc, target, isItem) {
    const lines = window.Editor.getValue().split("\n");
    const tree = scanDoc(lines);
    const tFound = findListItem(tree, target.start);
    if (!tFound) return false;
    const seg = lines.slice(loc.start, loc.end);
    const srcBase = isItem ? indentOf(seg[0]) : 0;
    const delta = tFound.item.contentCol - srcBase;
    const shifted = seg.map((l) => {
      if (l.trim() === "") return l;
      return delta >= 0 ? " ".repeat(delta) + l : l.replace(new RegExp("^ {0," + -delta + "}"), "");
    });
    const rest = lines.slice();
    rest.splice(loc.start, loc.end - loc.start);
    collapseBlankAt(rest, loc.start);
    let at = tFound.item.end;
    if (at > loc.start) at -= loc.end - loc.start;
    rest.splice(at, 0, ...shifted);
    return commit(0, lines.length, rest, {});
  }

  /* 移入引用：源区间每行加 "> "，插到引用块末尾（内部段落与嵌套保留）。
     前一行是非空引用内容时先补 ">" 空行分隔，避免并段。 */
  function moveIntoQuote(loc, target) {
    const lines = window.Editor.getValue().split("\n");
    const seg = lines.slice(loc.start, loc.end).map((l) => (l.trim() === "" ? ">" : "> " + l));
    const rest = lines.slice();
    rest.splice(loc.start, loc.end - loc.start);
    collapseBlankAt(rest, loc.start);
    let at = target.end;
    if (at > loc.start) at -= loc.end - loc.start;
    if (at > 0 && RE.quote.test(rest[at - 1] || "") && rest[at - 1].trim() !== ">") seg.unshift(">");
    rest.splice(at, 0, ...seg);
    return commit(0, lines.length, rest, {});
  }

  /* 移出引用：引用内的源区间剥一层 ">"，提到引用块之后。 */
  function moveOutOfQuote(loc, quoteLoc) {
    const lines = window.Editor.getValue().split("\n");
    const seg = lines.slice(loc.start, loc.end).map((l) => l.replace(/^\s{0,3}>[ \t]?/, ""));
    const rest = lines.slice();
    rest.splice(loc.start, loc.end - loc.start);
    collapseBlankAt(rest, loc.start);
    let at = quoteLoc.end;
    if (at > loc.start) at -= loc.end - loc.start;
    if (at > 0 && rest[at - 1] && rest[at - 1].trim() !== "") seg.push("");
    rest.splice(at, 0, ...seg);
    return commit(0, lines.length, rest, {});
  }

  /* 行号所在顶层标题的整节区间 {start, end}；该行不是顶层标题返回 null */
  function sectionAt(lineIdx) {
    const lines = window.Editor.getValue().split("\n");
    const tree = scanDoc(lines);
    const h = tree.find((b) => b.type === "h" && b.start === lineIdx);
    if (!h) return null;
    const r = sectionRange(tree, h);
    return r ? { start: r[0], end: r[1] } : null;
  }

  /* 源整节成为目标整节的子节：根标题等级 = 目标根等级 + 1，相对深度保持。
     任一标题越出 H1..H6 返回 {ok:false, reason}。 */
  function moveSectionToChild(srcLoc, dstLoc) {
    const lines = window.Editor.getValue().split("\n");
    const tree = scanDoc(lines);
    const srcH = tree.find((b) => b.type === "h" && b.start === srcLoc.start);
    const dstH = tree.find((b) => b.type === "h" && b.start === dstLoc.start);
    if (!srcH || !dstH) return { ok: false };
    const shifted = shiftSectionLines(lines.slice(srcLoc.start, srcLoc.end), dstH.level - srcH.level + 1);
    if (!shifted.ok) return { ok: false, reason: shifted.reason };
    const rest = lines.slice();
    rest.splice(srcLoc.start, srcLoc.end - srcLoc.start);
    collapseBlankAt(rest, srcLoc.start);
    let at = dstLoc.end;
    if (at > srcLoc.start) at -= srcLoc.end - srcLoc.start;
    const ins = shifted.lines.slice();
    if (at > 0 && rest[at - 1] && rest[at - 1].trim() !== "") ins.unshift("");
    if (at < rest.length && rest[at] && rest[at].trim() !== "") ins.push("");
    rest.splice(at, 0, ...ins);
    return { ok: commit(0, lines.length, rest, {}) };
  }

  /* 整节等级平移提交（保持相对等级，越界整体拒绝） */
  function shiftSectionLevel(loc, delta) {
    const lines = window.Editor.getValue().split("\n");
    const res = shiftSectionLines(lines.slice(loc.start, loc.end), delta);
    if (!res.ok) return { ok: false, reason: res.reason };
    return { ok: commit(loc.start, loc.end, res.lines, {}) };
  }

  window.Blocks = {
    scanDoc, domTree, align, locate, locateLine,
    commit, commitRanges, replaceEl, probeFirstLine, stripMarks, placeCaretAtLine,
    sectionRange, shiftSectionLines, moveBlock, deleteBlock,
    moveItem, indentItem, outdentItem, moveBlockTo, moveItemToChild,
    moveIntoQuote, moveOutOfQuote, sectionAt, moveSectionToChild, shiftSectionLevel,
  };
})();
