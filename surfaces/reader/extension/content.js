// Moguru Reader — content script.
// Block-level annotation (the Yomitan approach): the full visible text of a
// paragraph/block is sent to /annotate in ONE request (so the tokenizer gets
// real sentence context), then banded tokens are mapped back onto the DOM.
// Furigana (`<rt>`/`<rp>` inside <ruby>) is skipped — it's a reading aid,
// not content to color. Lazy (IntersectionObserver) + debounced, cached per
// (text hash + known-set version), repaints when the version bumps.

const JA_RE = /[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]/;
const SKIP_TAGS = new Set([
  "SCRIPT", "STYLE", "NOSCRIPT", "CODE", "PRE", "TEXTAREA", "INPUT",
  "SELECT", "BUTTON", "CANVAS", "SVG", "IFRAME",
  "RT", "RP", // furigana readings — never annotate, never feed to the parser
]);
const BLOCK_SEL = "p, li, h1, h2, h3, h4, h5, h6, td, th, dd, dt, blockquote, figcaption, article, div";

let enabled = true;
let knownVersion = null;
const cache = new Map(); // textHash+version -> banded token plan
const pending = new Set(); // blocks awaiting annotation
const observer = new IntersectionObserver(
  (entries) => {
    for (const e of entries) {
      if (e.isIntersecting) {
        observer.unobserve(e.target);
        queueBlock(e.target);
      }
    }
  },
  { rootMargin: "300px" }
);

// ---------------------------------------------------------------------------
// helpers
// ---------------------------------------------------------------------------

async function hashText(text) {
  const buf = await crypto.subtle.digest("SHA-1", new TextEncoder().encode(text));
  return Array.from(new Uint8Array(buf.slice(0, 8)))
    .map((b) => b.toString(16).padStart(2, "0"))
    .join("");
}

function send(msg) {
  return new Promise((resolve) => chrome.runtime.sendMessage(msg, resolve));
}

let flushTimer = null;
function queueBlock(block) {
  pending.add(block);
  if (!flushTimer) flushTimer = setTimeout(flush, 150);
}

async function flush() {
  flushTimer = null;
  const batch = [...pending];
  pending.clear();
  if (!enabled) return;
  for (const block of batch) {
    if (block.isConnected) annotateBlock(block).catch(() => {});
  }
}

// Text nodes of a block that belong to THIS block (not a nested one), with
// ruby readings and code excluded. Returns the concatenated text plus the
// node ranges so token char offsets can be mapped back.
function collectText(block) {
  const walker = document.createTreeWalker(block, NodeFilter.SHOW_TEXT, {
    acceptNode(node) {
      const p = node.parentElement;
      if (!p || SKIP_TAGS.has(p.tagName)) return NodeFilter.FILTER_REJECT;
      if (p.closest(".moguru-done, .moguru-panel, .moguru-legend")) return NodeFilter.FILTER_REJECT;
      if (!node.nodeValue || !JA_RE.test(node.nodeValue)) return NodeFilter.FILTER_REJECT;
      // text belonging to a nearer block (nested p inside a div, etc.)
      if (p.closest(BLOCK_SEL) !== block && block !== document.body) return NodeFilter.FILTER_REJECT;
      return NodeFilter.FILTER_ACCEPT;
    },
  });
  const nodes = [];
  let text = "";
  while (walker.nextNode()) {
    nodes.push({ node: walker.currentNode, start: text.length });
    text += walker.currentNode.nodeValue;
  }
  return { nodes, text };
}

