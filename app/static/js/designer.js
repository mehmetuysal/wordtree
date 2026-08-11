/* Word Tree level designer — the tree editor + preview, ported from the
   standalone prototype. It owns everything inside the "Structure" and
   "Preview" panels and knows nothing about the server; app.js wires it to the
   level library through the window.Designer API at the bottom of this file. */

/* ============================ state ============================ */

let __id = 1;
const nid = () => "n" + (__id++);
const N = (word, hidden, children = []) => ({ id: nid(), word, hidden, ox: 0, oy: 0, children });

let tree = N("", false);

let playerView = false;
let zoom = 0.85;
const collapsedSet = new Set();

let onChange = () => {};          // set by app.js — fires on any tree/meta edit
let onSuggest = null;             // set by app.js — async (node, path) => string[]
let onRegenerate = null;          // set by app.js — async (node, opts) => {word, children}

/* ============================ tree utils ============================ */

function findNode(n, id) {
  if (n.id === id) return n;
  for (const c of n.children) { const r = findNode(c, id); if (r) return r; }
  return null;
}
function findParent(n, id, parent = null) {
  if (n.id === id) return parent;
  for (const c of n.children) { const r = findParent(c, id, n); if (r) return r; }
  return null;
}
function pathTo(n, id, acc = []) {
  const here = acc.concat(n.word || "?");
  if (n.id === id) return here;
  for (const c of n.children) { const r = pathTo(c, id, here); if (r) return r; }
  return null;
}
function countNodes(n) { return 1 + n.children.reduce((a, c) => a + countNodes(c), 0); }
function collectHidden(n, out = []) {
  if (n.hidden) out.push(n.word || "?");
  n.children.forEach(c => collectHidden(c, out));
  return out;
}
function collectWords(n, out = []) {
  if ((n.word || "").trim()) out.push(n.word.trim());
  n.children.forEach(c => collectWords(c, out));
  return out;
}
function collectWordsExcept(n, skipId, out = []) {
  if (n.id === skipId) return out;
  if ((n.word || "").trim()) out.push(n.word.trim());
  n.children.forEach(c => collectWordsExcept(c, skipId, out));
  return out;
}
/** The subtree reduced to its nesting only — what the AI must match. */
function shapeOf(n) {
  return n.children.length ? { children: n.children.map(shapeOf) } : {};
}
/** Copy generated words onto the existing nodes, keeping ids/flags/offsets. */
function applyBranch(n, gen) {
  // an empty word means the model could only offer a duplicate — leave the slot
  // blank so the warning bar picks it up instead of silently keeping a clash
  n.word = (gen.word || "").trim().toUpperCase();
  (gen.children || []).forEach((g, i) => { if (n.children[i]) applyBranch(n.children[i], g); });
}
function maxDepth(n, d = 1) {
  if (!n.children.length) return d;
  return Math.max(...n.children.map(c => maxDepth(c, d + 1)));
}
/** Words used more than once in the tree — the puzzle is unsolvable with those. */
function duplicateWords(n, seen = new Set(), dupes = new Set()) {
  const w = (n.word || "").trim().toUpperCase();
  if (w) (seen.has(w) ? dupes : seen).add(w);
  n.children.forEach(c => duplicateWords(c, seen, dupes));
  return dupes;
}
function countEmpty(n) {
  let c = (n.word || "").trim() ? 0 : 1;
  n.children.forEach(k => c += countEmpty(k));
  return c;
}
function stripIds(n) {
  const o = { word: n.word, hidden: !!n.hidden };
  if (n.ox || n.oy) o.offset = { x: n.ox || 0, y: n.oy || 0 };
  if (n.children.length) o.children = n.children.map(stripIds);
  return o;
}
function addIds(n) {
  return {
    id: nid(), word: n.word || "", hidden: !!n.hidden,
    ox: (n.offset && n.offset.x) || 0,
    oy: (n.offset && n.offset.y) || 0,
    children: (n.children || []).map(addIds),
  };
}

/* ============================ layout ============================ */

const LEVEL_H = 96, GAP_X = 18, NODE_H = 44;

function nodeWidth(word) {
  const t = (word || "").trim() || "WORD";
  return Math.max(64, t.length * 10.5 + 34);
}
function layout(node, depth, cursor, out) {
  const w = nodeWidth(node.word);
  if (!node.children.length) {
    const x = cursor.x + w / 2;
    cursor.x += w + GAP_X;
    out.push({ id: node.id, node, x, y: depth * LEVEL_H, w });
    return { x, min: x - w / 2, max: x + w / 2 };
  }
  const res = node.children.map(c => layout(c, depth + 1, cursor, out));
  const min = Math.min(...res.map(r => r.min));
  const max = Math.max(...res.map(r => r.max));
  const x = (min + max) / 2;   // center over the whole subtree, not just child centers
  cursor.x = Math.max(cursor.x, x + w / 2 + GAP_X);
  out.push({ id: node.id, node, x, y: depth * LEVEL_H, w });
  return { x, min: Math.min(min, x - w / 2), max: Math.max(max, x + w / 2) };
}

