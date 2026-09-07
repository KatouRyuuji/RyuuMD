# RyuuMD 维护手册

面向维护者/运维支持。涵盖数据落点、系统痕迹、排障、发布与升级注意事项。

---

## 1. 数据落点（全部在用户侧，绿色无残留安装）

| 路径 | 内容 | 可否删除 |
| --- | --- | --- |
| `%APPDATA%\RyuuMD\config.json`（macOS：`~/Library/Application Support/RyuuMD/config.json`） | 全部配置：偏好、仓库列表、最近记录、会话、窗口尺寸、编辑区缩放、可读宽度、**云同步账号（含密码）** | 可，删后回默认（不影响笔记文件） |
| `%APPDATA%\RyuuMD\cloud-state.json` | 云同步指纹（相对路径 → sha/mtime），用于三路比对 | 可，删后下次按内容重新对齐 |
| `%APPDATA%\RyuuMD\instance.json` | 单实例锁（端口+token），运行时存在 | 运行中勿删；异常残留可删 |
| `%APPDATA%\RyuuMD\默认应用验证.md` | 「设为默认」流程生成的验证文档 | 可随时删 |

**程序本体**：onedir 整个目录 / onefile 单 exe（运行时解压到 `%TEMP%\_MEIxxxx`，
退出自动清理；强杀进程可能残留，可手动清 `%TEMP%\_MEI*`）。

## 2. 注册表痕迹（仅「设为默认应用」后存在，全部 HKCU）

| 键 | 用途 |
| --- | --- |
| `HKCU\Software\Classes\RyuuMD.Document` | ProgID：文档名/图标/打开命令 |
| `HKCU\Software\Classes\.md 等各扩展\OpenWithProgids\RyuuMD.Document` | 「打开方式」候选 |
| `HKCU\Software\RyuuMD\Capabilities` | 系统「默认应用」页元数据 |
| `HKCU\Software\RegisteredApplications\RyuuMD` | Capabilities 指针 |
| `HKCU\...\Explorer\FileExts\.md\UserChoice` | 用户在系统对话框确认后由系统写入 |

**彻底卸载清理**（可选，PowerShell）：

```powershell
Remove-Item -Recurse "HKCU:\Software\Classes\RyuuMD.Document" -ErrorAction SilentlyContinue
".md",".markdown",".mdown",".mkd",".mdx" | ForEach-Object {
  Remove-ItemProperty "HKCU:\Software\Classes\$_\OpenWithProgids" "RyuuMD.Document" -ErrorAction SilentlyContinue }
Remove-Item -Recurse "HKCU:\Software\RyuuMD" -ErrorAction SilentlyContinue
Remove-ItemProperty "HKCU:\Software\RegisteredApplications" "RyuuMD" -ErrorAction SilentlyContinue
Remove-Item -Recurse "$env:APPDATA\RyuuMD" -ErrorAction SilentlyContinue
```

## 3. 常见故障排查

### 3.1 启动无窗口 / 白屏

1. 确认 WebView2 Runtime 存在（Win11 自带；Win10 个别精简系统缺失 →
   安装 Microsoft Edge WebView2 Runtime）；
2. 查看是否有残留 `msedgewebview2.exe` 进程 → 结束后重试；
3. 源码运行看控制台报错；打包版可临时把 `main.py` 的 `debug=False` 改 True 重打包。

### 3.2 双击 md 没有复用实例（每次都冷启动新进程）

1. 检查 `%APPDATA%\RyuuMD\instance.json` 是否存在且主实例在运行；
2. 防火墙/安全软件拦截 127.0.0.1 回环连接（极少见）→ 加白名单；
3. 残留锁文件指向已死端口：`try_forward` 连接失败会自动接管，无需处理；
   若接管失败，删除 `instance.json` 重启。

### 3.3 「设为默认」后双击仍用旧应用

- 系统对话框中未勾选「始终」→ 重新走一遍设置流程；
- 企业组策略锁定文件关联 → 需管理员放开；
- 设置页按钮显示「已是默认 ✓」但资源管理器图标未刷新 → 注销/重启资源管理器
  （代码已调 SHChangeNotify，个别环境缓存顽固）。

