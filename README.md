# RyuuMD 墨读

轻量、全本地、极速的 Markdown 编辑与阅读工具。像编辑 HTML 一样便利地写、读 Markdown。

## 特性

- **即时渲染**：基于 Vditor IR 模式，所见即所得，接近 Typora 体验。
- **斜杠命令菜单**：按 `/` 唤起，上下箭头选择，`Tab`/`Enter` 插入（Notion 风格）。
  支持 1~6 级标题、图片、脚注、链接、分割线、表格、代码块、公式块、内容目录、加粗、斜体、有序/无序/待办列表。
- **双操作风格**，设置中随时切换，首次启动弹窗选择：
  - **Typora + Notion**（默认）：英文 / 符号触发，如 `/h1`、`#`、`/table`。
  - **Typora + Wolai**：拼音缩写触发，如 `/bt1`、`/dmk`、`/lb`、`/wxlb`。
- **phycat 配色**：亮色 sky（核心蓝）、暗色 vampire（吸血鬼红），现代扁平。
- **目录 / 大纲 / 最近侧栏**：浏览文件夹中的 md，文档大纲实时生成、点击跳转；最近打开列表快速回访文件与工作区（失效项点击后自动移除，可一键清空）。
- **拖拽即开**：拖入 `.md` 文件或文件夹即可打开。
- **会话记忆**：开启应用自动恢复上次浏览的文件夹与文件。
- **全本地**：无需联网，所有编辑器资源已内置（vendor/vditor）。

## 运行

```bash
pip install -r requirements.txt
python main.py
# 或双击 run.bat
```

也支持把 `.md` 文件或文件夹拖到 exe 图标上，或用「打开方式」启动。

## 打包

一键打包（自动装依赖 + 清理 + 构建），两种模式：

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
| 唤起命令菜单 | `/` |

## 测试

```bash
python run_tests.py
```

先跑 Python API 单元测试（16 项），再跑真实窗口端到端测试（21 项）。
端到端测试须真实窗口，**运行前请关闭其他 RyuuMD / WebView2 实例**。
测试计划、实测结果与手动验证清单见 `docs/TEST_PLAN.md`。

## 目录结构

```
main.py                 启动入口（含命令行/拖图标打开）
app/core/config.py      配置持久化（%APPDATA%/RyuuMD/config.json）
app/core/api.py         前端 JS API（文件读写、文件夹树、对话框）
app/web/index.html      前端外壳
app/web/css/            主题（app / editor / slash）
app/web/js/             icons / commands / slash / sidebar / editor / welcome / settings / app
app/web/vendor/vditor/  内置 Vditor 全量资源
```

## 配置说明

配置写入 `%APPDATA%/RyuuMD/config.json`：

- `operation_style`：`notion` | `wolai`
- `theme`：`light`（sky）| `dark`（vampire）
- `display_mode`：`ir`（渲染）| `sv`（源码）
- `last_file` / `last_folder`：会话记忆
- `welcome_shown`：是否已显示首次欢迎窗口
- `window_width` / `window_height`：窗口尺寸

> 赞助二维码：将图片放到 `app/web/assets/QRCode.png` 即会在欢迎窗口显示，否则显示占位文字。
> 欢迎窗口首次启动自动弹出，之后可从「设置 → 欢迎页」随时重开。