/* ============================ rendering ============================ */

const $ = id => document.getElementById(id);
const el = (tag, cls, text) => {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text !== undefined) e.textContent = text;
  return e;
};

function renderEditor() {
  const rows = $("rows");
  rows.innerHTML = "";
  buildRow(rows, tree, 0, null, 0, 1);
  renderStats();
}

function buildRow(container, node, depthIdx, parentId, index, siblings) {
  const row = el("div", "row" + (regenerating.has(node.id) ? " regenerating" : ""));
  row.style.paddingLeft = (depthIdx * 22) + "px";

  const twist = el("button", "twist" + (node.children.length ? "" : " ghost"),
    node.children.length ? (collapsedSet.has(node.id) ? "▸" : "▾") : "·");
  twist.disabled = !node.children.length;
  twist.onclick = () => {
    collapsedSet.has(node.id) ? collapsedSet.delete(node.id) : collapsedSet.add(node.id);
    renderEditor();
  };
  row.appendChild(twist);

  const input = el("input", "word-in" + ((node.word || "").trim() ? "" : " empty"));
  input.value = node.word;
  input.placeholder = "word…";
  input.oninput = () => {
    node.word = input.value.toUpperCase();
    input.classList.toggle("empty", !(node.word || "").trim());
    renderPreview();   // editor rows untouched → typing keeps focus
    renderStats();
    onChange();
  };
  row.appendChild(input);

  const pill = el("button", "pill " + (node.hidden ? "pill-hidden" : "pill-shown"),
    node.hidden ? "hidden" : "shown");
  pill.title = node.hidden
    ? "Hidden — player must place this word"
    : "Revealed — shown when the level starts";
  pill.onclick = () => { node.hidden = !node.hidden; renderAll(); onChange(); };
  row.appendChild(pill);

  const btns = el("div", "row-btns");
  const isRoot = parentId === null;

  const bAdd = el("button", "ic", "+");
  bAdd.title = "Add child";
  bAdd.onclick = () => {
    node.children.push(N("", true));
    collapsedSet.delete(node.id);
    renderAll();
    onChange();
  };

  const bAi = el("button", "ic ai", "✨");
  bAi.title = "Suggest child words with AI";
  bAi.onclick = async () => {
    if (!onSuggest) return;
    bAi.disabled = true;
    bAi.textContent = "…";
    try {
      const words = await onSuggest(node, pathTo(tree, node.id) || [], collectWords(tree));
      (words || []).forEach(w => node.children.push(N(w, true)));
      if (words && words.length) { collapsedSet.delete(node.id); onChange(); }
    } finally {
      renderAll();
    }
  };

  const bRe = el("button", "ic re", "↻");
  bRe.title = node.children.length
    ? (isRoot ? "Regenerate the whole tree below this word with AI"
              : "Regenerate this word and its whole branch with AI")
    : "Regenerate this word with AI";
  bRe.onclick = () => regenerate(node, { keepWord: isRoot, btn: bRe });

  const bUp = el("button", "ic", "↑");
  bUp.title = "Move up";
  bUp.disabled = isRoot || index === 0;
  bUp.onclick = () => { swap(parentId, index, -1); };

  const bDown = el("button", "ic", "↓");
  bDown.title = "Move down";
  bDown.disabled = isRoot || index === siblings - 1;
  bDown.onclick = () => { swap(parentId, index, +1); };

  const bDel = el("button", "ic danger", "✕");
  bDel.title = isRoot ? "Root can't be deleted" : "Delete (with branch)";
  bDel.disabled = isRoot;
  bDel.onclick = () => {
    const p = findParent(tree, node.id);
    if (p) { p.children = p.children.filter(c => c.id !== node.id); renderAll(); onChange(); }
  };

  btns.append(bAdd, bAi, bRe, bUp, bDown, bDel);
  row.appendChild(btns);
  container.appendChild(row);

  if (!collapsedSet.has(node.id)) {
    node.children.forEach((c, i) =>
      buildRow(container, c, depthIdx + 1, node.id, i, node.children.length));
  }
}

const regenerating = new Set();

/** Ask the AI for a fresh word (and, when the node has children, a fresh
    branch under it) and drop it into the tree in place. */
