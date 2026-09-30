# RyuuMD AI 指南

本文面向 AI 代理（Claude Code、Codex 及任何 MCP 客户端）：如何经 CLI 与 MCP 全自动使用 RyuuMD 的全部可自动化能力。RyuuMD 是本地 Markdown 笔记应用（仓库管理、即时渲染编辑、双链、模板、日记、AI 助手、可选 WebDAV 云同步）。

**一张工具表，两个入口。** 58 只工具覆盖笔记读写、文件管理、仓库管理、配置、最近打开、搜索与索引、模板、日记、收集箱、图片、AI、云同步、工作副本与 IM 通道；MCP 与 CLI 的 `tool` 命令调用同一张表，行为完全一致。

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

协议兼容 2024-11-05 / 2025-03-26 / 2025-06-18；旧工具名（`read_file` 等）自动映射，见文末「兼容」。

## 入口二：CLI

```bash
RyuuMD tools                                   # 列出全部工具（JSON 数组，含 input_schema，与 MCP tools/list 同表）
RyuuMD tool <name> [--args '<json>']           # 调用一只工具，输出结果 JSON
echo '{"path":"a.md"}' | RyuuMD tool read_note # --args 缺省时读 stdin；均无则按 {} 调用
```

退出码：`0` 成功（返回 `ok: true`）；`1` 工具执行失败（`ok: false`，读 `error` 字段）；`2` 用法错误（缺工具名 / `--args` 不是合法 JSON 对象）。

另有高频具名命令（人用友好，语义与同名工具一致）：`read` `write` `ls` `search` `vaults` `scratch` `send` `receive`。**AI 请一律用 `tool` 具名调用**——参数显式、返回 JSON、与 MCP 对齐。

## 调用约定

- 返回值恒为 JSON 对象，含 `ok: bool`；失败时另有 `error: string`。MCP 边界上 `ok:false` 映射为 `isError:true`。
- 写入类工具（`write_note`/`new_note`/`capture` 等）原子写盘，崩溃不留半截文件。
- `write_note` 是**整文覆盖**；局部修改请读后改、再整体写回，写前可用 `note_stat` 核对 mtime 防覆盖他人改动。
- `delete_note` 移入系统回收站（可恢复），不是物理删除。
- 多数仓库级工具的 `folder` 参数缺省时按「当前仓库」解析（上次打开的目录/文件推断）；自动化场景**始终显式传 `folder`**，避免依赖会话状态。
- `read_note` 会把目标记为「当前文件/最近打开」；纯速览用 `preview_note`（无副作用、有界读取）。

## 工具总表

按能力分组。完整参数 schema 以 `RyuuMD tools` 输出为准。

### 笔记读写与文件管理
| 工具 | 作用 | 关键参数 |
| --- | --- | --- |
| `read_note` | 读取笔记正文 | `path` |
| `write_note` | 整文覆盖写入 | `path`, `content` |
| `new_note` | 新建空笔记（自动补 .md） | `folder`, `name` |
| `preview_note` | 有界速览，无副作用 | `path`, `max_chars` |
| `note_stat` | 存在性/大小/mtime | `path` |
| `rename_note` | 同目录改名 | `path`, `new_name` |
| `move_note` | 移动到目标目录 | `path`, `target_folder` |
| `duplicate_note` | 复制副本 | `path` |
| `delete_note` | 移入回收站 | `path` |
| `list_notes` | 目录树（递归，含子文件夹） | `path` |
| `list_all_notes` | 仓库笔记平铺索引，可过滤 | `folder?`, `query?` |
| `new_folder` | 仓库内新建子文件夹 | `parent`, `name` |
| `make_dir` | 任意父目录新建文件夹 | `parent`, `name` |

### 仓库管理
| 工具 | 作用 | 关键参数 |
| --- | --- | --- |
| `list_vaults` | 列出已注册仓库（含 id） | — |
| `add_vault` | 注册已有目录为仓库 | `path`, `name?` |
| `create_vault` | 新建文件夹并注册 | `parent`, `name` |
| `rename_vault` | 改显示名 | `project_id`, `name` |
| `pin_vault` | 置顶/取消 | `project_id`, `pinned` |
| `remove_vault` | 从首页移除（不动磁盘） | `project_id` |
| `open_tutorial` | 注册内置学习仓库（幂等） | — |

