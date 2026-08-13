# RyuuMD 墨读

轻量、全本地、极速的 Markdown 编辑与阅读工具。像编辑 HTML 一样便利地写、读 Markdown；
以仓库形式管理笔记目录，首页一键切换，支持多窗口。

## 特性

- **即时渲染**：基于 Vditor IR 模式，所见即所得，接近 Typora 体验；渲染/源码切换保持阅读位置不跳。
- **软件首页**：启动即见的工作台 —— 问候语、快速操作、仓库列表（**卡片 / 列表双视图**）、
  最近打开（**最近 3 个文件单独成卡**，文件夹等归入「其他」分组）；在各笔记目录间一键切换。
- **仓库管理**（参考 Obsidian vault）：把任意目录保存为仓库，支持置顶、重命名、
  新窗口打开、在资源管理器中显示、移除（不动磁盘文件）；最近打开的文件夹可一键「存为仓库」。
- **多窗口**：仓库卡片「新窗口打开」，不同笔记库并排写作。
- **单实例秒开**：已运行时双击其他 md 文件，自动在已有实例中开新窗口，免冷启动等待。
- **一键默认应用**：设置 → 「默认 Markdown 应用」，弹系统对话框勾选「始终」即可
  （Win10/11 合规方式，无需管理员权限）。
- **可选云同步**：官方不提供云端。在设置中填写自己的 WebDAV（坚果云 / Nextcloud / 群晖 / AList 等），
  勾选「启用云同步」后生效；可按仓库开启，或勾选「同步全部仓库」。笔记仍保存在本地。
- **斜杠命令菜单**：按 `/` 唤起，上下箭头选择，`Tab`/`Enter` 插入（Notion 风格）；
  编辑区右键唤起同一面板，顶部带**剪贴板组**（复制/剪切/粘贴，无选区时前两项置灰）。
  支持 1~6 级标题、图片、脚注、链接、分割线、表格、代码块、公式块、内容目录、引用、加粗、斜体、有序/无序/待办列表。
- **双操作风格**，设置中随时切换，首次启动弹窗选择：
  - **Typora + Notion**（默认）：英文 / 符号触发，如 `/h1`、`#`、`/table`。
  - **Typora + Wolai**：拼音缩写触发，如 `/bt1`、`/dmk`、`/lb`、`/wxlb`。
- **phycat 配色**：亮色 sky（核心蓝）、暗色 vampire（吸血鬼红），现代扁平。
- **目录 / 大纲 / 最近侧栏**：浏览文件夹中的 md，文档大纲实时生成、点击跳转；最近打开列表快速回访文件与工作区（失效项点击后自动移除，可一键清空）。
- **文件树管理**：侧栏右键文件即可重命名、移动到…、在资源管理器中显示、删除
  （移入回收站，误删可恢复）——直接操作磁盘上的真实文件。
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

一键打包（自动装依赖 + 单测门禁 + 清理 + 构建；单测不过会中止），两种模式：

```bash
build.bat            REM 单文件 onefile（默认）
build.bat onedir     REM 单文件夹 onedir
```

或手动：

```bash
python -m pip install -r requirements.txt pyinstaller
python -m PyInstaller --noconfirm --clean RyuuMD.spec            # onedir
python -m PyInstaller --noconfirm --clean RyuuMD-onefile.spec    # onefile
```

| 模式 | 产物 | 体积 | 启动 | 适用 |
| --- | --- | --- | --- | --- |
| onedir（默认） | `dist/RyuuMD/`（含 `RyuuMD.exe` + `_internal/`） | 约 75 MB | 快 | 整目录拷贝分发 |
| onefile | `dist/RyuuMD.exe`（单个文件） | 约 33 MB | 略慢（启动时解压到临时目录） | 单文件便携分发 |

两种产物互不覆盖，可同时存在于 `dist/`。

**关于杀软误报**：spec 已关闭 UPX 压缩并嵌入版本信息资源（`version_info.txt`），
可显著降低 Microsoft Defender 启发式误报。若个别环境仍误报（云端声誉机制，
代码层面无法彻底避免），可向微软提交误报：
<https://www.microsoft.com/en-us/wdsi/filesubmission>。

## 快捷键

| 操作 | 快捷键 |
| --- | --- |
| 保存 | `Ctrl+S` |
| 打开文件 | `Ctrl+O` |
| 新建 | `Ctrl+N` |
| 切换侧栏 | `Ctrl+Shift+B` |
| 首页 | `Ctrl+Shift+H`（`Esc` 关闭） |
| 唤起命令菜单 | `/` |

## 测试

```bash
python run_tests.py
```

先跑 Python API 单元测试（76 项），再跑真实窗口端到端测试（44 项）。
端到端测试须真实窗口，**运行前请关闭其他 RyuuMD / WebView2 实例**。
测试计划、实测结果与手动验证清单见 `docs/TEST_PLAN.md`。

## 文档

- 使用手册：`docs/USER_GUIDE.md`（面向用户：首页/仓库/编辑/默认应用设置）
- 开发手册：`docs/DEV_GUIDE.md`（架构、模块职责、关键机制、调试与打包）
- 维护手册：`docs/MAINTENANCE.md`（数据目录、注册表、排障、发布流程）
- 测试计划：`docs/TEST_PLAN.md`

## 目录结构

```
main.py                 启动入口（多窗口管理、单实例接管、命令行/拖图标打开）
app/core/config.py      配置持久化（%APPDATA%/RyuuMD/config.json）
app/core/api.py         前端 JS API（文件读写/管理、文件夹树、仓库、多窗口、关联）
app/core/projects.py    仓库（项目）管理：增删改/置顶/重命名/打开计时
app/core/file_assoc.py  Windows 文件关联（ProgID 注册 + 一键设默认）
app/core/singleton.py   单实例守护（端口+token 转发，新窗口秒开）
app/core/fsutil.py      共享常量、md 计数与回收站删除（api/projects 共用）
app/core/webdav.py      轻量 WebDAV 客户端（标准库，无额外依赖）
app/core/cloud_sync.py  可选云同步引擎（默认关闭，用户自备 WebDAV）
app/web/index.html      前端外壳（含首页 DOM）
app/web/css/            主题（app / editor / slash / home）
app/web/js/             icons / commands / slash / sidebar / welcome / settings / home / editor / app
app/web/vendor/vditor/  内置 Vditor 全量资源
```

## 配置说明

配置写入 `%APPDATA%/RyuuMD/config.json`：

- `operation_style`：`notion` | `wolai`
- `theme`：`light`（sky）| `dark`（vampire）
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
