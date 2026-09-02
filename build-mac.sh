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
  arm64) STEM="RyuuMD-mac-arm64" ;;
  x86_64) STEM="RyuuMD-mac-x86_64" ;;
  *) STEM="RyuuMD-mac-${ARCH}" ;;
esac
ZIP_NAME="${STEM}.zip"
TAR_NAME="${STEM}.tar.gz"

echo "============================================"
echo "  RyuuMD macOS 打包  [$ARCH → $TAR_NAME]"
echo "============================================"
"$PY" --version

echo
echo "[1/6] 安装依赖 / PyInstaller ..."
"$PY" -m pip install -r requirements.txt pyinstaller

echo
echo "[2/6] 单元测试门禁 ..."
"$PY" -m unittest tests.test_api tests.test_cloud tests.test_search -v

echo
echo "[3/6] 从 icon.png 生成 icon.icns ..."
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
echo "[4/6] PyInstaller RyuuMD-mac.spec ..."
"$PY" -m PyInstaller --noconfirm --clean RyuuMD-mac.spec

if [[ ! -d dist/RyuuMD.app ]]; then
  echo "[错误] 未生成 dist/RyuuMD.app" >&2
  exit 1
fi

echo
echo "[5/6] ad-hoc 深签名 + 启动冒烟 ..."
codesign --force --deep --sign - dist/RyuuMD.app
APP_BIN="dist/RyuuMD.app/Contents/MacOS/RyuuMD"
chmod +x "$APP_BIN"
rm -f /tmp/ryuumd-smoke.out /tmp/ryuumd-smoke.err
"$APP_BIN" >/tmp/ryuumd-smoke.out 2>/tmp/ryuumd-smoke.err &
SPID=$!
sleep 10
if kill -0 "$SPID" 2>/dev/null; then
  echo "  smoke: 进程仍在运行"
  kill "$SPID" 2>/dev/null || true
  wait "$SPID" 2>/dev/null || true
else
  echo "[错误] 冒烟失败：进程 10 秒内退出" >&2
  echo "----- stdout -----" >&2
  cat /tmp/ryuumd-smoke.out >&2 || true
  echo "----- stderr -----" >&2
  cat /tmp/ryuumd-smoke.err >&2 || true
  exit 1
fi

echo
echo "[6/6] 打包 tar.gz / zip（含未公证说明）..."
STAGE="dist/RyuuMD-macos"
rm -rf "$STAGE"
mkdir -p "$STAGE"
ditto dist/RyuuMD.app "$STAGE/RyuuMD.app"
cp packaging/macos-first-run.txt "$STAGE/请先读我.txt"
cp packaging/macos-open.command "$STAGE/打开 RyuuMD.command"
chmod +x "$STAGE/打开 RyuuMD.command"
rm -f "dist/$ZIP_NAME" "dist/$TAR_NAME"
COPYFILE_DISABLE=1 tar -C "$STAGE" -czf "dist/$TAR_NAME" .
( cd "$STAGE" && ditto -c -k . "../$ZIP_NAME" )

echo
echo "完成: dist/$TAR_NAME  （推荐，保留符号链接）"
echo "      dist/$ZIP_NAME"
echo "未公证：必须在 Mac 上解压；提示「已损坏」时执行 xattr -cr RyuuMD.app"