async function annotateBlock(block) {
  const { nodes, text } = collectText(block);
  if (!nodes.length) return;
  const key = (await hashText(text)) + ":" + knownVersion;
  let plan = cache.get(key);
  if (!plan) {
    const resp = await send({ type: "moguru:annotate", text });
    if (!resp || !resp.ok) return; // engine down — leave page clean
    if (resp.version && resp.version !== knownVersion) {
      knownVersion = resp.version;
      cache.clear();
    }
    plan = resp.data.tokens.filter((t) => t.band && t.band !== "plain" && t.band !== "known");
    cache.set((await hashText(text)) + ":" + knownVersion, plan);
  }
  if (!plan.length) {
    block.classList.add("moguru-done");
    return;
  }
  // map block-text offsets back onto individual text nodes; a token spanning
  // a node boundary (kanji + okurigana split by ruby markup) wraps each part
  for (const { node, start } of nodes) {
    const len = node.nodeValue.length;
    const local = plan
      .filter((t) => t.char_start < start + len && t.char_end > start)
      .map((t) => ({
        ...t,
        char_start: Math.max(0, t.char_start - start),
        char_end: Math.min(len, t.char_end - start),
      }));
    if (local.length) render(node, local);
  }
  block.classList.add("moguru-done");
  watchBlockExit(block);
}

// Wrap the banded char-ranges of ONE text node in styled spans.
function render(node, tokens) {
  const parent = node.parentElement;
  if (!parent) return;
  const text = node.nodeValue;
  const frag = document.createDocumentFragment();
  let cursor = 0;
  for (const t of tokens) {
    if (t.char_start < cursor || t.char_start > text.length) continue;
    if (t.char_start > cursor) frag.appendChild(document.createTextNode(text.slice(cursor, t.char_start)));
    const span = document.createElement("span");
    span.className = `moguru-tok moguru-${t.band}`;
    span.dataset.lemma = t.lemma;
    span.dataset.reading = t.reading || "";
    span.dataset.band = t.band;
    span.textContent = text.slice(t.char_start, t.char_end);
    frag.appendChild(span);
    cursor = t.char_end;
  }
  if (cursor < text.length) frag.appendChild(document.createTextNode(text.slice(cursor)));
  parent.replaceChild(frag, node);
}

// ---------------------------------------------------------------------------
// block discovery (lazy)
// ---------------------------------------------------------------------------

function scan(root = document.body) {
  const blocks = new Set();
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, {
    acceptNode(node) {
      const p = node.parentElement;
      if (!p || SKIP_TAGS.has(p.tagName)) return NodeFilter.FILTER_REJECT;
      if (p.closest(".moguru-done, .moguru-panel, .moguru-legend")) return NodeFilter.FILTER_REJECT;
      if (!node.nodeValue || !JA_RE.test(node.nodeValue)) return NodeFilter.FILTER_REJECT;
      return NodeFilter.FILTER_ACCEPT;
    },
  });
  while (walker.nextNode()) {
    const b = walker.currentNode.parentElement.closest(BLOCK_SEL) || document.body;
    if (!b.classList.contains("moguru-done")) blocks.add(b);
  }
  for (const b of blocks) observer.observe(b);
}

// ---------------------------------------------------------------------------
// right-click: stash the clicked token for the background menu
// ---------------------------------------------------------------------------

document.addEventListener(
  "contextmenu",
  (ev) => {
    const tok = ev.target.closest?.(".moguru-tok");
    if (tok) {
      send({
        type: "moguru:stashToken",
        token: {
          word: tok.textContent,
          lemma: tok.dataset.lemma,
          sentence: sentenceOf(tok),
        },
      });
    }
  },
  true
);

function sentenceOf(el) {
  const block = el.closest(BLOCK_SEL);
  // visible text of the block, furigana excluded (clone and strip rt/rp)
  let text = "";
  if (block) {
    const clone = block.cloneNode(true);
    clone.querySelectorAll("rt, rp").forEach((n) => n.remove());
    text = clone.textContent || "";
  }
  text = (text || el.textContent || "").replace(/\s+/g, " ").trim();
  return text.slice(0, 300);
}

// ---------------------------------------------------------------------------
// signal emission (shadow schema, spec §4) — hover / lookup / complete
// ---------------------------------------------------------------------------

