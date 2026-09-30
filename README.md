# RyuuMD 墨读

轻量、全本地、极速的 Markdown 编辑与阅读工具。像编辑 HTML 一样便利地写、读 Markdown；
以仓库形式管理笔记目录，首页一键切换，支持多窗口。

## 特性

- **内置学习仓库**：首次启动自动注册「学习仓库」到首页 —— 交互式教程（6 章边玩边学）+
  `example.md` 全功能渲染大观园；副本位于用户数据目录，随意涂改不影响内置原件，
  随时可从首页快速卡或设置 → 通用重新打开。
- **即时渲染**：基于 Vditor IR 模式，所见即所得，接近 Typora 体验；渲染/源码切换保持阅读位置不跳。
- **软件首页**：启动即见的工作台 —— 问候语、快速操作、仓库列表（**卡片 / 列表双视图**）、
  最近打开（**最近 3 个文件单独成卡**，文件夹等归入「其他」分组）；在各笔记目录间一键切换。
- **仓库管理**（参考 Obsidian vault）：把任意目录保存为仓库，支持置顶、重命名、
  新窗口打开、在资源管理器中显示、移除（不动磁盘文件）；最近打开的文件夹可一键「存为仓库」。
- **多窗口**：工具栏「新窗口」按钮、仓库卡片「新窗口打开」，不同笔记库并排写作。
- **单实例秒开**：已运行时双击其他 md 文件，自动在已有实例中开新窗口，免冷启动等待；
  再次启动程序的行为可选「新开窗口 / 激活当前窗口」（设置 → 通用）。
- **一键默认应用**：设置 → 「默认 Markdown 应用」，弹系统对话框勾选「始终」即可
  （Win10/11 合规方式，无需管理员权限）。
- **AI 助手**：设置里填写 Anthropic Messages 的 Base URL / API Key / 模型；右侧 AI 侧栏统一承载提问、概括当前文档 / 仓库与「知识谱系」生成 / 刷新；搜索支持标题 / 内容即时搜，语义与 `ask ` 前缀需按 Enter 才请求。
- **可选云同步**：官方不提供云端。在设置中填写自己的 WebDAV（坚果云 / Nextcloud / 群晖 / AList 等），
  勾选「启用云同步」后生效；可按仓库开启，或勾选「同步全部仓库」。笔记仍保存在本地。
- **快速打开 / 命令面板 / 仓库搜索**：`Ctrl+P` 打开当前仓库笔记，`Ctrl+Shift+P` 运行命令，
  `Ctrl+Shift+F` 按标题 / 内容 / 语义搜索，也可用 `ask ` 前缀提问。
- **粘贴即图**：粘贴或拖入图片落到 `{文件名}.assets/`（Typora 同款），正文插入相对路径并正确显示。
- **双向链接**：`[[笔记名]]`，Ctrl+单击或点击已高亮链接即可跳转；键入 `[[` 可过滤仓库笔记；侧栏「链接」页显示反向链接与链出。
- **每日笔记 / 自动保存 / 导出 HTML**：`Ctrl+Shift+D` 打开今日日记；已保存文档停止输入后自动写盘；命令面板可导出独立 HTML。
- **斜杠命令菜单**：按 `/` 唤起，上下箭头选择，`Tab`/`Enter` 插入（Notion 风格）；
  编辑区右键唤起同一面板，顶部带**剪贴板组**（复制/剪切/粘贴，无选区时前两项置灰）。
  支持 1~6 级标题、图片、脚注、链接、分割线、表格、代码块、公式块、行内公式、内容目录、引用、加粗、斜体、有序/无序/待办列表。
- **段落转换（对标 Typora 段落菜单）**：框选或将光标停在段落上，右键 →「转换为」，
  可切换为 正文 / 1~6 级标题 / 无序·有序·待办列表 / 引用 / 代码块；跨段落框选逐段转换，行内格式保留。
- **块级右键操作**：光标在表格中——向上/下插入行、向左/右插入列、删除行/列、删除表格；
  光标在代码块中——复制代码、转换为普通文本、删除代码块；
  光标在公式块中——复制公式源码、转换为普通文本、删除公式块。
- **图表（mermaid）**：`流程图 / 时序图 / 甘特图 / 类图 / 状态图 / 饼图 / ER 图 / 用户旅程图 / 思维导图 / 时间线`
  一键插入模板，渲染模式即时成图，随明暗主题自动重渲染。
