#!/bin/bash
# 未公证包：清掉隔离属性后启动。必须在 Mac 上解压后再运行。
cd "$(dirname "$0")"
xattr -cr "RyuuMD.app" 2>/dev/null || true
xattr -d com.apple.quarantine "RyuuMD.app" 2>/dev/null || true
open "RyuuMD.app"
