# RyuuMD AI 指南

本文面向 AI 代理（Claude Code、Codex 及任何 MCP 客户端）：如何经 CLI 与 MCP 全自动使用 RyuuMD 的全部可自动化能力。RyuuMD 是本地 Markdown 笔记应用（仓库管理、即时渲染编辑、双链、模板、日记、AI 助手、可选 WebDAV 云同步）。

**10 只分组工具，两个入口。** 能力按领域合并为 `note` `folder` `search` `vault` `journal` `ai` `cloud` `workdir` `im` `app` 十只工具，用 `action` 参数选择具体操作，覆盖笔记读写、文件管理、仓库管理、配置、最近打开、搜索与索引、模板、日记、收集箱、图片、AI、云同步、工作副本与 IM 通道；MCP 与 CLI 的 `tool` 命令调用同一张表，行为完全一致。

## 入口一：MCP（首选）

```bash
RyuuMD mcp --install          # 一键注册到 Claude Code（user 级），重开会话后生效
RyuuMD mcp --print-config     # 打印可粘贴配置（其他 MCP 客户端用）
RyuuMD mcp                    # stdio 服务本体（客户端按上条配置自动拉起，勿手动长驻）
```

源码运行时把 `RyuuMD` 换成 `python main.py`。`--print-config` 输出形如：

```json
{"mcpServers": {"ryuumd": {"type": "stdio", "command": "...", "args": ["...","mcp"]}}}
```

协议兼容 2024-11-05 / 2025-03-26 / 2025-06-18；合并前的细粒度工具名与更早的旧名（`read_file` 等）仍可调用，见文末「兼容」。

## 入口二：CLI

```bash
RyuuMD tools                                   # 列出 10 只分组工具（JSON 数组，含 input_schema，与 MCP tools/list 同表）
RyuuMD tool <name> [--args '<json>']           # 调用工具（分组工具需带 action），输出结果 JSON
echo '{"action":"read","path":"a.md"}' | RyuuMD tool note  # --args 缺省时读 stdin；均无则按 {} 调用
```

退出码：`0` 成功（返回 `ok: true`）；`1` 工具执行失败（`ok: false`，读 `error` 字段）；`2` 用法错误（缺工具名 / `--args` 不是合法 JSON 对象）。

另有高频具名命令（人用友好，语义与同名工具一致）：`read` `write` `ls` `search` `vaults` `scratch` `send` `receive`。**AI 请一律用 `tool` 具名调用**——参数显式、返回 JSON、与 MCP 对齐。

## 调用约定

- 返回值恒为 JSON 对象，含 `ok: bool`；失败时另有 `error: string`。MCP 边界上 `ok:false` 映射为 `isError:true`。
- 写入类操作（`note.write` / `note.create` / `journal.inbox` 等）原子写盘，崩溃不留半截文件。
- `note.write` 是**整文覆盖**；局部修改请读后改、再整体写回，写前可用 `note.stat` 核对 mtime 防覆盖他人改动。
- `note.delete` 移入系统回收站（可恢复），不是物理删除。
- 多数仓库级操作的 `folder` 参数缺省时按「当前仓库」解析（上次打开的目录/文件推断）；自动化场景**始终显式传 `folder`**，避免依赖会话状态。
- `note.read` 会把目标记为「当前文件/最近打开」；纯速览用 `note.preview`（无副作用、有界读取）。
- 文中 `note.read` 表示调用 `note` 工具并传 `"action": "read"`；缺 action 或 action 不存在时返回错误并列出可用 action。

## 工具总表

按领域分 10 只工具，`action` 选操作；`?` 为可选参数。完整 schema 以 `RyuuMD tools` 输出为准。「原工具名」列是合并前的细粒度名，仍可直接调用。

### `note`：笔记（单篇 Markdown 笔记的读写与管理）

