/* 斜杠命令定义。
   每条命令：
     id, icon, title, desc, group
     keys     展示给用户的触发词（顶部小标签）
     notion   notion/英文风格关键词
     wolai    wolai/拼音缩写关键词
     cn       中文名（两种风格都可搜）
     insert   插入的 Markdown 文本（hint 与右键菜单共用）
   说明：标题/列表等行级标记，Vditor IR 模式本身也支持「#」「-」直接触发，
   这里提供 /命令 作为统一补充入口。 */
(function () {
  const COMMANDS = [
    // —— 标题 1~6 ——
    { id: "h1", icon: "h1", title: "一级标题", desc: "大号章节标题", group: "标题",
      keys: "/h1 · #", notion: ["h1", "heading1", "title1"], wolai: ["bt1"], cn: ["标题", "大标题", "一级标题"],
      insert: "# " },
    { id: "h2", icon: "h2", title: "二级标题", desc: "小节标题", group: "标题",
      keys: "/h2 · ##", notion: ["h2", "heading2"], wolai: ["bt2"], cn: ["标题", "二级标题"],
      insert: "## " },
    { id: "h3", icon: "h3", title: "三级标题", desc: "子节标题", group: "标题",
      keys: "/h3 · ###", notion: ["h3", "heading3"], wolai: ["bt3"], cn: ["标题", "三级标题"],
      insert: "### " },
    { id: "h4", icon: "heading", title: "四级标题", desc: "", group: "标题",
      keys: "/h4", notion: ["h4", "heading4"], wolai: ["bt4"], cn: ["标题", "四级标题"],
      insert: "#### " },
    { id: "h5", icon: "heading", title: "五级标题", desc: "", group: "标题",
      keys: "/h5", notion: ["h5", "heading5"], wolai: ["bt5"], cn: ["标题", "五级标题"],
      insert: "##### " },
    { id: "h6", icon: "heading", title: "六级标题", desc: "", group: "标题",
      keys: "/h6", notion: ["h6", "heading6"], wolai: ["bt6"], cn: ["标题", "六级标题"],
      insert: "###### " },

    // —— 列表 ——
    { id: "ul", icon: "listUnordered", title: "无序列表", desc: "项目符号列表", group: "列表",
      keys: "/wxlb · -", notion: ["ul", "unorderedlist", "bullet", "list"], wolai: ["wxlb", "wx"], cn: ["无序列表", "列表"],
      insert: "- " },
    { id: "ol", icon: "listOrdered", title: "有序列表", desc: "数字编号列表", group: "列表",
      keys: "/yxlb · 1.", notion: ["ol", "orderedlist", "number"], wolai: ["yxlb", "yx"], cn: ["有序列表", "编号列表"],
      insert: "1. " },
    { id: "todo", icon: "todo", title: "待办列表", desc: "可勾选的任务清单", group: "列表",
      keys: "/lb · todo", notion: ["todo", "todolist", "task", "checkbox"], wolai: ["lb", "dblb", "drlb"], cn: ["待办列表", "任务列表", "清单"],
      insert: "- [ ] " },

    // —— 块 ——
    { id: "code", icon: "code", title: "代码块", desc: "带语法高亮的代码", group: "块",
      keys: "/dmk · ```", notion: ["code", "codeblock", "pre"], wolai: ["dmk", "dm"], cn: ["代码块", "代码"],
      insert: "```\n\n```\n" },
    { id: "formula", icon: "formula", title: "公式块", desc: "KaTeX 数学公式", group: "块",
      keys: "/gsk · $$", notion: ["formula", "math", "katex", "latex"], wolai: ["gsk", "gs", "gongshi"], cn: ["公式块", "公式", "数学"],
      insert: "$$\n\n$$\n" },
    { id: "table", icon: "table", title: "表格", desc: "插入 3×2 表格", group: "块",
      keys: "/bg · table", notion: ["table", "grid"], wolai: ["bg", "bge"], cn: ["表格"],
      insert: "| 列1 | 列2 | 列3 |\n| --- | --- | --- |\n|  |  |  |\n" },
    { id: "quote", icon: "quote", title: "引用", desc: "引用块", group: "块",
      keys: "/quote · >", notion: ["quote", "blockquote"], wolai: ["yy", "yinyong"], cn: ["引用"],
      insert: "> " },
    { id: "hr", icon: "hr", title: "分割线", desc: "水平分割线", group: "块",
      keys: "/hr · ---", notion: ["hr", "divider", "rule"], wolai: ["fgx", "fg", "spfgx"], cn: ["分割线", "水平分割线"],
      insert: "\n---\n" },
    { id: "toc", icon: "toc", title: "内容目录", desc: "自动生成文档目录", group: "块",
      keys: "/toc · [toc]", notion: ["toc", "content", "catalog"], wolai: ["ml", "nrml", "mulu"], cn: ["内容目录", "目录"],
      insert: "[toc]\n\n" },

    // —— 内联 / 媒体 ——
    { id: "image", icon: "image", title: "图片", desc: "插入图片", group: "媒体",
      keys: "/tp · img", notion: ["image", "img", "pic", "photo"], wolai: ["tp", "tpian"], cn: ["图片"],
      insert: "![描述](图片地址)" },
    { id: "link", icon: "link", title: "链接", desc: "超链接", group: "媒体",
      keys: "/lj · link", notion: ["link", "url", "a"], wolai: ["lj", "lianjie"], cn: ["链接", "超链接"],
      insert: "[链接文字](https://)" },
    { id: "wiki", icon: "wiki", title: "双向链接", desc: "[[笔记名]]，Ctrl+单击打开", group: "媒体",
      keys: "/wiki · [[", notion: ["wiki", "wikilink", "bidirectional"], wolai: ["sxlj", "wiki"], cn: ["双向链接", "维基链接", "双链"],
      insert: "[[笔记名]]" },
    { id: "footnote", icon: "footnote", title: "脚注", desc: "插入脚注引用与定义", group: "媒体",
      keys: "/jz · note", notion: ["footnote", "note"], wolai: ["jz", "jiaozhu"], cn: ["脚注"],
      insert: "[^1]\n\n[^1]: 脚注内容\n" },
    { id: "bold", icon: "bold", title: "加粗", desc: "粗体文字", group: "格式",
      keys: "/jc · **", notion: ["bold", "strong"], wolai: ["jc", "jiacu", "cb"], cn: ["加粗", "粗体"],
      insert: "**粗体**" },
    { id: "italic", icon: "italic", title: "斜体", desc: "斜体文字", group: "格式",
      keys: "/xt · *", notion: ["italic", "em"], wolai: ["xt", "xieti"], cn: ["斜体"],
      insert: "*斜体*" },
    { id: "date", icon: "calendar", title: "今天日期", desc: "插入 YYYY-MM-DD", group: "插入",
      keys: "/date", notion: ["date", "today"], wolai: ["rq", "riqi"], cn: ["日期", "今天"],
      insert: "" },
    { id: "time", icon: "clock", title: "当前时间", desc: "插入日期与时间", group: "插入",
      keys: "/time", notion: ["time", "now", "datetime"], wolai: ["sj", "shijian"], cn: ["时间", "此刻"],
      insert: "" },
    { id: "mark", icon: "bold", title: "高亮", desc: "==高亮文字==", group: "格式",
      keys: "/mark", notion: ["mark", "highlight"], wolai: ["gy", "gaoliang"], cn: ["高亮"],
      insert: "==高亮==" },
    { id: "callout", icon: "quote", title: "提示块", desc: "引用式提示 / 警告", group: "块",
      keys: "/tip", notion: ["callout", "tip", "note", "admonition"], wolai: ["ts", "tishi"], cn: ["提示块", "警告块"],
      insert: "> [!note] 提示\n> 内容\n" },
  ];

  function pad2(n) { return (n < 10 ? "0" : "") + n; }
  function stamp(withTime) {
    const d = new Date();
    const day = d.getFullYear() + "-" + pad2(d.getMonth() + 1) + "-" + pad2(d.getDate());
    if (!withTime) return day;
    return day + " " + pad2(d.getHours()) + ":" + pad2(d.getMinutes());
  }

  /* 在 Vditor 实例插入命令文本（右键/斜杠菜单共用） */
  function runCommand(cmd, ed) {
    if (!cmd || !ed) return;
    let text = cmd.insert;
    if (cmd.id === "date") text = stamp(false);
    else if (cmd.id === "time") text = stamp(true);
    if (text) ed.insertValue(text, true);
  }

  /* 按操作风格返回该命令的「展示触发词」：
     notion → 英文/HTML 风格（/h1、/ul、/list）
     wolai  → 拼音缩写（/bt1、/wxlb）
     展示词与可搜索 token 同源，保证「看到什么就能输入什么」。 */
  function displayKey(cmd, style) {
    const arr = style === "wolai" ? cmd.wolai : cmd.notion;
    return "/" + (arr && arr[0] ? arr[0] : cmd.id);
  }

  /* 按当前操作风格返回命令的可搜索 token 集合。
     notion 模式只认英文 token，wolai 模式只认拼音 token；
     中文名两种风格都可搜，提升易用性。 */
  function tokensFor(cmd, style) {
    const base = cmd.cn.slice();
    if (style === "wolai") return base.concat(cmd.wolai);
    return base.concat(cmd.notion);
  }

  /* 过滤命令：query 为去掉前导 "/" 后的小写串 */
  function filterCommands(query, style) {
    const q = (query || "").trim().toLowerCase();
    if (!q) return COMMANDS.slice();
    const score = (cmd) => {
      const toks = tokensFor(cmd, style);
      let best = -1;
      for (const t of toks) {
        const tl = t.toLowerCase();
        if (tl === q) return 3;
        if (tl.startsWith(q)) best = Math.max(best, 2);
        else if (tl.includes(q)) best = Math.max(best, 1);
      }
      return best;
    };
    return COMMANDS.map((c) => [c, score(c)])
      .filter(([, sc]) => sc > 0)
      .sort((a, b) => b[1] - a[1])
      .map(([c]) => c);
  }

  window.COMMANDS = COMMANDS;
  window.filterCommands = filterCommands;
  window.runCommand = runCommand;
  window.displayKey = displayKey;
})();