async function regenerate(node, { keepWord = false, btn = null } = {}) {
  if (!onRegenerate || regenerating.has(node.id)) return;
  regenerating.add(node.id);
  const label = btn && btn.textContent;
  if (btn) { btn.disabled = true; btn.textContent = "…"; }
  try {
    const gen = await onRegenerate(node, {
      keepWord,
      path: pathTo(tree, node.id) || [],
      shape: shapeOf(node),
      avoid: collectWordsExcept(tree, node.id),
    });
    if (gen) { applyBranch(node, gen); onChange(); }
  } finally {
    regenerating.delete(node.id);
    if (btn) { btn.disabled = false; btn.textContent = label; }
    renderAll();
  }
}

$("btn-regen-tree").onclick = () =>
  regenerate(tree, { keepWord: true, btn: $("btn-regen-tree") });

function swap(parentId, index, dir) {
  const p = findNode(tree, parentId);
  const j = index + dir;
  if (!p || j < 0 || j >= p.children.length) return;
  [p.children[index], p.children[j]] = [p.children[j], p.children[index]];
  renderAll();
  onChange();
}

function renderStats() {
  const hidden = collectHidden(tree);
  $("stats").innerHTML = "";
  $("stats").append(
    el("span", "", countNodes(tree) + " words"),
    el("span", "dot"),
    el("span", "", hidden.length + " to place"),
    el("span", "dot"),
    el("span", "", "depth " + maxDepth(tree)),
  );
  const dupes = duplicateWords(tree);
  $("rows").querySelectorAll(".word-in").forEach(i =>
    i.classList.toggle("dup", dupes.has(i.value.trim().toUpperCase())));

  const empty = countEmpty(tree);
  const msgs = [];
  if (empty > 0) {
    msgs.push(empty + " node" + (empty > 1 ? "s" : "") + " still unnamed — fill every word before exporting.");
  }
  if (dupes.size) {
    msgs.push("Used more than once: " + [...dupes].join(", ") +
      " — every word must appear exactly once, or the player can't place it.");
  }
  $("warn").textContent = msgs.join(" ");
}

function renderPreview() {
  const positions = [];
  layout(tree, 0, { x: 0 }, positions);
  const posById = {};
  positions.forEach(p => posById[p.id] = p);

  // apply manual drag offsets — an offset moves the node AND its whole subtree
  (function acc(n, dx, dy) {
    dx += n.ox || 0; dy += n.oy || 0;
    const p = posById[n.id];
    p.x += dx; p.y += dy;
    n.children.forEach(c => acc(c, dx, dy));
  })(tree, 0, 0);

  // normalize so nothing goes off the top/left edge
  let minX = Infinity, minY = Infinity, maxX = 0, maxY = 0;
  positions.forEach(p => {
    minX = Math.min(minX, p.x - p.w / 2);
    minY = Math.min(minY, p.y);
    maxX = Math.max(maxX, p.x + p.w / 2);
    maxY = Math.max(maxY, p.y + NODE_H);
  });
  const shiftX = 16 - minX, shiftY = 12 - minY;
  positions.forEach(p => { p.x += shiftX; p.y += shiftY; });
  const W = maxX + shiftX + 16, H = maxY + shiftY + 16;

  const canvas = $("canvas");
  canvas.innerHTML = "";
  canvas.style.width = (W * zoom) + "px";
  canvas.style.height = (H * zoom) + "px";

  const inner = el("div");
  inner.style.cssText =
    "transform:scale(" + zoom + ");transform-origin:0 0;position:relative;" +
    "width:" + W + "px;height:" + H + "px;";
  canvas.appendChild(inner);

  const svgNS = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(svgNS, "svg");
  svg.setAttribute("width", W);
  svg.setAttribute("height", H);
  svg.style.cssText = "position:absolute;inset:0";
  positions.forEach(p => {
    const kids = p.node.children.map(c => posById[c.id]).filter(Boolean);
    if (!kids.length) return;
    const midY = p.y + NODE_H + (LEVEL_H - NODE_H) / 2;
    const xs = kids.map(k => k.x);
    // the bus must always include the parent's own x, so the vertical
    // stub from the parent connects to the line exactly
    const busMin = Math.min(...xs, p.x);
    const busMax = Math.max(...xs, p.x);
    const g = document.createElementNS(svgNS, "g");
    g.setAttribute("class", "edge");
    const paths = ["M " + p.x + " " + (p.y + NODE_H) + " V " + midY];
    if (busMax - busMin > 0.5) paths.push("M " + busMin + " " + midY + " H " + busMax);
    kids.forEach(k => paths.push("M " + k.x + " " + midY + " V " + k.y));
    paths.forEach(d => {
      const path = document.createElementNS(svgNS, "path");
      path.setAttribute("d", d);
      g.appendChild(path);
    });
    svg.appendChild(g);
  });
  inner.appendChild(svg);

  const dupes = duplicateWords(tree);
  positions.forEach(p => {
    const blank = playerView && p.node.hidden;
    const cls = blank ? "node-blank" : (p.node.hidden ? "node-hidden" : "node-shown");
    const dup = !playerView && dupes.has((p.node.word || "").trim().toUpperCase()) ? " dup" : "";
    const d = el("div", "node " + cls + dup + (playerView ? "" : " draggable"), blank ? "" : (p.node.word || "…"));
    d.style.cssText =
      "left:" + (p.x - p.w / 2) + "px;top:" + p.y + "px;" +
      "width:" + p.w + "px;height:" + NODE_H + "px;";
    if (!playerView) {
      d.addEventListener("pointerdown", e => startDrag(e, p.node));
      d.addEventListener("dblclick", () => {
        p.node.ox = 0; p.node.oy = 0;
        renderPreview();
        onChange();
      });
      d.title = dup
        ? "This word is used more than once — the player can't tell where it goes"
        : "Drag to move this branch · double-click to snap back";
    }
    inner.appendChild(d);
  });

  // hud + bank
  const hidden = collectHidden(tree);
  $("hud-level").textContent = "Level " + ($("in-level").value || 1);
  $("hud-moves").textContent = $("in-moves").value || 1;
  $("hud-coins").textContent = $("in-coins").value || 0;
  $("progress").textContent = (playerView ? 0 : hidden.length) + "/" + hidden.length;

  const bank = $("bank");
  bank.innerHTML = "";
  if (playerView && hidden.length) {
    bank.classList.remove("hiddenel");
    hidden.forEach(w => bank.appendChild(el("span", "chip", w)));
  } else {
    bank.classList.add("hiddenel");
  }
}

