/* AI 侧栏：提问、概括文档/仓库、知识谱系。所有 AI 交互的统一面板。
   结果以日志条目追加（提问与回答成对），pending 条目在响应到达后原地替换。
   知识谱系由后端按仓库缓存，侧栏按钮在「生成 / 刷新」两态间切换。
   Markdown 只做白名单渲染（先转义再加标签），模型输出不进 raw HTML。 */
(function () {
  function esc(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;")
      .replace(/>/g, "&gt;").replace(/"/g, "&quot;");
  }

  function markdown(src) {
    const raw = String(src == null ? "" : src);
    const fences = [];
    let s = raw.replace(/```[\s\S]*?```/g, (m) => {
      fences.push(m);
      return "\0F" + (fences.length - 1) + "\0";
    });
    s = esc(s);
    s = s.replace(/`([^`\n]+)`/g, "<code>$1</code>");
    s = s.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
    s = s.replace(/^###### (.+)$/gm, "<h6>$1</h6>");
    s = s.replace(/^##### (.+)$/gm, "<h5>$1</h5>");
    s = s.replace(/^#### (.+)$/gm, "<h4>$1</h4>");
    s = s.replace(/^### (.+)$/gm, "<h3>$1</h3>");
    s = s.replace(/^## (.+)$/gm, "<h2>$1</h2>");
    s = s.replace(/^# (.+)$/gm, "<h1>$1</h1>");
    s = s.replace(/^(?:- |\* )(.+)$/gm, "<li>$1</li>");
    s = s.replace(/(<li>.*<\/li>\n?)+/g, (m) => "<ul>" + m.replace(/\n/g, "") + "</ul>");
    s = s.replace(/\n/g, "<br>");
    s = s.replace(/\0F(\d+)\0/g, (_, i) => {
      const block = fences[Number(i)] || "";
      const nl = block.indexOf("\n");
      const body = nl >= 0 ? block.slice(nl + 1).replace(/\s*```$/, "") : "";
      return "<pre><code>" + esc(body) + "</code></pre>";
    });
    return s;
  }

  window.AiText = { esc: esc, markdown: markdown };
})();

