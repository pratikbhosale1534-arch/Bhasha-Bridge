/**
 * translator.js — Bhasha Bridge frontend
 * All document/model text is inserted with textContent (never innerHTML) to avoid XSS.
 * No API keys exist in the browser; every AI call goes through the FastAPI backend.
 */
const Translator = (() => {
  const $ = id => document.getElementById(id);
  const S = {
    doc: null,            // {filename, text, sections:[{id,title,text,chars,translated,simplified,tWarn,sWarn,view}]}
    langs: [], status: null, busy: false, stop: false, glossary: [],
    outLang: null,        // target language the stored outputs were produced in
    histIds: {}, viewer: null,
  };

  // ── helpers ──────────────────────────────────────────────────────────────
  const el = (tag, attrs = {}, ...kids) => {
    const n = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs)) {
      if (k === "class") n.className = v; else if (k === "text") n.textContent = v;
      else if (k.startsWith("on")) n.addEventListener(k.slice(2), v); else n.setAttribute(k, v);
    }
    kids.forEach(c => n.append(c));
    return n;
  };
  const langName = c => (S.langs.find(l => l.code === c) || {}).name || c;
  const fmt = b => b < 1048576 ? (b / 1024).toFixed(1) + " KB" : (b / 1048576).toFixed(1) + " MB";

  function showError(msg) { const e = $("trError"); e.textContent = msg; e.classList.remove("hidden"); e.scrollIntoView({ block: "nearest" }); }
  function clearError() { $("trError").classList.add("hidden"); }
  function toast(msg) { const t = el("div", { class: "toast", role: "status", text: msg }); document.body.append(t); setTimeout(() => t.remove(), 3500); }

  async function api(path, opts = {}) {
    let res;
    try { res = await fetch(path, opts); }
    catch (_) { throw new Error("Cannot reach the server. Check your connection and try again."); }
    let data = null;
    try { data = await res.json(); } catch (_) {}
    if (!res.ok) {
      let msg = data && data.error;
      if (!msg && data && Array.isArray(data.detail)) msg = "Invalid input: " + (data.detail[0] && data.detail[0].msg || "please check your request");
      throw new Error(msg || `Request failed (${res.status}).`);
    }
    return data;
  }
  const post = (p, body) => api(p, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });

  // ── init ─────────────────────────────────────────────────────────────────
  async function init() {
    try {
      const [st, lg, sm] = await Promise.all([api("/api/status"), api("/api/languages"), api("/api/samples")]);
      S.status = st; S.langs = lg.languages;
      renderBanner(st);
      $("trLimit").textContent = `up to ${st.limits.max_upload_mb} MB`;
      const mk = (sel, def) => {
        sel.replaceChildren();
        S.langs.forEach(l => sel.append(el("option", { value: l.code, text: l.name + (l.quality === "experimental" ? " (experimental)" : "") })));
        sel.value = def;
      };
      mk($("trSrc"), "en"); mk($("trTgt"), "hi");
      sm.samples.forEach(s => $("trSamples").append(el("button", { type: "button", text: s.title, onclick: () => loadSample(s.name) })));
      updateLangNote(); loadHistory();
    } catch (e) { showError(e.message); $("trAiBanner").textContent = "Could not load app status."; }
    wire();
  }

  function renderBanner(st) {
    const b = $("trAiBanner"), ai = st.ai;
    b.className = "tr-banner " + (ai.mode === "demo" ? "demo" : "ok");
    b.textContent = ai.mode === "demo"
      ? "⚠ " + ai.message + " Set WATSONX_API_KEY and WATSONX_PROJECT_ID (see README) to use IBM Granite."
      : `✔ Using IBM Granite (${ai.model}) via watsonx.ai.`;
  }

  function wire() {
    const drop = $("trDrop"), file = $("trFile");
    drop.addEventListener("click", () => file.click());
    drop.addEventListener("keydown", e => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); file.click(); } });
    ["dragover", "dragenter"].forEach(ev => drop.addEventListener(ev, e => { e.preventDefault(); drop.classList.add("drag-over"); }));
    ["dragleave", "drop"].forEach(ev => drop.addEventListener(ev, e => { e.preventDefault(); drop.classList.remove("drag-over"); }));
    drop.addEventListener("drop", e => { const f = e.dataTransfer.files[0]; if (f) upload(f); });
    file.addEventListener("change", () => { if (file.files[0]) upload(file.files[0]); file.value = ""; });
    $("trSwap").addEventListener("click", () => { const a = $("trSrc"), b = $("trTgt"); [a.value, b.value] = [b.value, a.value]; onLangChange(); });
    $("trSrc").addEventListener("change", onLangChange); $("trTgt").addEventListener("change", onLangChange);
    $("trAll").addEventListener("change", e => document.querySelectorAll(".tr-sec-check").forEach(c => c.checked = e.target.checked));
    $("trRunT").addEventListener("click", () => runSelected("translation"));
    $("trRunS").addEventListener("click", () => runSelected("simplification"));
    $("trRunG").addEventListener("click", buildGlossary);
    $("trStop").addEventListener("click", () => { S.stop = true; $("trStop").disabled = true; });
    $("trDl").addEventListener("click", download);
    $("trHistClear").addEventListener("click", clearHistory);
  }

  function updateLangNote() {
    const n = $("trLangNote"), src = S.langs.find(l => l.code === $("trSrc").value), tgt = S.langs.find(l => l.code === $("trTgt").value);
    const exp = [src, tgt].filter(l => l && l.quality === "experimental").map(l => l.name);
    if (exp.length) {
      n.textContent = `${[...new Set(exp)].join(" and ")} ${exp.length > 1 ? "are" : "is"} not on IBM's official Granite language list. Translations may contain more errors, so please have a fluent speaker review them.`;
      n.classList.remove("hidden");
    } else n.classList.add("hidden");
  }
  function onLangChange() {
    updateLangNote();
    if (S.doc && S.outLang && S.outLang !== $("trTgt").value && S.doc.sections.some(s => s.translated || s.simplified)) {
      S.doc.sections.forEach(s => { s.translated = s.simplified = null; s.tWarn = s.sWarn = []; });
      S.glossary = []; $("trGlossBox").classList.add("hidden"); S.outLang = null; S.histIds = {};
      renderSections(); toast("Target language changed — previous outputs cleared.");
    }
  }

  // ── upload / samples ─────────────────────────────────────────────────────
  async function upload(file) {
    clearError();
    const lim = S.status ? S.status.limits.max_upload_mb * 1048576 : 1e9;
    if (file.size > lim) return showError(`“${file.name}” is ${fmt(file.size)}; the limit is ${S.status.limits.max_upload_mb} MB.`);
    if (!/\.(pdf|docx|pptx|txt)$/i.test(file.name)) return showError("Unsupported file type. Please upload a PDF, DOCX, PPTX or TXT file.");
    const form = new FormData(); form.append("file", file);
    setDocInfo(`Reading “${file.name}”…`, true);
    try { loadDoc(await api("/api/extract", { method: "POST", body: form })); }
    catch (e) { $("trDocInfo").classList.add("hidden"); showError(e.message); }
  }
  async function loadSample(name) {
    clearError(); setDocInfo("Loading sample…", true);
    try { loadDoc(await api("/api/samples/" + encodeURIComponent(name))); }
    catch (e) { $("trDocInfo").classList.add("hidden"); showError(e.message); }
  }
  function setDocInfo(text, show) { const d = $("trDocInfo"); d.textContent = text; d.classList.toggle("hidden", !show); }

  function loadDoc(d) {
    S.doc = { filename: d.filename, text: d.text, sections: d.sections.map(s => ({ ...s, translated: null, simplified: null, tWarn: [], sWarn: [], view: "translation" })) };
    S.glossary = []; S.outLang = null; S.histIds = {};
    $("trGlossBox").classList.add("hidden");
    const info = $("trDocInfo"); info.replaceChildren(
      el("strong", { text: d.filename }),
      document.createTextNode(` · ${d.file_type.toUpperCase()} · ${d.chars.toLocaleString()} characters${d.pages ? " · " + d.pages + (d.file_type === "pptx" ? " slides" : " pages") : ""} · ${d.sections.length} section(s)`));
    (d.warnings || []).forEach(w => info.append(el("div", { class: "tr-warn", text: "⚠ " + w })));
    info.classList.remove("hidden");
    $("trWork").classList.remove("hidden"); $("trAll").checked = true;
    renderSections();
    $("trWork").scrollIntoView({ behavior: "smooth", block: "start" });
  }

  // ── rendering ────────────────────────────────────────────────────────────
  function renderSections() {
    const box = $("trSections"); box.replaceChildren();
    S.doc.sections.forEach((s, i) => {
      const chk = el("input", { type: "checkbox", class: "tr-sec-check", checked: "", "aria-label": "Select section " + s.title });
      chk.dataset.i = i;
      const btnT = el("button", { class: "tr-mini", type: "button", text: "Translate", onclick: () => runOne(i, "translation") });
      const btnS = el("button", { class: "tr-mini", type: "button", text: "Simplify", title: "AI explanation — not a translation", onclick: () => runOne(i, "simplification") });
      const head = el("header", {}, el("label", { class: "tr-check" }, chk, el("strong", { text: `${i + 1}. ${s.title}` })), el("div", {}, btnT, document.createTextNode(" "), btnS));
      const orig = el("div", { class: "tr-col" },
        el("h4", {}, document.createTextNode(`Original (${langName($("trSrc").value)})`)),
        el("div", { class: "tr-text", lang: $("trSrc").value, text: s.text }));
      const out = el("div", { class: "tr-col" }); out.id = "trOut" + i;
      box.append(el("article", { class: "tr-sec", id: "trSec" + i }, head, el("div", { class: "tr-cols" }, orig, out)));
      renderOut(i);
    });
  }

  function renderOut(i) {
    const s = S.doc.sections[i], out = $("trOut" + i); if (!out) return;
    out.replaceChildren();
    const view = s.view;
    const tabs = el("span", { class: "tr-tabs" },
      ...[["translation", "Translation"], ["simplification", "Simplified"]].map(([k, label]) =>
        el("button", { type: "button", "aria-pressed": String(view === k), text: label, onclick: () => { s.view = k; renderOut(i); } })));
    const tgt = S.outLang || $("trTgt").value;
    const badge = view === "translation" ? el("span", { class: "badge", text: "AI translation" }) : el("span", { class: "badge exp", text: "AI simplification — not a translation" });
    out.append(el("h4", {}, document.createTextNode(`${langName(tgt)} `), badge, tabs));
    const txt = view === "translation" ? s.translated : s.simplified;
    out.append(el("div", { class: "tr-text" + (txt ? "" : " empty"), lang: tgt, text: txt || (view === "translation" ? "Not translated yet." : "Not simplified yet.") }));
    (view === "translation" ? s.tWarn : s.sWarn).forEach(w => out.append(el("div", { class: "tr-warn", text: "⚠ " + w })));
    if (txt) out.append(el("div", { class: "tr-kind", text: "Review recommended: AI output can contain mistakes." }));
  }

  // ── running AI jobs ──────────────────────────────────────────────────────
  function setBusy(b) {
    S.busy = b; S.stop = false;
    ["trRunT", "trRunS", "trRunG", "trDl"].forEach(id => $(id).disabled = b);
    document.querySelectorAll(".tr-mini").forEach(x => x.disabled = b);
    $("trStop").classList.toggle("hidden", !b); $("trStop").disabled = false;
    if (!b) $("trProg").classList.add("hidden");
  }
  function progress(done, total, label) {
    $("trProg").classList.remove("hidden");
    $("trProgFill").style.width = Math.round((done / total) * 100) + "%";
    $("trProgText").textContent = `${label} ${Math.min(done + 1, total)} of ${total}…`;
  }

  async function processSection(i, kind) {
    const s = S.doc.sections[i], src = $("trSrc").value, tgt = $("trTgt").value;
    const body = { text: s.text, source_lang: src, target_lang: tgt, level: $("trLevel").value };
    const r = await post(kind === "translation" ? "/api/translate" : "/api/simplify", body);
    if (kind === "translation") { s.translated = r.text; s.tWarn = r.warnings; } else { s.simplified = r.text; s.sWarn = r.warnings; }
    s.view = kind; S.outLang = tgt; S.provider = r.provider; S.model = r.model;
    renderOut(i);
  }

  async function runOne(i, kind) { await runList([i], kind); }
  async function runSelected(kind) {
    const idx = [...document.querySelectorAll(".tr-sec-check")].filter(c => c.checked).map(c => +c.dataset.i);
    if (!idx.length) return showError("Select at least one section first.");
    await runList(idx, kind);
  }
  async function runList(idx, kind) {
    if (S.busy) return;
    clearError();
    if (kind === "translation" && $("trSrc").value === $("trTgt").value) return showError("Source and target languages are the same. Pick a different target language.");
    S.outLang = $("trTgt").value;
    setBusy(true);
    let ok = 0;
    try {
      for (let n = 0; n < idx.length; n++) {
        if (S.stop) { showError(`Stopped. ${ok} of ${idx.length} section(s) finished.`); break; }
        progress(n, idx.length, kind === "translation" ? "Translating section" : "Simplifying section");
        await processSection(idx[n], kind); ok++;
      }
    } catch (e) {
      showError(`${e.message}${ok ? ` (${ok} of ${idx.length} section(s) finished and are kept.)` : ""}`);
    } finally { setBusy(false); }
    if (ok) await saveHistory(kind);
  }

  async function buildGlossary() {
    if (S.busy || !S.doc) return; clearError();
    const picked = [...document.querySelectorAll(".tr-sec-check")].filter(c => c.checked).map(c => S.doc.sections[+c.dataset.i].text);
    const text = (picked.length ? picked : [S.doc.text]).join("\n\n").slice(0, 20000);
    setBusy(true); $("trProg").classList.remove("hidden"); $("trProgFill").style.width = "50%"; $("trProgText").textContent = "Building glossary…";
    try {
      const r = await post("/api/glossary", { text, target_lang: $("trTgt").value, max_terms: 12 });
      S.glossary = r.terms;
      renderGlossary(r);
    } catch (e) { showError(e.message); } finally { setBusy(false); }
  }
  function renderGlossary(r) {
    const box = $("trGloss"); box.replaceChildren();
    $("trGlossBox").classList.remove("hidden");
    if (!r.terms.length) { box.append(el("p", { class: "tr-muted", text: (r.warnings && r.warnings[0]) || "No glossary terms were found." })); return; }
    const t = el("table", {}, el("thead", {}, el("tr", {}, ...["Term", "Translation", "Definition"].map(h => el("th", { text: h })))));
    const tb = el("tbody");
    r.terms.forEach(x => tb.append(el("tr", {}, el("td", { text: x.term + (x.in_source ? "" : " ⚠") }), el("td", { text: x.translation, lang: $("trTgt").value }), el("td", { text: x.definition, lang: $("trTgt").value }))));
    t.append(tb); box.append(el("div", { class: "tr-table-wrap" }, t));
    (r.warnings || []).forEach(w => box.append(el("div", { class: "tr-warn", text: "⚠ " + w })));
  }

  // ── download ─────────────────────────────────────────────────────────────
  function collect(kind) {
    return S.doc.sections.map(s => (kind === "translation" ? s.translated : s.simplified)).filter(Boolean).join("\n\n");
  }
  async function download() {
    clearError();
    const kind = $("trDlWhat").value;
    let text = S.viewer && !S.doc ? S.viewer.text : (S.doc ? collect(kind) : "");
    if (!text) return showError(`There is no ${kind === "translation" ? "translation" : "simplified text"} to download yet. Run it on at least one section first.`);
    await doExport({ title: (S.doc ? S.doc.filename.replace(/\.[^.]+$/, "") : "bhasha") + "_" + $("trTgt").value, text, kind, fmt: $("trDlFmt").value, target: S.outLang || $("trTgt").value, gloss: $("trDlGloss").checked ? S.glossary : [] });
  }
  async function doExport({ title, text, kind, fmt, target, gloss }) {
    try {
      const res = await fetch("/api/export", { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ title, translated_text: text, format: fmt, kind, target_lang: target, glossary: gloss || [] }) });
      if (!res.ok) { const j = await res.json().catch(() => ({})); throw new Error(j.error || "Export failed."); }
      const blob = await res.blob(), a = el("a", { href: URL.createObjectURL(blob), download: (res.headers.get("content-disposition") || "").match(/filename="([^"]+)"/)?.[1] || `${title}.${fmt}` });
      document.body.append(a); a.click(); a.remove(); setTimeout(() => URL.revokeObjectURL(a.href), 2000);
    } catch (e) { showError(e.message); }
  }

  // ── history ──────────────────────────────────────────────────────────────
  async function saveHistory(kind) {
    if (!S.doc) return;
    const text = collect(kind); if (!text) return;
    try {
      const key = kind + S.outLang;
      if (S.histIds[key]) await api("/api/history/" + S.histIds[key], { method: "DELETE" }).catch(() => {});
      const r = await post("/api/history", { filename: S.doc.filename, kind, source_lang: $("trSrc").value, target_lang: S.outLang, provider: S.provider || "demo", model: S.model || null, translated_text: text });
      S.histIds[key] = r.id; loadHistory();
    } catch (_) { /* history is best-effort */ }
  }
  async function loadHistory() {
    try {
      const r = await api("/api/history"), box = $("trHist"); box.replaceChildren();
      $("trHistNote").textContent = `Only translated/simplified output is kept (never your original file) and is deleted automatically after ${r.retention_days} days.` + (S.status && S.status.history_persistent === false ? " On this hosting, history is temporary and may disappear at any time." : "");
      if (!r.items.length) box.append(el("p", { class: "tr-muted", text: "No saved documents yet." }));
      r.items.forEach(h => box.append(el("div", { class: "tr-hist-item" },
        el("span", {}, el("strong", { text: h.filename }), document.createTextNode(` · ${h.kind === "translation" ? "Translation" : "Simplified"} → ${langName(h.target_lang)} · ${new Date(h.created_at).toLocaleString()}${h.provider === "demo" ? " · DEMO" : ""}`)),
        el("span", {}, el("button", { class: "tr-mini", type: "button", text: "Open", onclick: () => openHistory(h.id) }), document.createTextNode(" "),
          el("button", { class: "tr-mini", type: "button", text: "Delete", onclick: () => delHistory(h.id) })))));
    } catch (_) {}
  }
  async function openHistory(id) {
    try {
      const h = await api("/api/history/" + id), v = $("trHistView"); v.replaceChildren(); v.classList.remove("hidden");
      S.viewer = { text: h.translated_text };
      const fmtSel = el("select", { class: "form-select", id: "trHvFmt" }, ...[["docx", "Word"], ["md", "Markdown"], ["txt", "Text"], ["html", "HTML"]].map(([k, l]) => el("option", { value: k, text: l })));
      v.append(el("h3", { text: `${h.filename} — ${h.kind === "translation" ? "AI translation" : "AI simplification"} (${langName(h.target_lang)})` }),
        el("div", { class: "tr-text", lang: h.target_lang, text: h.translated_text }),
        el("div", { class: "tr-row", style: "margin-top:10px" }, fmtSel,
          el("button", { class: "btn-secondary", type: "button", text: "Download", onclick: () => doExport({ title: h.filename.replace(/\.[^.]+$/, "") + "_" + h.target_lang, text: h.translated_text, kind: h.kind, fmt: fmtSel.value, target: h.target_lang }) }),
          el("button", { class: "btn-secondary", type: "button", text: "Close", onclick: () => { v.classList.add("hidden"); S.viewer = null; } })),
        el("p", { class: "tr-muted", text: "The original document is not stored, so only the output is shown." }));
      v.scrollIntoView({ block: "nearest", behavior: "smooth" });
    } catch (e) { showError(e.message); loadHistory(); }
  }
  async function delHistory(id) { try { await api("/api/history/" + id, { method: "DELETE" }); $("trHistView").classList.add("hidden"); loadHistory(); } catch (e) { showError(e.message); } }
  async function clearHistory() {
    if (!confirm("Delete all saved history items?")) return;
    try { await api("/api/history", { method: "DELETE" }); S.histIds = {}; $("trHistView").classList.add("hidden"); loadHistory(); } catch (e) { showError(e.message); }
  }

  document.addEventListener("DOMContentLoaded", init);
  return { init, _state: S };
})();
