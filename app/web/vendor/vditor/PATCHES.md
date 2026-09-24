# Vditor vendor 本地补丁记录

打包版本：Vditor v3.11.2（dist/index.min.js，minified）。

## 1. IR compositionend 延迟一拍（2026-09-24）

位置：`dist/index.min.js`，IR 模式的 `compositionend` 监听（搜索 `compositionend",(function(n){(0,c.vU)()||P(e,`）。

原实现：compositionend 同步执行 IR 输入处理器 P（`insertNode(<wbr>)` → 取块 outerHTML → Lute spin → 整块 outerHTML 重建）。

补丁：把 P 与 `composingLock = false` 一起放进 `setTimeout(..., 0)`，range 在 compositionend 时克隆保留。

原因：WebView2（TSF 文本输入桥）的 IME 提交时序与桌面 Chrome 有偏差，compositionend 瞬间 DOM 里可能仍是生拼音；同步整块重写会把拼音当正文 spin 进文档，随后 IME 提交的中文再插一次，表现为「中文+拼音」双份（如输入「测试」得到「测试ceshi」）。延迟一拍让 TSF 的 DOM 提交先落地。

升级 Vditor 时须重新评估此补丁（对照原串是否存在、P 的签名是否变化）。