(function () {
  const panel = document.getElementById("ai-sidebar");
  const logEl = document.getElementById("ai-side-log");
  const emptyEl = document.getElementById("ai-side-empty");
  const askInput = document.getElementById("ai-ask-input");
  const knowBtn = document.getElementById("ai-knowledge");

  let deps = {}; // { api, getFolder, getPath, getContent, toast }
  let knowledgeDone = false;
  let inflight = 0;

  function onEsc() { close(); }

  function init(d) {
    deps = d || {};
    document.getElementById("ai-side-close").addEventListener("click", close);
    document.getElementById("ai-sum-doc").addEventListener("click", summarizeDoc);
    document.getElementById("ai-sum-vault").addEventListener("click", summarizeVault);
    knowBtn.addEventListener("click", () => knowledge());
    document.getElementById("ai-ask-send").addEventListener("click", sendAsk);
    askInput.addEventListener("keydown", (e) => {
      if (e.key === "Enter") { e.preventDefault(); sendAsk(); }
    });
  }

  function isOpen() { return !panel.hidden; }

  function open() {
    panel.hidden = false;
    syncButton();
    if (window.App && window.App.registerEscape) window.App.registerEscape(onEsc);
  }

  function close() {
    panel.hidden = true;
    syncButton();
    if (window.App && window.App.unregisterEscape) window.App.unregisterEscape(onEsc);
  }

  function toggle() { if (isOpen()) close(); else open(); }

  function syncButton() {
    const btn = document.getElementById("btn-ai");
    if (btn) btn.setAttribute("aria-expanded", isOpen() ? "true" : "false");
  }

  function setBusy(on) {
    inflight += on ? 1 : -1;
    if (inflight < 0) inflight = 0;
    const busy = inflight > 0;
    ["ai-sum-doc", "ai-sum-vault", "ai-knowledge", "ai-ask-send"].forEach((id) => {
      const el = document.getElementById(id);
      if (el) el.disabled = busy;
    });
    askInput.disabled = busy;
  }

  function toast(msg, opts) { if (deps.toast) deps.toast(msg, opts); }

  function setKnowLabel(done) {
    knowBtn.innerHTML = done
      ? '<span class="ai-know-icon">' + ((window.ICONS && window.ICONS.refresh) || "") + "</span>刷新知识谱系"
      : "生成知识谱系";
  }

  function addEntry(label, body, opts) {
    opts = opts || {};
    if (emptyEl) emptyEl.style.display = "none";
    const el = document.createElement("div");
    el.className = "ai-entry" + (opts.error ? " is-error" : "") + (opts.pending ? " is-pending" : "");
    el.innerHTML = '<div class="ai-entry-head"><div class="ai-entry-label"></div>'
      + '<div class="ai-entry-tools">'
      + '<button type="button" class="ai-entry-tool" data-act="copy">复制</button>'
      + '<button type="button" class="ai-entry-tool" data-act="insert">插入文档</button>'
      + "</div></div>"
      + '<div class="ai-entry-body"></div>'
      + '<div class="ai-entry-note" hidden></div>';
    el.querySelector(".ai-entry-label").textContent = label;
    const bodyEl = el.querySelector(".ai-entry-body");
    let rawText = String(body == null ? "" : body);
    function paint(text, asError, truncated) {
      rawText = String(text == null ? "" : text);
      if (asError) {
        bodyEl.textContent = rawText;
      } else {
        bodyEl.innerHTML = window.AiText.markdown(rawText);
      }
      const note = el.querySelector(".ai-entry-note");
      if (truncated) {
        note.hidden = false;
        note.textContent = "仅根据仓库前若干篇摘录生成，结果可能不完整。";
      } else {
        note.hidden = true;
        note.textContent = "";
      }
    }
    paint(rawText, !!opts.error, false);
    el.querySelector('[data-act="copy"]').addEventListener("click", async () => {
      try {
        if (navigator.clipboard && navigator.clipboard.writeText) {
          await navigator.clipboard.writeText(rawText);
        } else {
          throw new Error("no clipboard");
        }
        toast("已复制");
      } catch (e) {
        toast("复制失败", { type: "error" });
      }
    });
    el.querySelector('[data-act="insert"]').addEventListener("click", () => {
      if (!window.Editor || !window.Editor.insertValue) {
        toast("编辑器尚未就绪", { type: "error" });
        return;
      }
      window.Editor.insertValue(rawText);
      toast("已插入当前文档");
    });
    logEl.appendChild(el);
    logEl.scrollTop = logEl.scrollHeight;
    return {
      settle(body2, error2, extra) {
        extra = extra || {};
        el.classList.remove("is-pending");
        el.classList.toggle("is-error", !!error2);
        paint(body2, !!error2, !!extra.truncated);
        if (extra.cached && !error2) {
          const note = el.querySelector(".ai-entry-note");
          note.hidden = false;
          note.textContent = (note.textContent ? note.textContent + " " : "") + "来自本窗口缓存。";
        }
        logEl.scrollTop = logEl.scrollHeight;
      },
    };
  }

  function folder() { return (deps.getFolder && deps.getFolder()) || ""; }

  async function settleCall(fn, pending, failText) {
    setBusy(true);
    try {
      const res = await fn();
      if (res && res.ok) {
        pending.settle(res.answer || res.text || "（无内容）", false, {
          truncated: !!res.truncated,
          cached: !!res.cached,
        });
        return true;
      }
      pending.settle((res && res.error) || failText, true);
    } catch (e) {
      pending.settle(failText + "：" + (e && e.message ? e.message : e), true);
    } finally {
      setBusy(false);
    }
    return false;
  }

  async function sendAsk() {
    const q = (askInput.value || "").trim();
    if (!q) return;
    if (inflight) { toast("请等待当前请求完成"); return; }
    const api = deps.api && deps.api();
    if (!api || !api.ask_ai) { toast("后端尚未就绪", { type: "error" }); return; }
    askInput.value = "";
    addEntry("提问", q);
    const pending = addEntry("AI 回答", "正在生成…", { pending: true });
    await settleCall(() => api.ask_ai(folder(), q), pending, "提问失败");
  }

  async function summarizeDoc() {
    if (inflight) { toast("请等待当前请求完成"); return; }
    const api = deps.api && deps.api();
    if (!api || !api.summarize_document) { toast("后端尚未就绪", { type: "error" }); return; }
    const path = (deps.getPath && deps.getPath()) || "";
    const content = (deps.getContent && deps.getContent()) || "";
    if (!path && !content.trim()) { toast("请先打开一篇文档"); return; }
    const pending = addEntry("概括当前文档", "正在生成…", { pending: true });
    await settleCall(() => api.summarize_document(path, content, folder()), pending, "概括失败");
  }

  async function summarizeVault() {
    if (inflight) { toast("请等待当前请求完成"); return; }
    const api = deps.api && deps.api();
    if (!api || !api.summarize_vault) { toast("后端尚未就绪", { type: "error" }); return; }
    if (!folder()) { toast("请先打开仓库"); return; }
    const pending = addEntry("概括当前仓库", "正在生成…", { pending: true });
    await settleCall(() => api.summarize_vault(folder()), pending, "概括失败");
  }

  async function knowledge(refresh) {
    if (inflight) { toast("请等待当前请求完成"); return; }
    const api = deps.api && deps.api();
    if (!api || !api.knowledge_tree) { toast("后端尚未就绪", { type: "error" }); return; }
    if (!folder()) { toast("请先打开仓库"); return; }
    const useRefresh = refresh == null ? knowledgeDone : !!refresh;
    const pending = addEntry("知识谱系", useRefresh ? "正在重新生成…" : "正在生成…", { pending: true });
    const ok = await settleCall(() => api.knowledge_tree(folder(), useRefresh), pending, "生成失败");
    if (ok) {
      knowledgeDone = true;
      setKnowLabel(true);
    }
  }

  function resetKnowledge() {
    knowledgeDone = false;
    setKnowLabel(false);
  }

  function isBusy() { return inflight > 0; }
  function hasKnowledge() { return knowledgeDone; }

  window.AiPanel = {
    init, open, close, toggle, isOpen, resetKnowledge,
    summarizeDoc, summarizeVault, knowledge, isBusy, hasKnowledge,
  };
})();