### 3.4 程序移动位置后关联失效

注册命令写的是绝对路径。把程序挪走后重新执行一次「设置 → 设为默认」即可
（register 幂等覆盖旧路径）。

### 3.5 配置损坏

`config.json` 非法 JSON 时自动回退默认值启动（不崩溃）。如需保留仓库列表，
手工修复 JSON 中 `projects` 数组即可。

### 3.6 仓库卡片显示「目录不存在」

目录被移动/重命名/删除。移除该仓库重新添加，或把目录恢复原位。

### 3.7 云同步失败 / 不想再同步

- 「未启用」：设置里总开关仍是关的，这是默认状态；
- 「未填写 WebDAV 地址」：只开了开关没填服务器；
- 「该仓库未开启云同步」：未勾选「同步全部仓库」，也未在首页点云朵；
- 401/403：用户名或应用密码错误（坚果云必须用应用密码）；
- SSL 错误：内网 NAS 自签证书可勾选「忽略 SSL」；公网使用受信任证书；
- 冲突文件 `*.conflict-*.md`：两边同时改过，打开对比后自行合并；
- 关闭同步：关掉总开关即可，本地文件不受影响。可再删
  `cloud-state.json` 与 config 里的 `cloud_sync.password`。

## 4. 测试与质量门禁

```bash
python run_tests.py     # 单测 126 + E2E 90，全绿才可发布
```

E2E 前置：真实窗口环境、关闭其他 RyuuMD/WebView2 实例。
每次发布前过一遍 `docs/TEST_PLAN.md` 的手动验证清单
（多窗口、单实例转发、默认应用、真实拖放等自动化盲区）。

## 5. 发布流程

1. `python run_tests.py` 全绿；
2. 更新 `version_info.txt` 版本号（降低杀软误报的版本资源）；
3. `build.bat [onefile|onedir] [nopause]`：
   - 自动结束运行中的 RyuuMD.exe（防止占用 dist 产物导致 PermissionError）；
   - 内置单测闸门（test_api + test_cloud + test_search + test_fonts + test_ai，失败即终止）；
   - 交互双击运行结尾 `pause`；脚本/CI 调用传第二参 `nopause`（或 `RYUUMD_NOPAUSE=1`）；
4. 手动清单走查（TEST_PLAN 末节）；
5. 产物：`dist/RyuuMD.exe`（onefile）/ `dist/RyuuMD/`（onedir），两者可共存；
6. **macOS（未公证）**：推送 `v*` tag 或手动跑 Actions **macOS 打包**；
   产物 `RyuuMD-mac-arm64.zip`。无 Apple 签名，用户需右键打开或 `xattr -cr`。
   本机 Windows 不能打 Mac 包。有 Mac 时 `bash build-mac.sh`。
7. 本地提交勿直接推远端（项目约定）。

## 6. 升级兼容性注意

- **config.json 向后兼容**：新键一律进 `DEFAULTS` 给默认值；读取用
  `config.get(key, default)`，老配置文件缺键不炸；
- **仓库数据**（`projects`）结构变更需写迁移（读时兼容旧结构，写时升级）；
- **注册表命令路径**随安装位置变化：升级替换 exe 位置不变则关联持续有效；
- 单实例协议（一行 JSON）如需扩展字段，保持旧字段语义不变（老进程可能
  与新进程短暂共存）。

## 7. 性能红线（改动时守住）

| 场景 | 机制 | 红线 |
| --- | --- | --- |
| 大目录加载 | 树扫描深度 8 / 条目 2000 截断 | 打开任意目录不卡死 |
| 大文档 | >512KB 自动源码模式 | 打开任意 md 不卡死 |
| 首页仓库统计 | count 预算 800 / 深度 5 | 首页渲染 < 1s |
| 文件树/大纲渲染 | 单次 innerHTML + 事件委托 | 千级节点流畅 |
| 长文输入 | 防抖 250→1200ms（>30 万字） | 连续输入不掉帧 |