### 搜索与仓库索引
| 工具 | 作用 | 关键参数 |
| --- | --- | --- |
| `search_notes` | 标题+正文搜索 | `query`, `folder?` |
| `smart_search` | 多模式：title/content/semantic；`ask ` 前缀走 AI 问答 | `query`, `folder?`, `mode?` |
| `vault_index` | tasks 待办 / tags 标签 / broken 断链 / orphans 孤立 / mentions 未链接提及 | `kind`, `folder?`, `query?`, `path?` |
| `vault_stats` | 仓库统计 | `folder?` |
| `resolve_wikilink` | 解析 [[双链]] 到实际路径 | `name`, `folder?`, `current_file?` |
| `find_backlinks` | 查反向链接 | `path`, `folder?` |

### 模板 / 日记 / 收集箱 / 图片
| 工具 | 作用 | 关键参数 |
| --- | --- | --- |
| `list_templates` | 列出仓库「模板/」下模板 | `folder?` |
| `new_from_template` | 从模板新建（替换 {{title}} {{date}} 等） | `template_path`, `name`, `folder?` |
| `daily_note` | 打开/创建今日日记 | `folder?`, `subfolder?` |
| `capture` | 追加一段文字到收集箱（带时间戳） | `text`, `folder?`, `name?` |
| `save_image` | base64 图片落到 `{笔记}.assets/`，返回相对引用路径 | `data_b64`, `md_path?`, `folder?`, `filename?`, `mime?` |

### 配置与最近打开
| 工具 | 作用 | 关键参数 |
| --- | --- | --- |
| `get_config` | 读配置（密钥已脱敏） | — |
| `update_config` | 合并写配置 | `values` |
| `list_recent` / `remove_recent` / `clear_recent` | 最近打开列表 | `path?` |

### AI（需先在配置中设置 `ai.base_url` / `api_key` / `model`）
| 工具 | 作用 | 关键参数 |
| --- | --- | --- |
| `ask_ai` | 带仓库摘录提问，模型可回调本工具表 | `question`, `folder?` |
| `summarize_note` | 概括一篇笔记 | `path` 或 `content`, `folder?` |
| `summarize_vault` | 概括仓库 | `folder?` |
| `knowledge_tree` | 生成知识谱系（缓存；`refresh` 重建） | `folder?`, `refresh?` |
| `test_ai` | 端点连通性自检 | — |

### 云同步（WebDAV）
| 工具 | 作用 | 关键参数 |
| --- | --- | --- |
| `configure_cloud` | 写连接设置（密码仅存本机） | `values` |
| `cloud_status` | 状态与最近同步结果 | — |
| `test_cloud` | 连通性自检 | — |
| `sync_cloud` | 立即同步（空 id 同步全部启用仓库） | `project_id?` |
| `set_vault_cloud` | 按仓库开关 | `project_id`, `enabled` |

### 工作副本（`edit_mode=workdir` 时生效；直改模式调用会明确报错）
| 工具 | 作用 | 关键参数 |
| --- | --- | --- |
| `workdir_status` / `workdir_summary` | 同步状态 / 差异汇总 | `path?` / `folder?` |
| `workdir_push` / `workdir_push_all` | 副本写回源（`force` 覆盖冲突） | `path?`/`folder?`, `force?` |
| `workdir_merge` / `workdir_merge_all` | 源合并进副本（strategy: keep_source/keep_copy/markers） | `path?`/`folder?`, `strategy?` |

### IM 通道
| 工具 | 作用 | 关键参数 |
| --- | --- | --- |
| `list_im_providers` | 列出通道 | — |
| `send_to_im` | 发 Markdown 到通道 | `provider`, `markdown`, `title?` |
| `receive_from_im` | 取回最近一封发出的 | `provider` |
| `read_scratch` / `write_scratch` | 首页随手记 | `content?` |