| action | 作用 | 参数 | 原工具名 |
| --- | --- | --- | --- |
| `read` | 读取 Markdown 笔记正文 | `path` | `read_note` |
| `write` | 写入 Markdown 笔记正文（整文覆盖） | `path`, `content` | `write_note` |
| `preview` | 有界读取笔记开头（速览用，不改最近打开） | `path`, `max_chars?` | `preview_note` |
| `stat` | 查询笔记存在性、大小与修改时间（写前冲突检查用） | `path` | `note_stat` |
| `create` | 在指定目录下新建空 Markdown 笔记（自动补 .md 后缀） | `folder`, `name` | `new_note` |
| `from_template` | 从模板新建笔记（替换 {{title}} {{date}} 等占位符） | `template_path`, `name`, `folder?` | `new_from_template` |
| `rename` | 重命名 Markdown 笔记（仅同目录改名） | `path`, `new_name` | `rename_note` |
| `move` | 把 Markdown 笔记移动到目标目录 | `path`, `target_folder` | `move_note` |
| `duplicate` | 在同目录复制一份笔记（自动命名「副本」） | `path` | `duplicate_note` |
| `delete` | 把 Markdown 笔记移入系统回收站（可恢复） | `path` | `delete_note` |
| `save_image` | 把 base64 图片落到笔记旁 {文件名}.assets/ 并返回相对引用路径 | `data_b64`, `md_path?`, `folder?`, `filename?`, `mime?` | `save_image` |

### `folder`：目录（列目录、平铺列笔记、建文件夹、列模板）

| action | 作用 | 参数 | 原工具名 |
| --- | --- | --- | --- |
| `list` | 列出目录下的 Markdown 笔记与子文件夹 | `path` | `list_notes` |
| `list_all` | 平铺列出仓库内全部 Markdown 笔记，可按关键词过滤 | `folder?`, `query?` | `list_all_notes` |
| `create` | 在已打开的仓库目录下新建子文件夹 | `parent`, `name` | `new_folder` |
| `mkdir` | 在任意已存在的父目录下新建文件夹（不注册为仓库） | `parent`, `name` | `make_dir` |
| `templates` | 列出仓库「模板/」下的笔记模板 | `folder?` | `list_templates` |

### `search`：搜索与双链（关键词 / 多模式检索与 [[双链]] 查询）

| action | 作用 | 参数 | 原工具名 |
| --- | --- | --- | --- |
| `text` | 在仓库内按标题或正文搜索 | `query`, `folder?` | `search_notes` |
| `smart` | 多模式检索：mode=title 标题 / content 正文 / semantic 语义；query 以「ask 」为前缀时走 AI 问答 | `query`, `folder?`, `mode?` | `smart_search` |
| `backlinks` | 查找指定笔记的反向链接（哪些笔记链向它） | `path`, `folder?` | `find_backlinks` |
| `resolve_link` | 解析 [[双链]] 名称到仓库内实际笔记路径 | `name`, `folder?`, `current_file?` | `resolve_wikilink` |

### `vault`：仓库（仓库注册管理、索引与统计）

| action | 作用 | 参数 | 原工具名 |
| --- | --- | --- | --- |
| `list` | 列出已注册仓库 | — | `list_vaults` |
| `index` | 仓库索引：kind=tasks 待办 / tags 标签 / broken 断开的双链 / orphans 孤立笔记 / mentions 未链接提及（需 path） | `folder?`, `kind?`, `query?`, `path?` | `vault_index` |
| `stats` | 仓库统计（笔记数、字数、标签数等） | `folder?` | `vault_stats` |
| `add` | 把已有目录注册为仓库 | `path`, `name?` | `add_vault` |
| `create` | 在父目录下新建文件夹并注册为仓库 | `parent`, `name` | `create_vault` |
| `rename` | 重命名仓库显示名 | `project_id`, `name` | `rename_vault` |
| `pin` | 置顶或取消置顶仓库 | `project_id`, `pinned` | `pin_vault` |
| `remove` | 从首页移除仓库（不动磁盘文件） | `project_id` | `remove_vault` |
| `open_tutorial` | 注册内置学习仓库到首页（幂等，只补缺失文件） | — | `open_tutorial` |

### `journal`：日记与收集（今日日记、收集箱、随手记）

