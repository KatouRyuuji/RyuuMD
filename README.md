# RyuuMD 墨读

轻量、全本地、极速的 Markdown 编辑与阅读工具。pywebview + Vditor IR 所见即所得，
以仓库形式管理笔记目录，首页一键切换，支持多窗口、双链、AI 助手（Anthropic）、
可选 WebDAV 云同步、mermaid/公式/竖排、斜杠命令与命令面板。

## 运行

```bash
pip install -r requirements.txt
python main.py
# 或双击 run.bat
```

## 打包

```bash
packaging\build.bat              # 单文件夹 onedir（默认，启动快）
packaging\build.bat onefile      # 单文件 exe（便携分发）
packaging\build.bat installer    # onedir + Inno Setup 安装包（需 Inno Setup 6）
bash packaging/build-mac.sh      # macOS .app（仅 macOS，Apple Silicon）
```

产物在 `dist/`。打包脚本自带单元测试门禁，单测不过会中止。

## 测试

```bash
python scripts/run_tests.py          # 单测 + 真实窗口 E2E（E2E 前请关闭其他 RyuuMD 实例）
python scripts/run_tests.py --unit   # 仅单测（打包门禁）
```

## 命令行与 MCP

无参数启动 GUI；首参数是命令名时走命令行（源码运行把 `RyuuMD` 换成 `python main.py`）：

```bash
RyuuMD read <path> / write <path> / ls <path> / search <query> / vaults / scratch [TEXT]
RyuuMD tools                       # 列出全部 AI 工具（JSON，含参数 schema）
RyuuMD tool <name> [--args JSON]   # 调用工具
RyuuMD mcp --install               # 注册到 Claude Code（user 级）
```

## 许可证

RyuuMD 以 [Apache License 2.0](LICENSE) 发布。随附第三方组件保留各自许可证：

| 组件 | 位置 | 许可证 |
| --- | --- | --- |
| Vditor 3.11.2（含本地补丁） | `app/web/vendor/vditor/` | MIT |
| Noto Sans SC | `app/web/assets/fonts/noto-sans-sc/` | SIL OFL 1.1 |
| 霞鹜文楷 LXGW WenKai | `app/web/assets/fonts/LXGWWenKai-Regular.ttf` | SIL OFL 1.1 |
| Cascadia Code | `app/web/assets/fonts/Cascadia-Code-Regular.ttf` | SIL OFL 1.1 |
| Inno Setup 简体中文语言文件 | `packaging/ChineseSimplified.isl` | Inno Setup 许可 |

应用图标与「墨读」形象素材（`assets/logo.png` 及由它生成的图标、安装向导图）版权归作者所有，不在 Apache-2.0 授权范围内。