const signalQueue = [];
let signalTimer = null;
function emitSignal(signal) {
  signalQueue.push({ ...signal, ts: new Date().toISOString() });
  if (!signalTimer) signalTimer = setTimeout(flushSignals, 2000);
}
function flushSignals() {
  signalTimer = null;
  if (!signalQueue.length) return;
  const batch = signalQueue.splice(0, 50);
  chrome.runtime.sendMessage(
    { type: "moguru:signals", signals: batch },
    () => void chrome.runtime.lastError
  );
}

// hover: dwell >= 800ms on a banded token (weak "sought the meaning")
let hoverTok = null, hoverTimer = null;
document.addEventListener("pointerover", (ev) => {
  const tok = ev.target.closest?.(".moguru-tok");
  if (!tok || tok === hoverTok) return;
  clearTimeout(hoverTimer);
  hoverTok = tok;
  const lemma = tok.dataset.lemma;
  hoverTimer = setTimeout(() => {
    if (hoverTok === tok && lemma) {
      emitSignal({
        type: "hover", key: lemma, key_kind: "vocab",
        sentence: sentenceOf(tok), modality: "reading",
        dwell_ms: 800,
      });
    }
  }, 800);
});
document.addEventListener("pointerout", (ev) => {
  if (ev.target.closest?.(".moguru-tok") === hoverTok) {
    clearTimeout(hoverTimer);
    hoverTok = null;
  }
});

// complete: a block that scrolled past without any token interaction — the
// soft positive backbone. Throttled + capped.
const exitedBlocks = new WeakSet();
const interactedLemmas = new Set();
document.addEventListener("pointerdown", (ev) => {
  const tok = ev.target.closest?.(".moguru-tok");
  if (tok?.dataset.lemma) interactedLemmas.add(tok.dataset.lemma);
});
const exitObserver = new IntersectionObserver((entries) => {
  for (const e of entries) {
    if (e.target.isConnected && !e.isIntersecting && e.boundingClientRect.top < 0) {
      exitObserver.unobserve(e.target);
      if (exitedBlocks.has(e.target)) continue;
      exitedBlocks.add(e.target);
      const lemmas = [...e.target.querySelectorAll(".moguru-tok")]
        .map((s) => s.dataset.lemma)
        .filter((l) => l && !interactedLemmas.has(l));
      if (lemmas.length && lemmas.length <= 12) {
        emitSignal({
          type: "complete", sentence: sentenceOf(e.target), modality: "reading",
        });
      }
    }
  }
}, {});
function watchBlockExit(block) {
  exitObserver.observe(block);
}

// ---------------------------------------------------------------------------
// click action card — plain left-click a colored word for the actions
// ---------------------------------------------------------------------------

let popEl = null;
function closePop() {
  popEl?.remove();
  popEl = null;
}
document.addEventListener("keydown", (ev) => {
  if (ev.key === "Escape") closePop();
});
document.addEventListener("click", (ev) => {
  if (ev.target.closest?.(".moguru-pop")) return; // clicks inside the card
  const tok = ev.target.closest?.(".moguru-tok");
  if (!tok) {
    closePop();
    return;
  }
  if (String(window.getSelection?.() || "")) return; // user is selecting text
  openPop(tok);
}, true);

