# -*- mode: python ; coding: utf-8 -*-
"""macOS .app（onedir + BUNDLE）。只能在 macOS 上构建，勿与 Windows spec 混用。

   构建（仓库根目录）： bash packaging/build-mac.sh
   产物： dist/RyuuMD.app
   前置： assets/icon.icns（由 build-mac.sh 用 sips/iconutil 从 icon.png 生成）
"""
import os
import sys

from PyInstaller.utils.hooks import collect_submodules

if sys.platform != "darwin":
    raise SystemExit("RyuuMD-mac.spec 只能在 macOS 上构建")

# spec 内相对路径相对本文件目录解析：以 SPECPATH 锚定仓库根（本文件在 packaging/ 下）
ROOT = os.path.dirname(SPECPATH)

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
    [os.path.join(ROOT, "main.py")],
    pathex=[],
    binaries=[],
    datas=[
        (os.path.join(ROOT, "app/web"), "app/web"),
        (os.path.join(ROOT, "assets"), "assets"),
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
    icon=os.path.join(ROOT, "assets/icon.icns"),
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
    icon=os.path.join(ROOT, "assets/icon.icns"),
    bundle_identifier="com.ryuuji.ryuumd",
    info_plist={
        "CFBundleName": "RyuuMD",
        "CFBundleDisplayName": "RyuuMD",
        "CFBundleShortVersionString": "1.8.5",  # 与 packaging/version_info.txt 同步
        "CFBundleVersion": "1.8.5",
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
