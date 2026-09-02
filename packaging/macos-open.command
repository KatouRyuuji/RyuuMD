#!/bin/bash
# 未公证包：清掉隔离属性后启动。从浏览器下载的 zip 解压后请用本脚本或右键打开。
cd "$(dirname "$0")"
xattr -cr "RyuuMD.app" 2>/dev/null || true
open "RyuuMD.app"