function renderAll() { renderEditor(); renderPreview(); }

/* ---------- manual drag of branches ---------- */

let drag = null;

function startDrag(e, node) {
  e.preventDefault();
  drag = { node, sx: e.clientX, sy: e.clientY, ox: node.ox || 0, oy: node.oy || 0, moved: false };
  document.body.classList.add("dragging");
}
window.addEventListener("pointermove", e => {
  if (!drag) return;
  const dx = (e.clientX - drag.sx) / zoom;
  const dy = (e.clientY - drag.sy) / zoom;
  if (!drag.moved && Math.abs(dx) < 3 && Math.abs(dy) < 3) return; // ignore accidental jiggle
  drag.moved = true;
  drag.node.ox = Math.round((drag.ox + dx) / 4) * 4;  // snap to 4px grid
  drag.node.oy = Math.round((drag.oy + dy) / 4) * 4;
  renderPreview();
});
window.addEventListener("pointerup", () => {
  if (!drag) return;
  const moved = drag.moved;
  drag = null;
  document.body.classList.remove("dragging");
  if (moved) onChange();
});

function clearOffsets(n) {
  n.ox = 0; n.oy = 0;
  n.children.forEach(clearOffsets);
}
$("btn-reset-layout").onclick = () => { clearOffsets(tree); renderPreview(); onChange(); };

/* ============================ preview controls ============================ */

$("seg-designer").onclick = () => {
  playerView = false;
  $("seg-designer").classList.add("on");
  $("seg-player").classList.remove("on");
  renderPreview();
};
$("seg-player").onclick = () => {
  playerView = true;
  $("seg-player").classList.add("on");
  $("seg-designer").classList.remove("on");
  renderPreview();
};
$("zoom").oninput = e => { zoom = +e.target.value; renderPreview(); };

function fitZoom() {
  const wrap = document.querySelector(".canvas-wrap");
  if (!wrap) return;
  const positions = [];
  layout(tree, 0, { x: 0 }, positions);
  let maxX = 0;
  positions.forEach(p => maxX = Math.max(maxX, p.x + p.w / 2));
  const W = maxX + 24;
  zoom = Math.min(1.2, Math.max(0.3, (wrap.clientWidth - 32) / W));
  $("zoom").value = zoom.toFixed(2);
  renderPreview();
}
$("btn-fit").onclick = fitZoom;
window.addEventListener("resize", () => {
  clearTimeout(window.__fitT);
  window.__fitT = setTimeout(fitZoom, 150);
});

/* ============================ public API ============================ */

window.Designer = {
  /** Replace the whole tree. Accepts the persisted {word,hidden,children} shape. */
  setTree(data) {
    tree = addIds(data && typeof data.word !== "undefined" ? data : { word: "", hidden: false });
    collapsedSet.clear();
    renderAll();
  },
  /** The tree in its persisted shape. */
  getTree() { return stripIds(tree); },
  emptyTree() { return { word: "", hidden: false }; },
  stats() {
    return {
      words: countNodes(tree),
      hidden: collectHidden(tree).length,
      depth: maxDepth(tree),
      empty: countEmpty(tree),
    };
  },
  renderAll,
  renderPreview,
  fitZoom,
  onChange(fn) { onChange = fn; },
  onSuggest(fn) { onSuggest = fn; },
  onRegenerate(fn) { onRegenerate = fn; },
};