- **数学公式**：行内 `$…$` 与 `$$…$$` 块即时渲染；公式引擎可选 KaTeX（默认，快）或
  MathJax（Typora 同款，兼容更多 LaTeX 宏），设置 → 通用 中切换。
- **双操作风格**，设置中随时切换，首次启动弹窗选择：
  - **Typora + Notion**（默认）：英文 / 符号触发，如 `/h1`、`#`、`/table`。
  - **Typora + Wolai**：拼音缩写触发，如 `/bt1`、`/dmk`、`/lb`、`/wxlb`。
- **RyuujiDesign 配色**：A 语言五板（霜靛 / 藤色 / 柳染 / 水浅葱 / 樱花），每板自带明暗双态，明暗对切自动换面。
- **目录 / 大纲 / 最近 / 链接侧栏**：浏览文件夹中的 md，文档大纲实时生成、点击跳转；最近打开列表快速回访；链接页展示反向链接。
- **文件树管理**：右键文件可重命名、移动、在资源管理器中显示、删除（回收站）；
  右键文件夹可新建笔记 / 文件夹。`Ctrl+P` 输入新名也可直接新建。
- **编辑区缩放**：`Ctrl+=` / `Ctrl+-` / `Ctrl+0`，状态栏显示当前比例。
- **查找替换**：`Ctrl+F` 本文查找，`Ctrl+H` 展开替换（支持全部替换）。
- **笔记模板**：仓库下建 `模板/` 放入 `.md`，命令面板可插入或从模板新建；支持 `{{title}}` `{{date}}` 等占位符；今日日记优先使用 `模板/日记.md`。
- **复制与定位**：命令面板可复制路径 / 双链 / HTML / Markdown，复制当前笔记，在目录树中定位（也可点状态栏路径）。
- **仓库索引**：命令面板「仓库待办 / 浏览标签 / 断开的双链 / 孤立笔记 / 未链接提及 / 仓库统计」；外部改盘上的已保存文件会自动重新载入（有未保存更改时不覆盖）。
- **可读宽度 / 转到行 / 快速收集**：命令面板可开关 Typora 式可读宽度；`Ctrl+G` 跳到指定行；「快速收集」把一段文字追加到仓库根的 `收集箱.md`；可折叠/展开全部目录。
- **拖拽即开**：拖入 `.md` 文件或文件夹即可打开。
- **启动即主页**：开机回到工作台，最近文件一触即达（可在设置改为「恢复上次会话」）。
- **本地优先**：编辑器资源全内置，默认同步关闭、无需联网；云同步为可选项。

## 运行

```bash
pip install -r requirements.txt
python main.py
# 或双击 run.bat
```

也支持把 `.md` 文件或文件夹拖到 exe 图标上，或用「打开方式」启动；
设为默认应用后双击 md 文件直接打开（已运行时秒开新窗口）。

## 打包

一键打包（自动结束运行中的 RyuuMD + 装依赖 + 单测门禁 + 清理 + 构建；单测不过会中止），两种模式：

```bash
build.bat                    REM 单文件夹 onedir（默认，启动快）
build.bat onefile            REM 单文件 onefile（便携分发）
build.bat onefile nopause    REM 脚本/CI 调用：结尾不等按键（或设 RYUUMD_NOPAUSE=1）
```

或手动：

```bash
python -m pip install -r requirements.txt pyinstaller
python -m PyInstaller --noconfirm --clean RyuuMD.spec            # onedir
python -m PyInstaller --noconfirm --clean RyuuMD-onefile.spec    # onefile
```

| 模式 | 产物 | 体积 | 启动 | 适用 |
| --- | --- | --- | --- | --- |
| onefile | `dist/RyuuMD.exe`（单个文件） | 约 42 MB | 略慢（启动时解压到临时目录） | 单文件便携分发 |
| onedir（`build.bat` 默认） | `dist/RyuuMD/`（含 `RyuuMD.exe` + `_internal/`） | 约 85 MB | 快 | 整目录拷贝分发 |

两种产物互不覆盖，可同时存在于 `dist/`。

**关于杀软误报**：spec 已关闭 UPX 压缩并嵌入版本信息资源（`version_info.txt`），
可显著降低 Microsoft Defender 启发式误报。若个别环境仍误报（云端声誉机制，
代码层面无法彻底避免），可向微软提交误报：
<https://www.microsoft.com/en-us/wdsi/filesubmission>。

