/* Level library shell: the sidebar, saving, batch import/export and AI.
   The tree editing itself lives in designer.js (window.Designer). */

(function () {
"use strict";

const $ = id => document.getElementById(id);
const CSRF = document.querySelector('meta[name="csrf-token"]').content;

const state = {
  levels: [],          // summaries from the server
  currentId: null,
  selected: new Set(), // ids ticked for batch export
  dirty: false,
  filter: "",
  aiUndo: null,        // the tree as it was before the last AI edit
};

/* ============================ helpers ============================ */

async function api(url, options = {}) {
  const res = await fetch(url, {
    headers: { "Content-Type": "application/json", "X-CSRFToken": CSRF },
    ...options,
  });
  const text = await res.text();
  let data = null;
  try { data = text ? JSON.parse(text) : null; } catch { /* an error page, not JSON */ }
  if (!res.ok) throw new Error((data && data.error) || `${res.status} ${res.statusText}`);
  return data;
}

function toast(message, kind = "info") {
  const stack = $("flash-stack");
  const node = document.createElement("div");
  node.className =
    "rounded-xl px-4 py-2.5 text-sm font-medium shadow-lg " +
    (kind === "error" ? "bg-danger text-white"
      : kind === "success" ? "bg-emerald-600 text-white" : "bg-ink text-white");
  node.textContent = message;
  stack.appendChild(node);
  setTimeout(() => node.remove(), 4000);
}

const meta = () => ({
  number: +$("in-level").value || 1,
  name: $("in-name").value.trim(),
  moves: +$("in-moves").value || 1,
  coins: +$("in-coins").value || 0,
  status: $("in-status").value,
});

function setMeta(level) {
  $("in-level").value = level.number;
  $("in-name").value = level.name || "";
  $("in-moves").value = level.moves;
  $("in-coins").value = level.coins;
  $("in-status").value = level.status || "draft";
}

function markDirty(dirty = true) {
  state.dirty = dirty;
  const badge = $("save-state");
  badge.className = "saving-badge" + (dirty ? " dirty" : " saved");
  badge.textContent = !dirty ? "saved"
    : state.currentId === null ? "new level — not saved yet" : "unsaved changes";
  $("btn-save").disabled = !dirty;
}

/* ============================ sidebar ============================ */

function renderList() {
  const list = $("level-list");
  list.innerHTML = "";

  const q = state.filter.toLowerCase();
  const visible = state.levels.filter(lv =>
    !q || String(lv.number).includes(q) || (lv.name || "").toLowerCase().includes(q));

  if (!visible.length) {
    list.appendChild(Object.assign(document.createElement("div"), {
      className: "empty-hint",
      textContent: state.levels.length ? "No level matches that search."
        : "No levels yet.\nHit + New to create your first one.",
    }));
  }

  visible.forEach(lv => {
    const item = document.createElement("div");
    item.className = "level-item" + (lv.id === state.currentId ? " active" : "");
    item.tabIndex = 0;
    item.dataset.id = lv.id;

    const box = document.createElement("input");
    box.type = "checkbox";
    box.checked = state.selected.has(lv.id);
    box.title = "Select for batch export";
    box.onclick = e => {
      e.stopPropagation();
      box.checked ? state.selected.add(lv.id) : state.selected.delete(lv.id);
      renderSelectionFooter();
    };

    const num = document.createElement("span");
    num.className = "num";
    num.textContent = lv.number;

    const info = document.createElement("div");
    info.className = "info";
    const title = document.createElement("div");
    title.className = "title";
    title.textContent = lv.name || `Level ${lv.number}`;
    const sub = document.createElement("div");
    sub.className = "sub";
    sub.textContent = `${lv.words} words · ${lv.hidden} to place`;
    info.append(title, sub);

    const dot = document.createElement("span");
    dot.className = "state" + (lv.status === "published" ? " published" : "");
    dot.title = lv.status;

    item.append(box, num, info, dot);
    item.onclick = () => switchTo(lv.id);
    item.onkeydown = e => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); switchTo(lv.id); } };
    list.appendChild(item);
  });

  renderSelectionFooter();
}

function renderSelectionFooter() {
  const n = state.selected.size;
  $("sel-count").textContent = n ? `${n} selected` : "select";
  $("sel-all").checked = n > 0 && n === state.levels.length;
  $("sel-all").indeterminate = n > 0 && n < state.levels.length;
}

async function refreshList() {
  const { levels } = await api("/api/levels");
  state.levels = levels;
  state.selected.forEach(id => { if (!levels.some(l => l.id === id)) state.selected.delete(id); });
  renderList();
}