| action | 作用 | 参数 | 原工具名 |
| --- | --- | --- | --- |
| `daily` | 打开（必要时创建）今日日记并返回其内容与路径 | `folder?`, `subfolder?` | `daily_note` |
| `inbox` | 把一段文字追加到仓库根的收集箱（默认「收集箱.md」），带时间戳 | `text`, `folder?`, `name?` | `capture` |
| `scratch_read` | 读取随手记 | — | `read_scratch` |
| `scratch_write` | 写入随手记 | `content` | `write_scratch` |

### `ai`：AI（AI 问答、概括与知识谱系；需先在配置中设置 `ai.base_url` / `api_key` / `model`）

| action | 作用 | 参数 | 原工具名 |
| --- | --- | --- | --- |
| `ask` | 带仓库摘录向 AI 提问；模型可回调本工具表完成读写 | `question`, `folder?` | `ask_ai` |
| `summarize_note` | AI 概括一篇笔记（path 或 content 二选一） | `path?`, `content?`, `folder?` | `summarize_note` |
| `summarize_vault` | AI 概括整个仓库的笔记材料 | `folder?` | `summarize_vault` |
| `knowledge_tree` | 生成仓库知识谱系（按仓库缓存；refresh=true 重新生成） | `folder?`, `refresh?` | `knowledge_tree` |
| `test` | 测试 AI 端点连通性（最小请求验证地址/密钥/模型） | — | `test_ai` |

### `cloud`：云同步（WebDAV 云同步）

| action | 作用 | 参数 | 原工具名 |
| --- | --- | --- | --- |
| `status` | 读取云同步配置状态与最近同步结果 | — | `cloud_status` |
| `sync` | 立即执行一次云同步（空 project_id 同步全部启用仓库） | `project_id?` | `sync_cloud` |
| `set_vault` | 按仓库开关云同步 | `project_id`, `enabled` | `set_vault_cloud` |
| `test` | 测试 WebDAV 连接 | — | `test_cloud` |
| `configure` | 写入 WebDAV 连接设置（url/username/password 等，密码仅存本机） | `values` | `configure_cloud` |

### `workdir`：工作副本（工作副本与源文件的差异、回写与合并；`edit_mode=workdir` 时生效，直改模式调用会明确报错）

| action | 作用 | 参数 | 原工具名 |
| --- | --- | --- | --- |
| `status` | 查询当前文件相对源文件的同步状态（未回写/源有更新/冲突） | `path?` | `workdir_status` |
| `summary` | 汇总工作副本与源目录的差异 | `folder?` | `workdir_summary` |
| `push` | 把工作副本写回源文件（force=true 覆盖源端更新） | `path?`, `force?` | `workdir_push` |
| `merge` | 把源文件合并进工作副本（strategy: keep_source / keep_copy / markers） | `path?`, `strategy?` | `workdir_merge` |
| `push_all` | 把整个仓库的工作副本写回源目录 | `folder?`, `force?` | `workdir_push_all` |
| `merge_all` | 把源目录全部变更合并进工作副本 | `folder?`, `strategy?` | `workdir_merge_all` |

### `im`：即时通讯（把 Markdown 发到飞书/POPO/钉钉/企业微信/微信/QQ 或取回）

| action | 作用 | 参数 | 原工具名 |
| --- | --- | --- | --- |
| `providers` | 列出可用的即时通讯通道（feishu/popo/dingtalk/wecom/wechat/qq） | — | `list_im_providers` |
| `send` | 把 Markdown 发到飞书/POPO/钉钉/企业微信/微信/QQ 通道 | `provider`, `markdown`, `title?` | `send_to_im` |
| `receive` | 从即时通讯通道取回最近一封发出的 Markdown | `provider` | `receive_from_im` |

### `app`：应用设置（应用配置与最近打开）

| action | 作用 | 参数 | 原工具名 |
| --- | --- | --- | --- |
| `get_config` | 读取应用配置（云同步与 AI 密钥已脱敏） | — | `get_config` |
| `update_config` | 合并写入应用配置（如 theme/palette/font_ui/daily_note_folder 等） | `values` | `update_config` |
| `recent` | 列出最近打开的文件与文件夹 | — | `list_recent` |
| `remove_recent` | 从最近打开列表移除一项 | `path` | `remove_recent` |
| `clear_recent` | 清空最近打开列表 | — | `clear_recent` |

## 典型工作流（CLI 示例，MCP 同名同参）