### macOS（Apple Silicon，未公证）

本机是 Windows，Mac 包由 GitHub Actions 的 `macos-latest` runner 构建：

- 推送 `v*` tag，或在 Actions 里手动跑 **macOS 打包**；
- 产物 `RyuuMD-mac-arm64.tar.gz`（推荐）与 `.zip`（`.app` + 说明 + `打开 RyuuMD.command`）；
  须在 Mac 上解压，不要在 Windows 解压后再拷过去；
- **未签名、未公证**。首次打开：右键 `RyuuMD.app` → 打开；或双击 `打开 RyuuMD.command`；
  或终端 `xattr -cr RyuuMD.app`。
- 仅 Apple Silicon（M 系列）。Intel Mac 不支持。
- 配置目录：`~/Library/Application Support/RyuuMD`。

有 Mac 本机时也可：

```bash
bash build-mac.sh
```

## 命令行与 MCP

无参数时启动 GUI；首参数是命令名时走命令行（源码运行把 `RyuuMD` 换成 `python main.py`）：

```bash
RyuuMD read <path>                              # 读取笔记正文
RyuuMD write <path> [--content TEXT]            # 写入笔记（无 --content 时读 stdin）
RyuuMD ls <path>                                # 列出目录下的笔记与子文件夹
RyuuMD search <query> [--folder PATH]           # 仓库内搜索
RyuuMD vaults                                   # 列出已注册仓库
RyuuMD scratch [TEXT]                           # 随手记：无内容时读取，有内容时写入
RyuuMD send <feishu|popo|dingtalk|wecom|wechat|qq> [--content TEXT]   # 发到 IM 通道
RyuuMD receive <channel>                        # 取回最近一封发出的 Markdown
```

上面是高频具名命令；**全部可自动化能力**以工具表形式暴露（58 只工具：笔记读写、文件管理、仓库管理、配置、搜索索引、模板、日记、收集箱、图片、AI、云同步、工作副本、IM），经 `tool` / `tools` 命令或 MCP 访问：

```bash
RyuuMD tools                                    # 列出全部工具（JSON，含参数 schema）
RyuuMD tool <name> [--args '<json>']            # 调用工具；--args 缺省时读 stdin
RyuuMD tool daily_note --args '{"folder":"D:/notes"}'
```

入口判定是「命令名优先」：双击、拖拽、文件关联传入的完整路径始终走 GUI；裸词（如 cwd 下名为 `mcp` 的目录）会被当作命令。

**给 AI 代理的完整接入与使用文档见根目录 `AI_GUIDE.md`**（MCP 注册、工具总表、典型工作流、能力边界）。

### 接入 Claude Code（MCP）

```bash
RyuuMD mcp --install          # 一键注册（user 级），重开 Claude Code 会话后生效
RyuuMD mcp --print-config     # 或打印配置手动粘贴
RyuuMD mcp                    # 手动启动 MCP stdio 服务（调试）
```

注册后 Claude Code 可调用整张工具表（read_note / write_note / smart_search / vault_index / daily_note / capture 等 57 只公开工具）。协议兼容 2024-11-05 ~ 2025-06-18，旧工具名（read_file 等）自动映射。

Windows 打包版是无控制台窗口程序：从终端交互运行时输出可能出现在提示符之后；管道、重定向与 Claude Code 接入场景不受影响。旧前缀 `--cli` / `--tui` / `--mcp` 与旧命令名（list / im-send / im-receive / scratch-read / scratch-write）仍兼容。

## 快捷键

Mac 上修饰键为 `⌘`（与下表 `Ctrl` 对应）。

| 操作 | 快捷键 |
| --- | --- |
| 保存 | `Ctrl+S` |
| 打开文件 | `Ctrl+O` |
| 新建 | `Ctrl+N` |
| 快速打开笔记 | `Ctrl+P` |
| 命令面板 | `Ctrl+Shift+P` |
| 仓库全文搜索 | `Ctrl+Shift+F` |
| 本文查找 | `Ctrl+F` |
| 查找替换 | `Ctrl+H` |
| 转到行 | `Ctrl+G` |
| 今日日记 | `Ctrl+Shift+D` |
| 放大 / 缩小 / 重置 | `Ctrl+=` / `Ctrl+-` / `Ctrl+0` |
| 切换侧栏 | `Ctrl+Shift+B` |
| 首页 | `Ctrl+Shift+H`（`Esc` 关闭） |
| 唤起命令菜单 | `/` |

