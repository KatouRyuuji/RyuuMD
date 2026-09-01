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
    { id: "formula", icon: "formula", title: "公式块", desc: "KaTeX / MathJax 数学公式", group: "块",
      keys: "/gsk · $$", notion: ["formula", "math", "katex", "latex"], wolai: ["gsk", "gs", "gongshi"], cn: ["公式块", "公式", "数学"],
      insert: "$$\n\n$$\n" },
    { id: "formula-inline", icon: "formula", title: "行内公式", desc: "行内数学公式 $…$", group: "块",
      keys: "/xngs · $", notion: ["inlinemath", "mathinline", "tex"], wolai: ["xngs", "hngs"], cn: ["行内公式", "行内数学"],
      insert: "$公式$" },
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

    // —— 图表（mermaid 模板，对标 Typora 的图表渲染能力）——
    { id: "mmd-flow", icon: "diagram", title: "流程图", desc: "mermaid 流程图（flowchart）", group: "图表",
      keys: "/lct", notion: ["flowchart", "flow", "mermaid"], wolai: ["lct", "liucheng"], cn: ["流程图", "图表"],
      insert: "```mermaid\nflowchart TD\n    A[开始] --> B{条件判断}\n    B -->|是| C[处理]\n    B -->|否| D[结束]\n```\n" },
    { id: "mmd-seq", icon: "diagram", title: "时序图", desc: "mermaid 时序图（sequence）", group: "图表",
      keys: "/sxt", notion: ["sequence", "sequencediagram"], wolai: ["sxt", "shixu"], cn: ["时序图", "顺序图"],
      insert: "```mermaid\nsequenceDiagram\n    用户->>应用: 发起请求\n    应用-->>用户: 返回结果\n```\n" },
    { id: "mmd-gantt", icon: "diagram", title: "甘特图", desc: "mermaid 甘特图（gantt）", group: "图表",
      keys: "/gtt", notion: ["gantt"], wolai: ["gtt", "gante"], cn: ["甘特图"],
      insert: "```mermaid\ngantt\n    title 项目计划\n    dateFormat YYYY-MM-DD\n    section 阶段一\n    任务 A: 2026-01-01, 7d\n    任务 B: 2026-01-08, 5d\n```\n" },
    { id: "mmd-class", icon: "diagram", title: "类图", desc: "mermaid 类图（class）", group: "图表",
      keys: "/lt", notion: ["classdiagram", "class", "uml"], wolai: ["lt", "leitu"], cn: ["类图"],
      insert: "```mermaid\nclassDiagram\n    class 动物 {\n        +名称\n        +叫()\n    }\n    猫 --|> 动物 : 继承\n```\n" },
    { id: "mmd-state", icon: "diagram", title: "状态图", desc: "mermaid 状态图（state）", group: "图表",
      keys: "/ztt", notion: ["state", "statediagram"], wolai: ["ztt", "zhuangtai"], cn: ["状态图"],
      insert: "```mermaid\nstateDiagram-v2\n    [*] --> 待机\n    待机 --> 运行: 启动\n    运行 --> [*]: 停止\n```\n" },
    { id: "mmd-pie", icon: "pie", title: "饼图", desc: "mermaid 饼图（pie）", group: "图表",
      keys: "/bt", notion: ["pie", "piechart"], wolai: ["bt", "bingtu"], cn: ["饼图"],
      insert: "```mermaid\npie title 时间分配\n    \"工作\" : 8\n    \"睡眠\" : 8\n    \"其他\" : 8\n```\n" },
    { id: "mmd-er", icon: "diagram", title: "ER 图", desc: "mermaid 实体关系图（erDiagram）", group: "图表",
      keys: "/ert", notion: ["er", "erdiagram", "entity"], wolai: ["ert", "shiti"], cn: ["ER图", "实体关系图"],
      insert: "```mermaid\nerDiagram\n    用户 ||--o{ 订单 : 拥有\n    订单 ||--|{ 商品 : 包含\n```\n" },
    { id: "mmd-journey", icon: "diagram", title: "用户旅程图", desc: "mermaid 用户旅程（journey）", group: "图表",
      keys: "/yhlct", notion: ["journey", "userjourney"], wolai: ["yhlc", "lvcheng"], cn: ["用户旅程图", "旅程图"],
      insert: "```mermaid\njourney\n    title 我的一天\n    section 上午\n      写作: 5: 我\n      阅读: 3: 我\n```\n" },
    { id: "mmd-mindmap", icon: "diagram", title: "思维导图", desc: "mermaid 思维导图（mindmap）", group: "图表",
      keys: "/swdt", notion: ["mindmap", "mind"], wolai: ["swdt", "daotu"], cn: ["思维导图", "脑图"],
      insert: "```mermaid\nmindmap\n  root((中心主题))\n    分支一\n      子主题\n    分支二\n```\n" },
    { id: "mmd-timeline", icon: "diagram", title: "时间线", desc: "mermaid 时间线（timeline）", group: "图表",
      keys: "/sjx", notion: ["timeline"], wolai: ["sjx", "shijianxian"], cn: ["时间线", "时间轴"],
      insert: "```mermaid\ntimeline\n    title 发展历程\n    2024 : 起点\n    2025 : 成长\n```\n" },
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