/* ============================ level switching ============================ */

async function confirmDiscard() {
  if (!state.dirty) return true;
  const answer = confirm("This level has unsaved changes.\n\nOK = save and continue, Cancel = stay here.");
  if (!answer) return false;
  return await save();
}

function clearAiUndo() {
  state.aiUndo = null;
  $("btn-ai-undo").classList.add("hiddenel");
}

async function switchTo(id, { force = false } = {}) {
  if (id === state.currentId && !force) return;
  if (!force && !(await confirmDiscard())) return;
  clearAiUndo();      // it belongs to the level we are leaving

  const level = await api(`/api/levels/${id}`);
  state.currentId = level.id;
  setMeta(level);
  Designer.setTree(level.tree);
  Designer.fitZoom();
  markDirty(false);
  renderList();
  history.replaceState(null, "", `/?level=${level.id}`);
}

function neighbour(delta) {
  const idx = state.levels.findIndex(l => l.id === state.currentId);
  const next = state.levels[(idx < 0 ? 0 : idx) + delta];
  if (next) switchTo(next.id);
}

/* ============================ saving ============================ */

async function save() {
  const body = { ...meta(), tree: Designer.getTree() };
  try {
    if (state.currentId === null) {
      const level = await api("/api/levels", { method: "POST", body: JSON.stringify(body) });
      state.currentId = level.id;
      setMeta(level);
      history.replaceState(null, "", `/?level=${level.id}`);
    } else {
      await api(`/api/levels/${state.currentId}`, { method: "PUT", body: JSON.stringify(body) });
    }
    markDirty(false);
    await refreshList();
    toast("Level saved.", "success");
    return true;
  } catch (err) {
    toast(err.message, "error");
    return false;
  }
}

async function newLevel() {
  if (!(await confirmDiscard())) return;
  const highest = state.levels.reduce((a, l) => Math.max(a, l.number), 0);
  state.currentId = null;
  setMeta({ number: highest + 1, name: "", moves: 30, coins: 100, status: "draft" });
  Designer.setTree(Designer.emptyTree());
  Designer.fitZoom();
  markDirty(true);
  renderList();
  history.replaceState(null, "", "/");
  document.querySelector(".rows .word-in")?.focus();
}

/* ============================ export / import ============================ */

function currentLevelJson() {
  const m = meta();
  const tree = Designer.getTree();
  return JSON.stringify({
    format: "wordtree.levels.v1",
    levels: [{
      level: m.number, name: m.name, moves: m.moves, coins: m.coins, status: m.status,
      hiddenCount: Designer.stats().hidden, tree,
    }],
  }, null, 2);
}

/** The tree as CSV: one row per word, in the column matching its depth.
    Same shape the level spreadsheets use — CRLF, no trailing newline. */
