# RyuuMD AI 指南

本文面向 AI 代理（Claude Code、Codex 及任何 MCP 客户端）：如何经 CLI 与 MCP 全自动使用 RyuuMD 的全部可自动化能力。RyuuMD 是本地 Markdown 笔记应用（仓库管理、即时渲染编辑、双链、模板、日记、AI 助手、可选 WebDAV 云同步）。

**4 只工具，两个入口。** 对外只列 `ryuumd_note`（单篇笔记与目录）、`ryuumd_vault`（仓库级检索、索引与组织）、`ryuumd_sync`（云同步与工作副本）、`ryuumd_im`（发到即时通讯）四只工具，用 `action` 参数选操作，整套 schema 约 3.9K 字符；MCP 与 CLI 的 `tool` 命令调用同一张表，行为完全一致。

**触发约定：只在用户提到 RyuuMD，或明确要求使用 RyuuMD 工具时才调用。** 任务即使涉及 Markdown、笔记或本地文件，只要用户没提 RyuuMD，就用代理自带的文件工具处理。MCP 服务会在 `initialize` 的 `instructions` 和每只工具描述开头声明这一约束。

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
RyuuMD tools                                   # 列出 4 只工具（JSON 数组，含 input_schema，与 MCP tools/list 同表）
RyuuMD tool <name> [--args '<json>']           # 调用工具（分组工具需带 action），输出结果 JSON
echo '{"action":"read","path":"a.md"}' | RyuuMD tool ryuumd_note  # --args 缺省时读 stdin；均无则按 {} 调用
```

退出码：`0` 成功（返回 `ok: true`）；`1` 工具执行失败（`ok: false`，读 `error` 字段）；`2` 用法错误（缺工具名 / `--args` 不是合法 JSON 对象）。

另有高频具名命令（人用友好，语义与同名工具一致）：`read` `write` `ls` `search` `vaults` `scratch` `send` `receive`。**AI 请一律用 `tool` 具名调用**——参数显式、返回 JSON、与 MCP 对齐。

## 调用约定

- 返回值恒为 JSON 对象，含 `ok: bool`；失败时另有 `error: string`。MCP 边界上 `ok:false` 映射为 `isError:true`。
- 写入类操作（`ryuumd_note.write` / `ryuumd_note.create` / `ryuumd_vault.capture` 等）原子写盘，崩溃不留半截文件。
- `ryuumd_note.write` 是**整文覆盖**；局部修改请读后改、再整体写回，写前可用 `ryuumd_note.stat` 核对 mtime 防覆盖他人改动。
- `ryuumd_note.delete` 移入系统回收站（可恢复），不是物理删除。
- 多数仓库级操作的 `folder` 参数缺省时按「当前仓库」解析（上次打开的目录/文件推断）；自动化场景**始终显式传 `folder`**，避免依赖会话状态。
- `ryuumd_note.read` 会把目标记为「当前文件/最近打开」；传 `max_chars` 则只读开头、无副作用。
- 文中 `ryuumd_note.read` 表示调用 `ryuumd_note` 工具并传 `"action": "read"`；缺 action 或 action 不存在时返回错误并列出可用 action。

## 工具总表

`?` 为可选参数；完整 schema 以 `RyuuMD tools` 输出为准。「原工具名」是合并前的细粒度名，仍可直接调用。

### `ryuumd_note`：单篇 Markdown 笔记与目录

| action | 作用 | 参数 | 原工具名 |
| --- | --- | --- | --- |
| `read` | 读正文；给 max_chars 则只读开头且不记入最近打开 | `path`, `max_chars?` | `read_note` / `preview_note` |
| `write` | 整文覆盖写入 | `path`, `content` | `write_note` |
| `create` | 在 folder 下新建空笔记 | `folder`, `name` | `new_note` |
| `list` | 列目录下的笔记与子文件夹 | `path` | `list_notes` |
| `stat` | 存在性/大小/mtime，写前防冲突 | `path` | `note_stat` |
| `rename` | 同目录改名 | `path`, `new_name` | `rename_note` |
| `move` | 移到目标目录 | `path`, `target_folder` | `move_note` |
| `delete` | 移入回收站 | `path` | `delete_note` |
| `image` | base64 图片存到附件目录（默认 image/），返回相对路径 | `data_b64`, `md_path?`, `folder?`, `filename?`, `mime?` | `save_image` |
| `scratch` | 读随手记；给 content 则覆盖写入 | `content?` | `read_scratch` / `write_scratch` |

### `ryuumd_vault`：仓库级检索、索引与组织（folder 缺省为当前仓库）

| action | 作用 | 参数 | 原工具名 |
| --- | --- | --- | --- |
| `list` | 列已注册仓库 | — | `list_vaults` |
| `add` | 把已有目录注册为仓库 | `path`, `name?` | `add_vault` |
| `create` | 新建目录并注册为仓库 | `parent`, `name` | `create_vault` |
| `mkdir` | 仓库内新建子文件夹 | `parent`, `name` | `new_folder` |
| `files` | 平铺列全部笔记，可按 query 过滤 | `folder?`, `query?` | `list_all_notes` |
| `search` | 检索标题/正文/语义 | `query`, `folder?`, `mode?` | `smart_search` |
| `index` | 待办/标签/断链/孤立笔记/未链接提及 | `folder?`, `kind?`, `query?`, `path?` | `vault_index` |
| `stats` | 笔记数、字数等统计 | `folder?` | `vault_stats` |
| `backlinks` | 谁链向 path | `path`, `folder?` | `find_backlinks` |
| `resolve_link` | [[双链]] 名解析为路径 | `name`, `folder?`, `current_file?` | `resolve_wikilink` |
| `templates` | 列「模板/」下的模板 | `folder?` | `list_templates` |
| `from_template` | 从模板新建笔记 | `template_path`, `name`, `folder?` | `new_from_template` |
| `daily` | 打开或创建今日日记 | `folder?`, `subfolder?` | `daily_note` |
| `capture` | 带时间戳追加到收集箱 | `text`, `folder?`, `name?` | `capture` |

### `ryuumd_sync`：WebDAV 云同步与工作副本（工作副本仅 edit_mode=workdir 时可用）

| action | 作用 | 参数 | 原工具名 |
| --- | --- | --- | --- |
| `status` | 云同步配置与最近结果 | — | `cloud_status` |
| `cloud` | 立即云同步，缺省全部启用仓库 | `project_id?` | `sync_cloud` |
| `diff` | 工作副本与源目录差异汇总 | `folder?` | `workdir_summary` |
| `file` | 单文件相对源文件的状态 | `path?` | `workdir_status` |
| `push` | 单文件写回源 | `path?`, `force?` | `workdir_push` |
| `push_all` | 整仓写回源 | `folder?`, `force?` | `workdir_push_all` |
| `merge` | 源文件合并进单文件副本 | `path?`, `strategy?` | `workdir_merge` |
| `merge_all` | 源目录全部合并进副本 | `folder?`, `strategy?` | `workdir_merge_all` |

### `ryuumd_im`：Markdown 发到或取自飞书/POPO/钉钉/企业微信/微信/QQ

| action | 作用 | 参数 | 原工具名 |
| --- | --- | --- | --- |
| `send` | 发送 | `provider`, `markdown`, `title?` | `send_to_im` |
| `receive` | 取回最近一封 | `provider` | `receive_from_im` |

### 未列出但仍可调用

以下操作偏 GUI 设置或代理自身就能完成（如 AI 概括），不进工具列表以节省上下文，但仍可按原名直接调用，参数见 `docs/DEV_GUIDE.md` 或源码 `TOOL_SPECS`：

`ask_ai`、`clear_recent`、`configure_cloud`、`duplicate_note`、`get_config`、`knowledge_tree`、`list_im_providers`、`list_recent`、`make_dir`、`open_tutorial`、`pin_vault`、`remove_recent`、`remove_vault`、`rename_vault`、`search_notes`、`set_vault_cloud`、`summarize_note`、`summarize_vault`、`test_ai`、`test_cloud`、`update_config`。

## 典型工作流（CLI 示例，MCP 同名同参）

### 从零建一个仓库并写入第一篇笔记

```bash
RyuuMD tool ryuumd_vault --args '{"action":"create","parent":"D:/notes","name":"research"}'
RyuuMD tool ryuumd_note --args '{"action":"create","folder":"D:/notes/research","name":"reading-list"}'
RyuuMD tool ryuumd_note --args '{"action":"write","path":"D:/notes/research/reading-list.md","content":"# 阅读清单\n\n- [ ] 《深入理解计算机系统》\n"}'
```

### 盘点仓库：搜索、待办、统计

```bash
RyuuMD tool ryuumd_vault  --args '{"action":"search","folder":"D:/notes/research","query":"操作系统","mode":"content"}'
RyuuMD tool ryuumd_vault  --args '{"action":"index","folder":"D:/notes/research","kind":"tasks"}'
RyuuMD tool ryuumd_vault  --args '{"action":"stats","folder":"D:/notes/research"}'
```

### 每日记录：日记 + 快速收集

```bash
RyuuMD tool ryuumd_vault --args '{"action":"daily","folder":"D:/notes/research"}'
RyuuMD tool ryuumd_vault --args '{"action":"capture","folder":"D:/notes/research","text":"灵感：把索引做成每日快照"}'
```

### 整理：移动、重命名、双链体检

```bash
RyuuMD tool ryuumd_note   --args '{"action":"move","path":"D:/notes/research/tmp.md","target_folder":"D:/notes/research/archive"}'
RyuuMD tool ryuumd_vault  --args '{"action":"index","folder":"D:/notes/research","kind":"broken"}'
RyuuMD tool ryuumd_vault  --args '{"action":"backlinks","folder":"D:/notes/research","path":"D:/notes/research/reading-list.md"}'
```

### 插图（AI 生图/截图落盘）

```bash
RyuuMD tool ryuumd_note --args '{"action":"image","md_path":"D:/notes/research/reading-list.md","filename":"cover.png","data_b64":"<base64>"}'
# 返回 {"ok":true,"rel":"image/pasted-....png"}，正文引用 ![](image/pasted-....png)
```

## 行为边界（这些只能 GUI 做）

以下能力依赖窗口或系统对话框，不在工具表内，AI 无法也不应尝试经 CLI/MCP 触发：

- 系统文件/文件夹对话框（打开、另存、移动对话框）
- 窗口生命周期：新开窗口、窗口内切换仓库、恢复上次会话
- 「设为默认 Markdown 应用」（系统设置对话框）
- 导出独立 HTML（渲染管线在前端 Vditor；替代：`ryuumd_note.read` 取 Markdown 自行渲染）
- 在资源管理器中显示、用默认浏览器打开链接
- 编辑器内交互（斜杠菜单、段落转换、块级右键操作）——这些是渲染层操作，对磁盘文件用 `ryuumd_note.write` 等价达成

## 兼容

- 旧前缀 `--cli` / `--tui` / `--mcp` 与旧命令名（`list` / `im-send` / `im-receive` / `scratch-read` / `scratch-write`）仍可用。
- 合并前的 57 只细粒度工具名（`read_note`、`smart_search`、`daily_note` 等）仍可直接调用，参数不变，只是不再出现在工具列表里；对应关系见上方工具总表。
- 更早的旧 MCP 工具名自动映射：`read_file`→`read_note`、`write_file`→`write_note`、`list_folder`→`list_notes`、`search_vault`→`search_notes`、`list_projects`→`list_vaults`、`im_send`→`send_to_im`、`im_receive`→`receive_from_im`。
- Windows 打包版是无控制台窗口程序：终端直接运行时输出可能出现在提示符之后；管道、重定向与 MCP 接入场景不受影响。

## 相关文档

- `README.md`：产品功能与安装
- `docs/USER_GUIDE.md`：用户使用手册
- `docs/DEV_GUIDE.md`：架构与模块职责
- `docs/MAINTENANCE.md`：数据目录与排障