## 典型工作流（CLI 示例，MCP 同名同参）

### 从零建一个仓库并写入第一篇笔记
```bash
RyuuMD tool create_vault --args '{"parent":"D:/notes","name":"research"}'
RyuuMD tool new_note --args '{"folder":"D:/notes/research","name":"reading-list"}'
RyuuMD tool write_note --args '{"path":"D:/notes/research/reading-list.md","content":"# 阅读清单\n\n- [ ] 《深入理解计算机系统》\n"}'
```

### 盘点仓库：搜索、待办、统计
```bash
RyuuMD tool smart_search --args '{"folder":"D:/notes/research","query":"操作系统","mode":"content"}'
RyuuMD tool vault_index  --args '{"folder":"D:/notes/research","kind":"tasks"}'
RyuuMD tool vault_stats  --args '{"folder":"D:/notes/research"}'
```

### 每日记录：日记 + 快速收集
```bash
RyuuMD tool daily_note --args '{"folder":"D:/notes/research"}'
RyuuMD tool capture    --args '{"folder":"D:/notes/research","text":"灵感：把索引做成每日快照"}'
```

### 整理：移动、重命名、双链体检
```bash
RyuuMD tool move_note      --args '{"path":"D:/notes/research/tmp.md","target_folder":"D:/notes/research/archive"}'
RyuuMD tool vault_index    --args '{"folder":"D:/notes/research","kind":"broken"}'
RyuuMD tool find_backlinks --args '{"folder":"D:/notes/research","path":"D:/notes/research/reading-list.md"}'
```

### 插图（AI 生图/截图落盘）
```bash
RyuuMD tool save_image --args '{"md_path":"D:/notes/research/reading-list.md","filename":"cover.png","data_b64":"<base64>"}'
# 返回 {"ok":true,"rel":"reading-list.assets/pasted-....png"}，正文引用 ![](reading-list.assets/pasted-....png)
```

### AI 助理
```bash
RyuuMD tool ask_ai          --args '{"folder":"D:/notes/research","question":"这个仓库里关于操作系统的笔记有哪些要点？"}'
RyuuMD tool summarize_vault --args '{"folder":"D:/notes/research"}'
RyuuMD tool knowledge_tree  --args '{"folder":"D:/notes/research","refresh":true}'
```

## 行为边界（这些只能 GUI 做）

以下能力依赖窗口或系统对话框，不在工具表内，AI 无法也不应尝试经 CLI/MCP 触发：

- 系统文件/文件夹对话框（打开、另存、移动对话框）
- 窗口生命周期：新开窗口、窗口内切换仓库、恢复上次会话
- 「设为默认 Markdown 应用」（系统设置对话框）
- 导出独立 HTML（渲染管线在前端 Vditor；替代：`read_note` 取 Markdown 自行渲染）
- 在资源管理器中显示、用默认浏览器打开链接
- 编辑器内交互（斜杠菜单、段落转换、块级右键操作）——这些是渲染层操作，对磁盘文件用 `write_note` 等价达成

## 兼容

- 旧前缀 `--cli` / `--tui` / `--mcp` 与旧命令名（`list` / `im-send` / `im-receive` / `scratch-read` / `scratch-write`）仍可用。
- 旧 MCP 工具名自动映射：`read_file`→`read_note`、`write_file`→`write_note`、`list_folder`→`list_notes`、`search_vault`→`search_notes`、`list_projects`→`list_vaults`、`im_send`→`send_to_im`、`im_receive`→`receive_from_im`。
- Windows 打包版是无控制台窗口程序：终端直接运行时输出可能出现在提示符之后；管道、重定向与 MCP 接入场景不受影响。

## 相关文档

- `PROGRESS.md`：项目进度与迭代计划（单一事实源；迭代开始先读，收尾更新）
- `README.md`：产品功能与安装
- `docs/USER_GUIDE.md`：用户使用手册
- `docs/DEV_GUIDE.md`：架构与模块职责
- `docs/MAINTENANCE.md`：数据目录与排障