## 测试

```bash
python run_tests.py
```

先跑 Python API 单元测试，再跑真实窗口端到端测试。打包门禁用 `python run_tests.py --unit`。
端到端测试须真实窗口，**运行前请关闭其他 RyuuMD / WebView2 实例**。
测试计划与手动验证清单见 `docs/TEST_PLAN.md`。

## 文档

- 进度与迭代：`PROGRESS.md`（项目现状、当前迭代、下一步候选、里程碑；单一事实源）
- AI 自动化指南：`AI_GUIDE.md`（面向 AI 代理：CLI/MCP 接入、工具总表、工作流）
- 使用手册：`docs/USER_GUIDE.md`（面向用户：首页/仓库/编辑/默认应用设置）
- 开发手册：`docs/DEV_GUIDE.md`（架构、模块职责、关键机制、调试与打包）
- 维护手册：`docs/MAINTENANCE.md`（数据目录、注册表、排障、发布流程）
- 测试计划：`docs/TEST_PLAN.md`

## 目录结构

```
main.py                 启动入口（多窗口管理、单实例接管、命令行/拖图标打开）
app/core/config.py      配置持久化（%APPDATA%/RyuuMD/config.json）
app/core/api.py         前端 JS API（文件读写/管理、文件夹树、仓库、多窗口、关联、收集箱）
app/core/search.py      仓库索引（搜索 / 双链 / 待办标签 / 未链接提及）
app/core/anthropic.py   Anthropic Messages 客户端与概括 / 语义搜索 / ask
app/core/projects.py    仓库（项目）管理：增删改/置顶/重命名/打开计时
app/core/file_assoc.py  Windows 文件关联（ProgID 注册 + 一键设默认）
app/core/singleton.py   单实例守护（端口+token 转发，新窗口秒开）
app/core/fsutil.py      共享常量、md 计数与回收站删除（api/projects 共用）
app/core/webdav.py      轻量 WebDAV 客户端（标准库，无额外依赖）
app/core/cloud_sync.py  可选云同步引擎（默认关闭，用户自备 WebDAV）
app/core/workdir.py     仓库工作副本
app/core/adv.py         CLI / TUI / MCP / AISkill（一张工具表两个入口：tool 命令与 MCP 同表）
app/core/im.py          即时通讯通道载荷
app/web/index.html      前端外壳（含首页 DOM）
app/web/css/            主题（app / editor / slash / home）
app/web/js/             icons / commands / slash / convert / sidebar / welcome / settings / home / editor / palette / ai_panel / app
app/web/vendor/vditor/  内置 Vditor 运行时（index.min.js + method.min.js + 插件）
```

## 配置说明

配置写入 `%APPDATA%/RyuuMD/config.json`：

- `operation_style`：`notion` | `wolai`
- `theme`：`light` | `dark`（基础明暗）
- `palette`：配色方案，默认 `a1`（a1 霜靛 / a3 藤色 / a4 柳染 / a5 水浅葱 / a6 樱花；每板自带明暗双态。旧 phycat id 与已退役 `a2` 读取时自动映射）
- `font_ui` / `font_mono`：正文字体/等宽字体，空 = 默认（外壳固定 Noto Sans SC，正文霞鹜文楷 / Cascadia Code）
- `math_engine`：`katex`（默认，快）| `mathjax`（Typora 同款，兼容更多宏）
- `display_mode`：`ir`（渲染）| `sv`（源码）
- `startup_page`：`home`（始终首页，默认）| `restore`（恢复上次会话）
- `home_view`：`card`（卡片）| `list`（列表），首页仓库视图偏好
- `projects`：仓库列表 `[{id, name, path, pinned, cloud_enabled, created_at, last_opened_at}]`
- `last_file` / `last_folder`：会话记忆
- `recent_files`：最近打开（最新在前，去重限长）
- `welcome_shown`：是否已显示首次欢迎窗口
- `window_width` / `window_height`：窗口尺寸（关闭时记忆）
- `cloud_sync`：可选 WebDAV 同步（默认全关）。`enabled` 须勾选才生效；密码仅存本机，接口不回传明文。

> 赞助二维码：将图片放到 `app/web/assets/QRCode.png` 即会在欢迎窗口显示，否则显示占位文字。
> 欢迎窗口首次启动自动弹出，之后可从「设置 → 欢迎页」随时重开。
