/* 开发期对拍：blocks.js scanDoc 顶层块划分 vs Lute Md2VditorIRDOM 顶层块。
   运行：node tests/dev/scan_check.js */
const fs = require("fs");
const path = require("path");
const ROOT = path.join(__dirname, "..", "..");

require(path.join(ROOT, "app/web/vendor/vditor/dist/js/lute/lute.min.js"));
global.window = {};
require(path.join(ROOT, "app/web/js/blocks.js"));
const { scanDoc } = window.Blocks;

const lute = Lute.New();
lute.SetVditorIR(true);
lute.SetToC(true);
lute.SetFootnotes(true);
lute.SetAutoSpace(true);
lute.SetCodeSyntaxHighlight(false);

const VOID = new Set(["br", "hr", "img", "input", "wbr", "link", "meta", "col"]);
/* 机器生成 HTML 的深度 0 顶层元素标签序列 */
function topTags(html) {
  const tags = [];
  let depth = 0, i = 0;
  const re = /<(\/?)([a-zA-Z][a-zA-Z0-9]*)((?:"[^"]*"|'[^']*'|[^>"'])*)>/g;
  while ((i = re.exec(html))) {
    const [, close, tag, attrs] = i;
    const t = tag.toLowerCase();
    if (VOID.has(t) || /\/>$/.test(i[0])) {
      if (!close && depth === 0) tags.push(t);
      continue;
    }
    if (!close) {
      if (depth === 0) tags.push(t === "div" ? "div" + (attrs.match(/data-type="([^"]*)"/) ? "[" + attrs.match(/data-type="([^"]*)"/)[1] + "]" : "") : t);
      depth++;
    } else {
      depth--;
    }
  }
  return tags;
}

/* scanDoc 类型 → IR 顶层标签 */
function expectTag(n) {
  switch (n.type) {
    case "h": return "h" + n.level;
    case "p": return "p";
    case "list": return "ul|ol";
    case "quote": return "blockquote";
    case "code": return "div[code-block]";
    case "math": return "div[math-block]";
    case "table": return "table";
    case "hr": return "hr";
    case "html": return "div[html-block]|p|div";
    case "toc": return "div[toc-block]";
    case "footnote": return "div[footnotes-block]";
    default: return n.type;
  }
}

const docs = {
  基础混合: "# 标题\n\n段落一\n\n- a\n- b\n\n> 引用\n\n```js\ncode\n```\n\n| x | y |\n|---|---|\n| 1 | 2 |\n\n---\n\n$$x^2$$\n\n尾段",
  嵌套列表: "- parentA\n  - childB\n- parentC\n\n  续段 C\n\n- parentD",
  松散列表: "- a\n\n  cont\n\n- b\n\n  - nested\n\n    deep cont\n\n尾段",
  setext标题: "标题一\n=========\n\n正文\n\n标题二\n---------\n\n尾",
  引用内结构: "> ## 内部标题\n>\n> - item\n>   - nested\n>\n> ```\n> # 不是标题\n> ```\n\n外层段落",
  围栏含井号: "```\n# 注释不是标题\n- 不是列表\n```\n\n段落",
  待办混合: "- [ ] task one\n- [x] task two\n  - [ ] sub\n\n1. first\n2. second",
  lazy续行: "- item one\nlazy continuation\n\n- item two\n\n尾段",
  toc: "[toc]\n\n# A\n\n## B\n\n正文",
  脚注: "有脚注[^1]的段落。\n\n[^1]: 脚注内容\n\n下一段",
  html块: "<div>\nhello\n</div>\n\n段落",
  空格式: "## \n\n- \n\n> \n\n正文",
  图片段落: "![图](a.png)\n\n文字",
  行内数学: "段落 $x^2$ 继续\n\n$$ \n\nmath body \n\n$$ \n\n尾",
  样式总览: "# 标题1\n\n## 标题2\n\n### 标题3\n\n#### 标题4\n\n##### 标题5\n\n###### 标题6\n\n普通字体\n\n**加粗字体**\n\n```\n这是代码块这是代码块\n这是代码块这是代码块\n```\n\n> 这是引用这是引用\n> 这是引用这是引用\n\n1. 这是有序列表\n2. 这是有序列表\n3. 这是有序列表\n\n- 这是无序列表\n- 这是无序列表\n- 这是无序列表\n\n- [ ] 这是todo1\n- [x] 这是todo2\n",
};

let fail = 0;
for (const [name, md] of Object.entries(docs)) {
  const mdNorm = lute.VditorIRDOM2Md(lute.Md2VditorIRDOM(md)); // 与编辑器内 getValue 同源的规范化文本
  const html = lute.Md2VditorIRDOM(mdNorm);
  const got = scanDoc(mdNorm.split("\n"));
  const gotTags = got.map(expectTag);
  const wantTags = topTags(html);
  const ok = wantTags.length === gotTags.length && wantTags.every((t, k) => gotTags[k].split("|").includes(t));
  if (!ok) {
    fail++;
    console.log(`✗ ${name}`);
    console.log("  want:", wantTags.join(" "));
    console.log("  got :", got.map((n) => `${n.type}${n.level || ""}[${n.start}-${n.end})`).join(" "));
    console.log("  norm:", JSON.stringify(mdNorm.slice(0, 120)));
  } else {
    console.log(`✓ ${name} (${wantTags.length} 块)`);
  }
}
process.exit(fail ? 1 : 0);