function openPop(tok) {
  closePop();
  const word = tok.textContent;
  const lemma = tok.dataset.lemma || word;
  const sentence = sentenceOf(tok);

  popEl = document.createElement("div");
  popEl.className = "moguru-pop";
  const rect = tok.getBoundingClientRect();
  popEl.style.left = `${Math.max(8, Math.min(window.innerWidth - 316, rect.left + window.scrollX))}px`;
  popEl.style.top = `${Math.max(8, rect.top + window.scrollY - 10)}px`;

  const head = document.createElement("div");
  head.className = "moguru-pop-head";
  head.append(makeTxt(word), makeSmall(lemma));
  const x = document.createElement("button");
  x.textContent = "×";
  x.onclick = closePop;
  head.appendChild(x);

  const actions = document.createElement("div");
  actions.className = "moguru-pop-actions";
  const body = document.createElement("div");
  body.className = "moguru-pop-body";
  body.style.display = "none";

  const btn = (label, fn) => {
    const b = document.createElement("button");
    b.textContent = label;
    b.onclick = fn;
    actions.appendChild(b);
  };
  btn("説明 explain", async (e) => {
    e.target.disabled = true;
    body.style.display = "block";
    body.textContent = "…";
    const r = await send({ type: "moguru:explain", word, sentence, lemma });
    body.textContent = "";
    if (!r || !r.ok) { body.textContent = `⚠ ${r?.error || "engine unreachable"}`; return; }
    const toks = r.entries?.tokens || [];
    const t = toks.find((t) => t.lemma) || {};
    appendKv(body, "reading", t.reading_kana || "");
    for (const [l, es] of Object.entries(r.entries?.entries || {})) {
      for (const en of es.slice(0, 1)) {
        const gloss = (en.senses || []).flatMap((s) => s.gloss || []).slice(0, 3).join("; ");
        appendKv(body, "JMdict", `${l}: ${gloss}`);
      }
    }
    if (r.answer) {
      const ans = document.createElement("div");
      ans.style.marginTop = "6px";
      ans.textContent = r.answer.slice(0, 900);
      body.appendChild(ans);
    }
  });
  btn("Anki", async (e) => {
    e.target.disabled = true;
    const r = await send({ type: "moguru:mine", text: sentence, target: lemma, add: true });
    body.style.display = "block";
    body.textContent = "";
    if (!r || !r.ok) { body.textContent = `⚠ ${r?.error || "engine unreachable"}`; return; }
    const item = (r.data.results || [])[0];
    body.textContent = item?.note_id
      ? `✔ card #${item.note_id} — ${item.candidate.target}`
      : `⚠ ${r.data.results?.[0]?.error || "no candidate"}`;
  });
  btn("既知 known", async (e) => {
    e.target.disabled = true;
    await send({ type: "moguru:mark", lemma });
    closePop(); // repaint arrives via version broadcast
  });

  popEl.append(head, actions, body);
  document.documentElement.appendChild(popEl);
}

function makeTxt(t) { const s = document.createElement("span"); s.textContent = t; return s; }
function makeSmall(t) { const s = document.createElement("small"); s.textContent = t; return s; }
function appendKv(parent, k, v) {
  const div = document.createElement("div");
  const b = document.createElement("b");
  b.textContent = `${k}: `;
  div.append(b, document.createTextNode(v || ""));
  parent.appendChild(div);
}

// ---------------------------------------------------------------------------
// messages from background
// ---------------------------------------------------------------------------

chrome.runtime.onMessage.addListener((msg, _s, _r) => {
  if (msg.type === "moguru:version") {
    if (msg.version && msg.version !== knownVersion) {
      knownVersion = msg.version;
      repaintAll();
    }
  } else if (msg.type === "moguru:toggle") {
    toggle();
  } else if (msg.type === "moguru:panel") {
    showPanel(msg.panel);
  }
});

function repaintAll() {
  cache.clear();
  // unwrap existing spans back into plain text, then re-scan
  document.querySelectorAll("span.moguru-tok").forEach((span) => {
    const parent = span.parentElement;
    if (!parent) return;
    parent.replaceChild(document.createTextNode(span.textContent), span);
    parent.normalize();
  });
  document.querySelectorAll(".moguru-done").forEach((el) => el.classList.remove("moguru-done"));
  if (enabled) scan();
}

function toggle() {
  enabled = !enabled;
  document.documentElement.classList.toggle("moguru-off", !enabled);
  if (enabled) scan();
}

