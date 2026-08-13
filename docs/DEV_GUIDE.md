# RyuuMD 开发手册

面向参与开发/二次开发者。涵盖架构、模块职责、关键机制、调试、测试与打包。

---

## 1. 技术栈与设计原则

| 层 | 技术 | 说明 |
| --- | --- | --- |
| 外壳 | pywebview（gui=edgechromium） | 复用系统 WebView2，包体极小 |
| 编辑器 | Vditor（vendor 全量内置） | IR 即时渲染 / SV 源码双模式 |
| 前端 | 原生 HTML/CSS/JS，零框架零构建 | IIFE 模块 + `window.Xxx` 命名空间 |
| 后端 | Python 3.10+，仅 pywebview 一个三方依赖 | JS ↔ Python 经 js_api 桥 |
| 持久化 | JSON（%APPDATA%/RyuuMD/config.json） | 线程安全，损坏自动回退默认 |

原则：**轻量、本地优先、极速**。不引框架、不加构建步骤；默认同步关闭无需联网。
新功能优先复用现有模式（事件委托、innerHTML 单次赋值、配置即状态）。

## 2. 目录与模块职责

```
main.py                     进程入口：单实例判定、WindowManager、webview.start
app/core/
  config.py                 Config：线程安全 JSON 配置（DEFAULTS 定义全部键，tmp+replace 原子写）
  fsutil.py                 共享常量 MD_EXTS/IGNORE_DIRS/IMAGE_EXTS + skip_dir_name + count_md_files + recycle_file
  api.py                    Api：暴露给 JS 的全部方法（每窗口一个实例；含文件管理 rename/move/delete、append_capture）
  search.py                 仓库 md 索引 / 全文搜索 / [[wikilink]] / 待办标签断链 / 未链接提及
  projects.py               ProjectStore：仓库增删改查/置顶/排序/打开计时
  file_assoc.py             Windows 文件关联：注册 + SHOpenWithDialog + 状态查询
  singleton.py              单实例：try_forward（客户端）/ InstanceServer（服务端）
  webdav.py                 轻量 WebDAV（PROPFIND/GET/PUT/MKCOL，仅标准库）
  cloud_sync.py             可选云同步引擎：门闩 + 三路比对 + 冲突副本
app/web/
  index.html                单页外壳：工具栏/侧栏/编辑器/首页/弹窗挂载点
  css/app.css               主题变量（:root / [data-theme=dark]）+ 外壳样式
  css/home.css              首页样式（仓库卡片/列表、快速操作、最近区）
  css/editor-theme.css      Vditor 渲染区 phycat 化
  css/slash.css             斜杠菜单
  css/palette.css           命令面板 + 本文查找条 + 专注/打字机/可读宽度
  js/icons.js               内联 SVG 图标集（feather 风格，currentColor）
  js/commands.js            斜杠命令定义（notion/wolai 两套触发词）
  js/slash.js               斜杠/右键菜单交互（含剪贴板组：复制/剪切/粘贴）+ `[[` 笔记过滤
  js/sidebar.js             侧栏：文件树/大纲/最近（容器级事件委托）+ 文件右键管理菜单 + 目录全折叠
  js/home.js                首页：仓库双视图/快速操作/最近/重命名弹窗
  js/welcome.js             首次欢迎窗口
  js/settings.js            设置弹窗（含默认应用、自动保存、云同步入口）
  js/editor.js              Vditor 封装：模式切换（保持阅读位置）/大纲提取/主题/大文档策略/相对图片/wikilink
  js/palette.js             Ctrl+P 快速打开 / Ctrl+Shift+P 命令 / Ctrl+Shift+F 搜索 / 待办·标签·断链·提及索引
  js/app.js                 主控制器：boot、启动策略、打开/保存、快捷键、拖放、贴图、自动保存、可读宽度
tests/
  test_api.py               Python 层单测（unittest，零三方依赖）
  test_cloud.py             云同步门闩/双向/冲突
  test_search.py            仓库检索/wikilink/贴图/日记/待办索引/收集箱
  test_e2e.py               真实窗口 E2E（evaluate_js 探针）
```

## 3. 架构与数据流

### 3.1 JS ↔ Python 桥

pywebview 把 `Api` 实例的公开方法挂到 `window.pywebview.api.*`；
前端 `await api().method(args)` 调用，返回值必须可 JSON 序列化。
约定：所有方法返回 `{ok: bool, ...}`，失败带 `error` 字符串。

### 3.2 多窗口模型

```
main()                          （进程级，仅一次）
 ├─ try_forward()  已有实例？→ 转发路径后本进程退出
 ├─ Config()                     全窗口共享（线程安全）
 ├─ InstanceServer.start()       监听转发请求 → manager.create(path)
 ├─ WindowManager.create(path)   每窗口：独立 Api(initial_path) + 拖放绑定 + 尺寸记忆
 └─ webview.start()              GUI 主循环（所有窗口关闭后返回）
```

- **每窗口一个 Api 实例**：`initial_path` 是窗口私有状态，`_window` 绑定各自窗口；
- **Config 全局共享**：主题/偏好改动即时落盘，新窗口启动即读到；
- pywebview 支持主循环启动后从任意线程 `create_window`（InstanceServer
  的 daemon 线程直接调用即可）。

### 3.3 窗口启动数据流（前端视角）

```
boot()
 ├─ 绑定按钮/快捷键/拖放（前置，不依赖后端）
 ├─ waitForApi() → get_config()
 ├─ Home.init(handlers) / Editor.init(...)
 └─ afterEditorReady()
     ├─ 首启 → 欢迎窗口
     ├─ await api.get_initial_path()   ← 命令行/转发/新窗口路径（主动拉取，无竞态）
     │   有 → openPath() 直接进编辑器
     ├─ startup_page == "home" → Home.show()
     └─ 否则 restoreSession()；无会话 → Home.show()
```

> 历史设计变更：初始路径原经 `evaluate_js` 注入 `window.__INITIAL_PATH__`，
> 存在「注入晚于 boot 检查」的竞态；现改为前端主动拉取，老通道仅作兼容兜底。

### 3.4 首页（home.js）数据流

```
Home.show() → renderGreeting() + refresh()
refresh() → Promise.all(list_projects, get_recent) → renderRepos / renderRecent
点击卡片 → api.open_project(id)（后端 touch 计时）→ handlers.openFolderResult
          → app.js applyFolder() → 侧栏渲染 + Home.hide()
```

视图偏好 `home_view` 经 `applyConfig` 持久化；仓库排序由后端统一
（置顶优先 → last_opened_at 倒序），前端不重排。

## 4. 关键机制

### 4.1 单实例（singleton.py）

- 主实例绑定 `127.0.0.1:随机端口`，端口 + 随机 token 写
  `%APPDATA%/RyuuMD/instance.json`；
- 新进程先 `try_forward`（1s 连接超时）：成功 → 主实例 `manager.create(path)`
  开新窗口，本进程退出；失败（残留文件/主实例已死）→ 自己接管为主实例；
- 一行 JSON 协议 `{token, action:"open", path}`，token 不符回 `denied`；
- `webview.start()` 返回（全窗口关闭）后 `server.stop()` 删锁文件。

### 4.2 Windows 文件关联（file_assoc.py）

- `register()`：全部写 **HKCU**（免管理员）——
  `Software\Classes\RyuuMD.Document`（ProgID+图标+命令）、
  各扩展名 `OpenWithProgids`、`Software\RyuuMD\Capabilities` +
  `RegisteredApplications`（出现在系统「默认应用」页）；
- `prompt_set_default()`：注册后 `SHOpenWithDialog`（OAIF_REGISTER_EXT|OAIF_EXEC）
  弹系统对话框；确认后系统用默认应用打开「验证文档」，经单实例机制在新窗口
  展示「设置成功」→ 闭环反馈；
- `status()`：`AssocQueryStringW(ASSOCSTR_PROGID)` 查当前默认是否为我们；
- 命令行兼容 frozen（exe）与源码运行（pythonw main.py）。

### 4.3 拖放双通道（历史坑，勿动）

Vditor 面板 drop 处理器首行 `stopPropagation`，事件冒泡被截断：

- **主通道**：app.js 在 window **捕获阶段**接管 drop → WebView2
  `postMessageWithAdditionalObjects("FilesDropped", files)` 回传原生 →
  pywebview 暂存路径到 `_dnd_state['paths']` → `api.open_dropped_files`
  按文件名匹配打开；
- **兜底通道**：main.py 经 pywebview DOM 事件注册 document 级 drop —— 该注册
  同时是 FilesDropped 原生捕获的开关（`num_listeners>0`），**必须保留**。

### 4.4 大文档策略

`read_file` 返回 `size`；前端 >512KB 且当前渲染模式 → 强制切源码模式 + toast，
离开大文档自动恢复用户偏好（`svForced` 标记）。

### 4.5 仓库统计预算

`count_md_files(budget=800, max_depth=5)`：迭代式 scandir，超预算即截断返回
`capped=True`（前端显示 `N+`）—— 保证任意大小的仓库首页秒开。

### 4.6 可选云同步（用户自备 WebDAV）

默认全关。生效须同时满足：`cloud_sync.enabled`、填好 url/username、以及
`sync_all_projects` 或仓库 `cloud_enabled`。

- 客户端：`WebDavClient` 预发 Basic（兼容坚果云/群晖不先 401 的实现）；
- 引擎：`CloudEngine` 按数据目录进程内单例，多窗口共享锁；
- 远端布局：`{url}/{remote_root}/{project_id}/相对路径`；
- 三路比对（本地 / 远端 / `cloud-state.json` 指纹）；冲突时远端另存
  `*.conflict-时间.md`，本地保留并上传；**不同步删除**；
- `get_config` 脱敏密码；`applyConfig` 禁止把 `cloud_sync` 经 `update_config` 回写
  （否则会冲掉密码）；
- 保存后 `push_file` 后台线程，失败不影响本地保存。

### 4.7 仓库检索与贴图

- `search.py`：scandir + 深度/文件数预算；跳过 `skip_dir_name`（含 `*.assets`）。
- 快速打开只扫文件名；全文搜索先文件名后正文（每文件一条命中、单文件最多读 256KB）。
- `[[wikilink]]`：当前文件旁相对路径 → 仓库根相对路径 → 全库词干匹配（同目录优先）。
- 未链接提及：去掉 `[[wikilink]]` 与 md 链接后再匹配当前笔记名；自身文件不计。
- 快速收集：`append_capture` 只允许仓库根文件名（禁止路径分隔符），追加 `## YYYY-MM-DD HH:MM` 分节。
- 贴图：已保存文档 → `{stem}.assets/`；否则仓库 `assets/`。前端把相对 `img src` 改写为 `file://` 以便 WebView2 显示。
- 外链点击走 `open_external`，禁止 WebView2 整页跳转。
- **T12**：设置弹窗保持 7 行（含自动保存）。可读宽度等开关走命令面板 + `DEFAULTS`，禁止再加 `.setting-row`。

## 5. 开发与调试

```bash
pip install -r requirements.txt
python main.py                        # 开发运行
# main.py 中 webview.start(debug=True) 可开 DevTools（右键 → 检查）
```

- 前端改动刷新即生效（重启应用即可，无构建步骤）；
- 后端 print 调试：源码运行时控制台可见；
- 配置隔离实验：临时改 `APPDATA` 环境变量再启动（注意：Python 用户
  site-packages 也在 APPDATA 下，pip --user 安装的依赖会失联，建议虚拟环境）。

### 代码风格

- Python：中文注释说明「为什么」，类型标注，异常宽容（工具类应用，
  失败返回 `{ok:False,error}` 而非抛出）；
- JS：IIFE + `window.Xxx` 导出；列表渲染用**单次 innerHTML + 容器级事件委托**
  （大目录性能关键）；所有用户可见文案中文；
- CSS：一律用 `var(--*)` 主题变量，新组件同时适配亮/暗主题。

## 6. 测试

```bash
python run_tests.py                   # 单测 + E2E 全量
python -m unittest tests.test_api tests.test_cloud tests.test_search -v
python tests/test_e2e.py              # 仅 E2E（须真实窗口，关闭其他实例）
```

- 单测覆盖后端纯逻辑（文件/树/仓库/关联/单实例/多窗口 API/云同步/检索）；
- E2E 用 `evaluate_js` 探针驱动真实窗口断言 UI 行为（当前 62 项）；
- 新增功能必须配套用例；中文注入断言一律用 `JV()`（json.dumps）；
- 详见 `docs/TEST_PLAN.md`（含手动验证清单）。

## 7. 打包发布

```bash
build.bat            # onefile（默认）
build.bat onedir     # onedir
```

- 脚本会先跑单元测试作门禁（`[2/5]` 步），单测不过则中止构建，E2E 需手动 `python run_tests.py`；
- 新增的 `app/web` 静态资源自动随 `('app/web','app/web')` datas 打包；
- 新增 Python 模块经 import 自动分析，无需改 spec；
- 若引入新的动态 import → 加 `hiddenimports`；
- 发布前过一遍 `docs/TEST_PLAN.md` 手动清单（含多窗口/关联/拖放真实操作）。
