# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置 —— 单文件 exe（onefile，含 web 资源）。
   构建： pyinstaller RyuuMD-onefile.spec  （或 build.bat onefile）
   产物： dist/RyuuMD.exe（单个可执行文件）。
   说明： 运行时会把内置资源解压到临时目录（sys._MEIPASS），
          故首次/每次启动比 onedir 略慢，体积也略大。
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
    excludes=[],
    cipher=block_cipher,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

# onefile：把 binaries / datas 直接打进 EXE，不使用 COLLECT。
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='RyuuMD',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    # 关闭 UPX：UPX 压缩壳是杀软启发式扫描的重点怀疑特征，开启显著增加
    # Microsoft Defender 误报概率；体积略增但换来分发可用性。
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    # 写入版本信息资源（公司/产品/描述），匿名 exe 更易被误报
    version='version_info.txt',
    console=False,            # 窗口程序，无控制台
    disable_windowed_traceback=False,
    icon='assets/icon.ico',
)
