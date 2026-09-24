# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置 —— 生成单文件夹 exe（含 web 资源）。
   构建： pyinstaller RyuuMD.spec
"""

from PyInstaller.utils.hooks import collect_submodules

block_cipher = None

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    datas=[
        ('app/web', 'app/web'),   # 前端资源（含 vendor/vditor 全量）
        ('assets', 'assets'),     # 应用图标
    ],
    hiddenimports=['webview.platforms.edgechromium'] + collect_submodules('app.core'),
    hookspath=[],
    runtime_hooks=[],
    # cryptography 由 webview 包静态引用但运行时惰性 import 且本应用永不触发
    # （已验证 import webview/platforms 后 sys.modules 无它）；排除省约 15MB 解压/扫描
    excludes=['cryptography'],
    cipher=block_cipher,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='RyuuMD',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    # 关闭 UPX：UPX 压缩壳是杀软启发式扫描的重点怀疑特征，开启显著增加
    # Microsoft Defender 误报概率；体积略增但换来分发可用性。
    upx=False,
    # 写入版本信息资源（公司/产品/描述），匿名 exe 更易被误报
    version='version_info.txt',
    console=False,            # 窗口程序，无控制台
    disable_windowed_traceback=False,
    icon='assets/icon.ico',
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,   # 同 EXE 处说明：关闭 UPX 防误报
    upx_exclude=[],
    name='RyuuMD',
)
