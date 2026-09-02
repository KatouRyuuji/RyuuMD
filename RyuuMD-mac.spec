# -*- mode: python ; coding: utf-8 -*-
"""macOS .app（onedir + BUNDLE）。只能在 macOS 上构建，勿与 Windows spec 混用。

   构建： bash build-mac.sh
   产物： dist/RyuuMD.app
   前置： assets/icon.icns（由 build-mac.sh 用 sips/iconutil 从 icon.png 生成）
"""
import sys

from PyInstaller.utils.hooks import collect_submodules

if sys.platform != "darwin":
    raise SystemExit("RyuuMD-mac.spec 只能在 macOS 上构建")

block_cipher = None

hiddenimports = sorted(
    set(
        collect_submodules("webview")
        + collect_submodules("objc")
        + [
            "WebKit",
            "AppKit",
            "Foundation",
            "CoreFoundation",
            "Quartz",
            "Security",
            "UniformTypeIdentifiers",
            "PyObjCTools",
        ]
    )
)

a = Analysis(
    ["main.py"],
    pathex=[],
    binaries=[],
    datas=[
        ("app/web", "app/web"),
        ("assets", "assets"),
    ],
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=[],
    cipher=block_cipher,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="RyuuMD",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    icon="assets/icon.icns",
    # Finder / 「打开方式」经 Apple Event 传入的路径转成 sys.argv
    argv_emulation=True,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="RyuuMD",
)
app = BUNDLE(
    coll,
    name="RyuuMD.app",
    icon="assets/icon.icns",
    bundle_identifier="com.ryuuji.ryuumd",
    info_plist={
        "CFBundleName": "RyuuMD",
        "CFBundleDisplayName": "RyuuMD",
        "CFBundleShortVersionString": "1.4.0",  # 与 version_info.txt 同步
        "CFBundleVersion": "1.4.0",
        "NSHighResolutionCapable": True,
        "NSRequiresAquaSystemAppearance": False,
        "LSMinimumSystemVersion": "11.0",
        "NSAppTransportSecurity": {"NSAllowsLocalNetworking": True},
        "CFBundleDocumentTypes": [
            {
                "CFBundleTypeName": "Markdown document",
                "CFBundleTypeRole": "Editor",
                "LSHandlerRank": "Alternate",
                "CFBundleTypeExtensions": ["md", "markdown", "mdown", "mkd", "mdx"],
            }
        ],
    },
)