### 从零建一个仓库并写入第一篇笔记

```bash
RyuuMD tool vault --args '{"action":"create","parent":"D:/notes","name":"research"}'
RyuuMD tool note --args '{"action":"create","folder":"D:/notes/research","name":"reading-list"}'
RyuuMD tool note --args '{"action":"write","path":"D:/notes/research/reading-list.md","content":"# 阅读清单\n\n- [ ] 《深入理解计算机系统》\n"}'
```

### 盘点仓库：搜索、待办、统计

```bash
RyuuMD tool search --args '{"action":"smart","folder":"D:/notes/research","query":"操作系统","mode":"content"}'
RyuuMD tool vault  --args '{"action":"index","folder":"D:/notes/research","kind":"tasks"}'
RyuuMD tool vault  --args '{"action":"stats","folder":"D:/notes/research"}'
```

### 每日记录：日记 + 快速收集

```bash
RyuuMD tool journal --args '{"action":"daily","folder":"D:/notes/research"}'
RyuuMD tool journal --args '{"action":"inbox","folder":"D:/notes/research","text":"灵感：把索引做成每日快照"}'
```

### 整理：移动、重命名、双链体检

```bash
RyuuMD tool note   --args '{"action":"move","path":"D:/notes/research/tmp.md","target_folder":"D:/notes/research/archive"}'
RyuuMD tool vault  --args '{"action":"index","folder":"D:/notes/research","kind":"broken"}'
RyuuMD tool search --args '{"action":"backlinks","folder":"D:/notes/research","path":"D:/notes/research/reading-list.md"}'
```

### 插图（AI 生图/截图落盘）

```bash
RyuuMD tool note --args '{"action":"save_image","md_path":"D:/notes/research/reading-list.md","filename":"cover.png","data_b64":"<base64>"}'
# 返回 {"ok":true,"rel":"reading-list.assets/pasted-....png"}，正文引用 ![](reading-list.assets/pasted-....png)
```

### AI 助理

```bash
RyuuMD tool ai --args '{"action":"ask","folder":"D:/notes/research","question":"这个仓库里关于操作系统的笔记有哪些要点？"}'
RyuuMD tool ai --args '{"action":"summarize_vault","folder":"D:/notes/research"}'
RyuuMD tool ai --args '{"action":"knowledge_tree","folder":"D:/notes/research","refresh":true}'
```
## 行为边界（这些只能 GUI 做）

以下能力依赖窗口或系统对话框，不在工具表内，AI 无法也不应尝试经 CLI/MCP 触发：

- 系统文件/文件夹对话框（打开、另存、移动对话框）
- 窗口生命周期：新开窗口、窗口内切换仓库、恢复上次会话
- 「设为默认 Markdown 应用」（系统设置对话框）
- 导出独立 HTML（渲染管线在前端 Vditor；替代：`note.read` 取 Markdown 自行渲染）
- 在资源管理器中显示、用默认浏览器打开链接
- 编辑器内交互（斜杠菜单、段落转换、块级右键操作）——这些是渲染层操作，对磁盘文件用 `note.write` 等价达成

## 兼容

- 旧前缀 `--cli` / `--tui` / `--mcp` 与旧命令名（`list` / `im-send` / `im-receive` / `scratch-read` / `scratch-write`）仍可用。
- 合并前的 57 只细粒度工具名（`read_note`、`smart_search`、`daily_note` 等）仍可直接调用，参数不变，只是不再出现在工具列表里；对应关系见上方各表。
- 更早的旧 MCP 工具名自动映射：`read_file`→`read_note`、`write_file`→`write_note`、`list_folder`→`list_notes`、`search_vault`→`search_notes`、`list_projects`→`list_vaults`、`im_send`→`send_to_im`、`im_receive`→`receive_from_im`。
- Windows 打包版是无控制台窗口程序：终端直接运行时输出可能出现在提示符之后；管道、重定向与 MCP 接入场景不受影响。

## 相关文档

- `README.md`：产品功能与安装
- `docs/USER_GUIDE.md`：用户使用手册
- `docs/DEV_GUIDE.md`：架构与模块职责
- `docs/MAINTENANCE.md`：数据目录与排障
