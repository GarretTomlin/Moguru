// Moguru Reader — content script.
// Walks visible Japanese text nodes, asks the engine to /annotate, wraps
// banded tokens in styled spans. Lazy (IntersectionObserver) + debounced,
// cached per (text hash + known-set version), repaints when the version
// bumps (a word you just learned stops glowing).

const JA_RE = /[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]/;
const SKIP_TAGS = new Set([
  "SCRIPT", "STYLE", "NOSCRIPT", "CODE", "PRE", "TEXTAREA", "INPUT",
  "SELECT", "BUTTON", "CANVAS", "SVG", "IFRAME",
]);

let enabled = true;
let knownVersion = null;
const cache = new Map(); // textHash+version -> render plan
const pending = new Set(); // nodes awaiting annotation
const observer = new IntersectionObserver(
  (entries) => {
    for (const e of entries) {
      if (e.isIntersecting) {
        observer.unobserve(e.target);
        queueNode(e.target);
      }
    }
  },
  { rootMargin: "200px" }
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

function markDone(node) {
  node.parentElement?.classList.add("moguru-done");
}

function queueNode(node) {
  pending.add(node);
  scheduleFlush();
}

let flushTimer = null;
function scheduleFlush() {
  if (flushTimer) return;
  flushTimer = setTimeout(flush, 150);
}

async function flush() {
  flushTimer = null;
  const batch = [...pending];
  pending.clear();
  if (!enabled) return;
  for (const node of batch) {
    if (!node.parentElement) continue; // detached while waiting
    annotateNode(node).catch(() => markDone(node));
  }
}

async function annotateNode(node) {
  const text = node.nodeValue;
  if (!text || !JA_RE.test(text)) {
    markDone(node);
    return;
  }
  const key = (await hashText(text)) + ":" + knownVersion;
  let plan = cache.get(key);
  if (!plan) {
    const resp = await send({ type: "moguru:annotate", text });
    if (!resp || !resp.ok) {
      markDone(node); // engine down — leave page clean
      return;
    }
    if (resp.version && resp.version !== knownVersion) {
      knownVersion = resp.version;
      cache.clear();
    }
    plan = resp.data.tokens.filter((t) => t.band && t.band !== "plain");
    cache.set((await hashText(text)) + ":" + knownVersion, plan);
  }
  render(node, plan);
}

// Wrap banded tokens (char offsets into node.nodeValue) in styled spans.
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
  parent.classList.add("moguru-done");
  watchSentenceExits(parent);
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

// complete: a sentence block that left the viewport without any interaction
// on its tokens — the soft positive backbone. Throttled + capped.
const seenSentences = new WeakSet();
const interactedLemmas = new Set();
document.addEventListener("pointerdown", (ev) => {
  const tok = ev.target.closest?.(".moguru-tok");
  if (tok?.dataset.lemma) interactedLemmas.add(tok.dataset.lemma);
});
const exitObserver = new IntersectionObserver((entries) => {
  for (const e of entries) {
    if (e.target.isConnected && !e.isIntersecting && e.boundingClientRect.top < 0) {
      exitObserver.unobserve(e.target);
      if (seenSentences.has(e.target)) continue;
      seenSentences.add(e.target);
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
function watchSentenceExits(node) {
  const block = node.closest?.("p, li, h1, h2, h3, td, div");
  if (block) exitObserver.observe(block);
}

// ---------------------------------------------------------------------------
// node discovery (lazy)
// ---------------------------------------------------------------------------

function scan(root = document.body) {
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, {
    acceptNode(node) {
      if (SKIP_TAGS.has(node.parentElement?.tagName)) return NodeFilter.FILTER_REJECT;
      if (node.parentElement?.closest(".moguru-done, .moguru-panel, .moguru-legend"))
        return NodeFilter.FILTER_REJECT;
      if (!JA_RE.test(node.nodeValue)) return NodeFilter.FILTER_REJECT;
      return NodeFilter.FILTER_ACCEPT;
    },
  });
  const nodes = [];
  while (walker.nextNode()) nodes.push(walker.currentNode);
  for (const node of nodes) observer.observe(node.parentElement);
}

// ---------------------------------------------------------------------------
// right-click: stash the clicked token for the background menu
// ---------------------------------------------------------------------------

document.addEventListener(
  "contextmenu",
  (ev) => {
    const tok = ev.target.closest?.(".moguru-tok");
    if (tok) {
      const sentence = sentenceOf(tok);
      send({
        type: "moguru:stashToken",
        token: {
          word: tok.textContent,
          lemma: tok.dataset.lemma,
          sentence,
        },
      });
    }
  },
  true
);

function sentenceOf(tok) {
  // Best effort: the nearest block ancestor's text, trimmed to a sane length.
  const block = tok.closest("p, li, h1, h2, h3, h4, td, div");
  const text = (block?.innerText || tok.textContent || "").replace(/\s+/g, " ").trim();
  return text.slice(0, 300);
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
  document.querySelectorAll(".moguru-done").forEach((el) => el.classList.remove("moguru-done"));
  // unwrap existing spans so re-render starts from clean text nodes
  document.querySelectorAll("span.moguru-tok").forEach((span) => {
    const parent = span.parentElement;
    if (!parent) return;
    parent.replaceChild(document.createTextNode(span.textContent), span);
    parent.normalize();
  });
  scan();
}

function toggle() {
  enabled = !enabled;
  document.documentElement.classList.toggle("moguru-off", !enabled);
  if (enabled) {
    scan();
  }
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
