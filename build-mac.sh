#!/usr/bin/env bash
# RyuuMD macOS 打包：生成 icns → 单测门禁 → PyInstaller .app → zip
# 只能在 macOS 上运行（GitHub Actions macos-latest / 本机 Mac）。
set -euo pipefail
cd "$(dirname "$0")"

if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "[错误] build-mac.sh 只能在 macOS 上运行" >&2
  exit 1
fi

PY="${PYTHON:-python3}"
if ! command -v "$PY" >/dev/null 2>&1; then
  echo "[错误] 未找到 Python（python3）" >&2
  exit 1
fi

ARCH="$(uname -m)"
case "$ARCH" in
  arm64) ZIP_NAME="RyuuMD-mac-arm64.zip" ;;
  x86_64) ZIP_NAME="RyuuMD-mac-x86_64.zip" ;;
  *) ZIP_NAME="RyuuMD-mac-${ARCH}.zip" ;;
esac

echo "============================================"
echo "  RyuuMD macOS 打包  [$ARCH → $ZIP_NAME]"
echo "============================================"
"$PY" --version

echo
echo "[1/5] 安装依赖 / PyInstaller ..."
"$PY" -m pip install -r requirements.txt pyinstaller

echo
echo "[2/5] 单元测试门禁 ..."
"$PY" -m unittest tests.test_api tests.test_cloud tests.test_search -v

echo
echo "[3/5] 从 icon.png 生成 icon.icns ..."
if [[ ! -f assets/icon.png ]]; then
  echo "[错误] 缺少 assets/icon.png" >&2
  exit 1
fi
ICONSET="build/RyuuMD.iconset"
rm -rf "$ICONSET"
mkdir -p "$ICONSET"
sips -z 16 16     assets/icon.png --out "$ICONSET/icon_16x16.png" >/dev/null
sips -z 32 32     assets/icon.png --out "$ICONSET/icon_16x16@2x.png" >/dev/null
sips -z 32 32     assets/icon.png --out "$ICONSET/icon_32x32.png" >/dev/null
sips -z 64 64     assets/icon.png --out "$ICONSET/icon_32x32@2x.png" >/dev/null
sips -z 128 128   assets/icon.png --out "$ICONSET/icon_128x128.png" >/dev/null
sips -z 256 256   assets/icon.png --out "$ICONSET/icon_128x128@2x.png" >/dev/null
sips -z 256 256   assets/icon.png --out "$ICONSET/icon_256x256.png" >/dev/null
sips -z 512 512   assets/icon.png --out "$ICONSET/icon_256x256@2x.png" >/dev/null
sips -z 512 512   assets/icon.png --out "$ICONSET/icon_512x512.png" >/dev/null
sips -z 1024 1024 assets/icon.png --out "$ICONSET/icon_512x512@2x.png" >/dev/null
iconutil -c icns "$ICONSET" -o assets/icon.icns

echo
echo "[4/5] PyInstaller RyuuMD-mac.spec ..."
"$PY" -m PyInstaller --noconfirm --clean RyuuMD-mac.spec

if [[ ! -d dist/RyuuMD.app ]]; then
  echo "[错误] 未生成 dist/RyuuMD.app" >&2
  exit 1
fi

echo
echo "[5/5] 打包 zip（含未公证说明）..."
STAGE="dist/RyuuMD-macos"
rm -rf "$STAGE"
mkdir -p "$STAGE"
cp -R dist/RyuuMD.app "$STAGE/"
cp packaging/macos-first-run.txt "$STAGE/请先读我.txt"
cp packaging/macos-open.command "$STAGE/打开 RyuuMD.command"
chmod +x "$STAGE/打开 RyuuMD.command"
rm -f "dist/$ZIP_NAME"
( cd "$STAGE" && ditto -c -k . "../$ZIP_NAME" )

echo
echo "完成: dist/$ZIP_NAME"
echo "未公证：用户需右键打开，或运行「打开 RyuuMD.command」。"