function treeToCsv(tree) {
  const rows = [];
  let width = 1;
  (function walk(node, depth) {
    if (!node) return;
    width = Math.max(width, depth + 1);
    rows.push([depth, (node.word || "").trim()]);
    (node.children || []).forEach(c => walk(c, depth + 1));
  })(tree, 0);

  const cell = v => (/[",\r\n]/.test(v) ? '"' + v.replace(/"/g, '""') + '"' : v);
  return rows.map(([depth, word]) => {
    const cols = new Array(width).fill("");
    cols[depth] = cell(word);
    return cols.join(",");
  }).join("\r\n");
}

function csvFilename() {
  const m = meta();
  const name = (m.name || "").trim().replace(/[^\w -]+/g, "").trim();
  return (name ? name.toUpperCase().replace(/\s+/g, "_") : `LEVEL_${m.number}`) + ".csv";
}

async function fillExport() {
  const scope = $("export-scope").value;
  const csv = $("export-format").value === "csv";
  const box = $("export-text");

  // a CSV carries a single tree and no level metadata, so it is current-level only
  $("export-scope").disabled = csv;
  $("export-note").classList.toggle("hiddenel", !csv);
  $("btn-download").textContent = csv ? "Download .csv" : "Download .json";
  if (csv) {
    $("export-scope").value = "current";
    box.value = treeToCsv(Designer.getTree());
    return;
  }
  if (scope === "current") {
    box.value = currentLevelJson();
    return;
  }
  const ids = scope === "selected" ? [...state.selected] : [];
  if (scope === "selected" && !ids.length) {
    box.value = "";
    toast("Tick some levels in the sidebar first.", "error");
    return;
  }
  box.value = "loading…";
  const res = await fetch("/api/levels/export" + (ids.length ? `?ids=${ids.join(",")}` : ""));
  box.value = await res.text();
}

function openExport(scope) {
  // CSV is single-tree; a batch export has to fall back to JSON
  if (scope !== "current" && $("export-format").value === "csv") $("export-format").value = "json";
  $("export-scope").value = scope;
  $("ov-export").classList.remove("hiddenel");
  fillExport();
}

function download(text, filename, type = "application/json") {
  const blob = new Blob([text], { type });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = filename;
  a.click();
  URL.revokeObjectURL(a.href);
}

async function importToLibrary() {
  let payload;
  try {
    payload = JSON.parse($("import-text").value);
  } catch (err) {
    $("import-err").textContent = "Invalid JSON: " + err.message;
    return;
  }
  const levels = Array.isArray(payload) ? payload : (payload.levels || [payload]);
  try {
    const res = await api(`/api/levels/import?mode=${$("import-mode").value}`,
      { method: "POST", body: JSON.stringify({ levels }) });
    $("ov-import").classList.add("hiddenel");
    $("import-text").value = "";
    $("import-err").textContent = "";
    await refreshList();
    const parts = [];
    if (res.created.length) parts.push(`${res.created.length} created`);
    if (res.updated.length) parts.push(`${res.updated.length} updated`);
    if (res.skipped.length) parts.push(`${res.skipped.length} skipped`);
    if (res.errors.length) parts.push(`${res.errors.length} failed`);
    toast("Import: " + (parts.join(", ") || "nothing to do"),
      res.errors.length ? "error" : "success");
    if (res.errors.length) console.warn("Import errors", res.errors);
  } catch (err) {
    $("import-err").textContent = err.message;
  }
}

function importIntoEditor() {
  let payload;
  try {
    payload = JSON.parse($("import-text").value);
  } catch (err) {
    $("import-err").textContent = "Invalid JSON: " + err.message;
    return;
  }
  const first = Array.isArray(payload) ? payload[0] : (payload.levels ? payload.levels[0] : payload);
  if (!first || !first.tree) {
    $("import-err").textContent = "No level with a `tree` found in that JSON.";
    return;
  }
  state.currentId = null;
  setMeta({
    number: first.level || first.number || 1,
    name: first.name || "",
    moves: first.moves || 30,
    coins: first.coins || 0,
    status: first.status || "draft",
  });
  Designer.setTree(first.tree);
  Designer.fitZoom();
  markDirty(true);
  $("ov-import").classList.add("hiddenel");
  $("import-err").textContent = "";
  renderList();
  toast("Loaded into the editor — hit Save to add it to the library.");
}

/* ============================ AI ============================ */

/** The AI command bar: an instruction in, an updated tree out. It never answers
    in words — the only visible result is the tree changing. */
async function runTreeEdit(event) {
  event.preventDefault();
  const instruction = $("ai-cmd").value.trim();
  if (!instruction) return;

  const bar = $("ai-bar"), btn = $("btn-ai-apply"), input = $("ai-cmd");
  const before = Designer.getTree();     // one step back, in case the edit guts the tree
  bar.classList.add("busy");
  btn.disabled = input.disabled = true;
  btn.textContent = "Applying…";
  try {
    const { tree, duplicates } = await api("/api/ai/edit-tree", {
      method: "POST",
      body: JSON.stringify({
        instruction,
        tree: Designer.getTree(),
        topic: meta().name || "",
      }),
    });
    const wordsBefore = Designer.stats().words;
    Designer.applyTree(tree);
    Designer.fitZoom();
    markDirty(true);
    input.value = "";
    state.aiUndo = before;
    $("btn-ai-undo").classList.remove("hiddenel");
    const s = Designer.stats();
    const lost = wordsBefore - s.words;
    toast(`Tree updated — ${s.words} words, ${s.hidden} to place.`, "success");
    if (lost >= 3) {
      toast(`That removed ${lost} words. Undo is next to Apply.`, "error");
    }
    if (duplicates && duplicates.length) {
      toast("Repeated words to fix: " + duplicates.join(", "), "error");
    }
  } catch (err) {
    toast(err.message, "error");
  } finally {
    bar.classList.remove("busy");
    btn.disabled = input.disabled = false;
    btn.textContent = "Apply";
    input.focus();
  }
}

async function runGenerate() {
  const topic = $("ai-topic").value.trim();
  if (!topic) { $("ai-err").textContent = "Give it a topic first."; return; }
  const btn = $("btn-ai-run");
  btn.disabled = true;
  btn.innerHTML = '<span class="spin">◠</span> Generating…';
  try {
    const { tree } = await api("/api/ai/generate-tree", {
      method: "POST",
      body: JSON.stringify({
        topic,
        breadth: +$("ai-breadth").value,
        depth: +$("ai-depth").value,
        hideFromDepth: +$("ai-hide").value,
      }),
    });
    Designer.setTree(tree);
    Designer.fitZoom();
    if (!$("in-name").value.trim()) $("in-name").value = topic;
    markDirty(true);
    $("ov-ai").classList.add("hiddenel");
    $("ai-err").textContent = "";
  } catch (err) {
    $("ai-err").textContent = err.message;
  } finally {
    btn.disabled = false;
    btn.textContent = "Generate";
  }
}

Designer.onSuggest(async (node, path, avoid) => {
  const word = (node.word || "").trim();
  if (!word) { toast("Give the node a word before asking for suggestions.", "error"); return []; }
  try {
    const { words } = await api("/api/ai/suggest-children", {
      method: "POST",
      body: JSON.stringify({ word, path, avoid, count: 4 }),
    });
    return words;
  } catch (err) {
    toast(err.message, "error");
    return [];
  }
});

Designer.onRegenerate(async (node, { keepWord, path, shape, avoid }) => {
  try {
    const { node: fresh } = await api("/api/ai/regenerate-branch", {
      method: "POST",
      // an unnamed node is filled in from its parent, or from the level name
      // when it is the root — no word needed to press ↻
      body: JSON.stringify({
        word: (node.word || "").trim(), path, shape, avoid, keepWord,
        topic: meta().name || "",
      }),
    });
    return fresh;
  } catch (err) {
    toast(err.message, "error");
    return null;
  }
});

/* ============================ collapsible panes ============================ */

const PANES = {
  levels: { pane: "pane-levels", rail: "rail-levels", btn: "btn-collapse-levels", label: "levels" },
  structure: { pane: "pane-structure", rail: "rail-structure", btn: "btn-collapse-structure", label: "structure" },
};

function setPane(key, collapsed) {
  const { pane, btn, label } = PANES[key];
  $(pane).classList.toggle("collapsed", collapsed);
  $(btn).title = `${collapsed ? "Show" : "Hide"} ${label}`;
  localStorage.setItem(`wordtree.collapsed.${key}`, collapsed ? "1" : "0");
  Designer.fitZoom();
}

Object.entries(PANES).forEach(([key, { pane, rail, btn }]) => {
  const toggle = () => setPane(key, !$(pane).classList.contains("collapsed"));
  $(btn).onclick = toggle;
  $(rail).onclick = toggle;
  setPane(key, localStorage.getItem(`wordtree.collapsed.${key}`) === "1");
});

/* ============================ wiring ============================ */

Designer.onChange(() => markDirty(true));

["in-level", "in-name", "in-moves", "in-coins", "in-status"].forEach(id => {
  $(id).addEventListener("input", () => { Designer.renderPreview(); markDirty(true); });
});

$("btn-save").onclick = save;
$("btn-new").onclick = newLevel;
$("level-search").oninput = e => { state.filter = e.target.value.trim(); renderList(); };

$("sel-all").onchange = e => {
  state.selected = e.target.checked ? new Set(state.levels.map(l => l.id)) : new Set();
  renderList();
};

$("btn-export").onclick = () => openExport("current");
$("btn-export-selected").onclick = () => openExport(state.selected.size ? "selected" : "all");
$("export-scope").onchange = fillExport;
$("export-format").onchange = fillExport;
$("btn-export-close").onclick = () => $("ov-export").classList.add("hiddenel");
$("btn-copy").onclick = async () => {
  try {
    await navigator.clipboard.writeText($("export-text").value);
    $("btn-copy").textContent = "Copied ✓";
    setTimeout(() => ($("btn-copy").textContent = "Copy"), 1500);
  } catch { $("export-text").select(); }
};
$("btn-download").onclick = () => {
  if ($("export-format").value === "csv") {
    download($("export-text").value, csvFilename(), "text/csv;charset=utf-8");
    return;
  }
  const scope = $("export-scope").value;
  const name = scope === "current" ? `level_${meta().number}.json` : "wordtree-levels.json";
  download($("export-text").value, name);
};

$("btn-import").onclick = () => {
  $("import-err").textContent = "";
  $("ov-import").classList.remove("hiddenel");
};
$("btn-pick-file").onclick = () => $("file-input").click();
$("file-input").onchange = async e => {
  const files = [...(e.target.files || [])];
  if (!files.length) return;
  const parsed = [];
  for (const file of files) {
    try {
      const data = JSON.parse(await file.text());
      parsed.push(...(Array.isArray(data) ? data : (data.levels || [data])));
    } catch (err) {
      $("import-err").textContent = `${file.name}: ${err.message}`;
    }
  }
  $("import-text").value = JSON.stringify({ levels: parsed }, null, 2);
  e.target.value = "";
};
$("btn-import-cancel").onclick = () => $("ov-import").classList.add("hiddenel");
$("btn-import-save").onclick = importToLibrary;
$("btn-import-editor").onclick = importIntoEditor;

$("btn-ai").onclick = () => {
  $("ai-err").textContent = "";
  $("ov-ai").classList.remove("hiddenel");
  $("ai-topic").focus();
};
$("btn-ai-cancel").onclick = () => $("ov-ai").classList.add("hiddenel");
$("btn-ai-run").onclick = runGenerate;

$("ai-bar").addEventListener("submit", runTreeEdit);
$("btn-ai-undo").onclick = () => {
  if (!state.aiUndo) return;
  Designer.applyTree(state.aiUndo);
  Designer.fitZoom();
  markDirty(true);
  clearAiUndo();
  toast("Reverted the AI edit.");
};

$("btn-menu").onclick = e => {
  e.stopPropagation();
  $("menu").classList.toggle("hiddenel");
};
document.addEventListener("click", () => $("menu").classList.add("hiddenel"));

$("mi-duplicate").onclick = async () => {
  if (state.currentId === null) { toast("Save this level first.", "error"); return; }
  const copy = await api(`/api/levels/duplicate/${state.currentId}`, { method: "POST" });
  await refreshList();
  markDirty(false);
  switchTo(copy.id, { force: true });
};
$("mi-delete").onclick = async () => {
  if (state.currentId === null) { toast("This level isn't saved yet.", "error"); return; }
  const lv = state.levels.find(l => l.id === state.currentId);
  if (!confirm(`Delete "${lv ? lv.name || lv.number : state.currentId}"? This cannot be undone.`)) return;
  await api(`/api/levels/${state.currentId}`, { method: "DELETE" });
  state.currentId = null;
  markDirty(false);
  await refreshList();
  if (state.levels.length) switchTo(state.levels[0].id, { force: true });
  else newLevel();
  toast("Level deleted.", "success");
};

[$("ov-export"), $("ov-import"), $("ov-ai")].forEach(ov =>
  ov.addEventListener("click", e => { if (e.target === ov) ov.classList.add("hiddenel"); }));

document.addEventListener("keydown", e => {
  const typing = /^(INPUT|TEXTAREA|SELECT)$/.test(document.activeElement.tagName);
  if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "s") { e.preventDefault(); save(); return; }
  if (e.key === "Escape") {
    document.querySelectorAll(".overlay").forEach(o => o.classList.add("hiddenel"));
    return;
  }
  if (e.altKey && (e.key === "ArrowUp" || e.key === "ArrowDown")) {
    e.preventDefault();
    neighbour(e.key === "ArrowUp" ? -1 : 1);
    return;
  }
  if (e.altKey && (e.code === "Digit1" || e.code === "Digit2")) {
    e.preventDefault();
    const key = e.code === "Digit1" ? "levels" : "structure";
    setPane(key, !$(PANES[key].pane).classList.contains("collapsed"));
    return;
  }
  if (e.key === "/" && !typing) { e.preventDefault(); $("level-search").focus(); }
});

window.addEventListener("beforeunload", e => {
  if (state.dirty) { e.preventDefault(); e.returnValue = ""; }
});

/* ============================ boot ============================ */

(async function boot() {
  await refreshList();

  const wanted = +new URLSearchParams(location.search).get("level");
  const target = state.levels.find(l => l.id === wanted) || state.levels[0];
  if (target) await switchTo(target.id, { force: true });
  else newLevel();

  api("/api/ai/status").then(({ configured, model }) => {
    if (!configured) {
      $("btn-ai").title = "Set OPENAI_API_KEY on the server to enable AI generation";
      $("btn-ai").style.opacity = ".55";
      $("ai-cmd").placeholder = "Set OPENAI_API_KEY on the server to use AI editing";
      $("ai-cmd").disabled = $("btn-ai-apply").disabled = true;
      return;
    }
    $("ai-cmd").title = "Runs on " + model;
  });
})();

})();