// ---------------------------------------------------------------------------
// explanation panel
// ---------------------------------------------------------------------------

function showPanel(panel) {
  document.querySelector(".moguru-panel")?.remove();
  const el = document.createElement("div");
  el.className = "moguru-panel";
  const head = document.createElement("div");
  head.className = "moguru-panel-head";
  head.textContent = `潜る — ${panel.word || ""}`;
  const close = document.createElement("button");
  close.textContent = "×";
  close.onclick = () => el.remove();
  head.appendChild(close);
  el.appendChild(head);

  const body = document.createElement("div");
  body.className = "moguru-panel-body";
  if (panel.error) {
    body.textContent = `⚠ ${panel.error} — is the engine running? (moguru serve)`;
  } else if (panel.mined || panel.minedError) {
    if (panel.minedError) body.textContent = `⚠ ${panel.minedError}`;
    for (const r of panel.mined || []) {
      const c = r.candidate;
      const div = document.createElement("div");
      div.textContent = `✔ ${c.sentence}  → card #${r.note_id} (${c.target})`;
      body.appendChild(div);
    }
  } else if (panel.entries) {
    const toks = panel.entries.tokens || [];
    const tok = toks.find((t) => t.lemma) || {};
    body.appendChild(kv("surface", tok.surface || panel.word));
    body.appendChild(kv("lemma", tok.lemma || ""));
    body.appendChild(kv("reading", tok.reading_kana || ""));
    for (const [lemma, entries] of Object.entries(panel.entries.entries || {})) {
      for (const e of entries.slice(0, 2)) {
        const gloss = (e.senses || []).flatMap((s) => s.gloss || []).slice(0, 3).join("; ");
        body.appendChild(kv("JMdict", `${lemma}: ${gloss}`));
      }
    }
    if (panel.answer) {
      const ans = document.createElement("div");
      ans.className = "moguru-panel-ans";
      ans.textContent = panel.answer.slice(0, 1200);
      body.appendChild(ans);
    }
  } else if (!panel.word) {
    body.textContent = "右クリックする単語の上にカーソルを合わせてください（色のついた語）。";
  }
  el.appendChild(body);
  document.documentElement.appendChild(el);
  setTimeout(() => el.remove(), 60_000);
}

function kv(k, v) {
  const div = document.createElement("div");
  const b = document.createElement("b");
  b.textContent = `${k}: `;
  div.appendChild(b);
  div.appendChild(document.createTextNode(v || ""));
  return div;
}

// ---------------------------------------------------------------------------
// legend
// ---------------------------------------------------------------------------

function mountLegend() {
  if (document.querySelector(".moguru-legend")) return;
  const el = document.createElement("div");
  el.className = "moguru-legend";
  el.innerHTML =
    '<span class="moguru-tok moguru-iplus">i+1</span> mine me · ' +
    '<span class="moguru-tok moguru-new_hard">new</span> too hard · ' +
    '<span class="moguru-tok moguru-known_unstable">shaky</span> · ' +
    '<span style="opacity:.6">Alt+M toggle</span>';
  document.body.appendChild(el);
}

// ---------------------------------------------------------------------------
// boot
// ---------------------------------------------------------------------------

(async () => {
  const { enabled: saved, cssVars } = await chrome.storage.sync.get(["enabled", "cssVars"]);
  if (cssVars) {
    const style = document.createElement("style");
    style.className = "moguru-colors";
    style.textContent = cssVars;
    document.documentElement.appendChild(style);
  }
  if (saved === false) {
    enabled = false;
    document.documentElement.classList.add("moguru-off");
    return;
  }
  const v = await send({ type: "moguru:version" });
  if (v && v.ok) knownVersion = v.version;
  scan();
  mountLegend();
  // late-loading pages (SPAs)
  new MutationObserver(() => {
    if (!enabled) return;
    scan();
  }).observe(document.body, { childList: true, subtree: true });
})();
