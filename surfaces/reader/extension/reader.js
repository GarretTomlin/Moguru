// Moguru PDF reader — extension page rendering PDFs with pdf.js, annotating
// the selectable text layer with engine bands. Extension pages share the
// background worker's engine access via chrome.runtime messages.

import * as pdfjsLib from "./lib/pdf.min.mjs";
pdfjsLib.GlobalWorkerOptions.workerSrc = "lib/pdf.worker.min.mjs";

const pagesEl = document.getElementById("pages");
const textPagesEl = document.getElementById("textpages");
let bandsOn = true;
let knownVersion = null;
const pageTexts = new Map(); // pageNum -> { text, tokens }
let textMode = false;

function send(msg) {
  return new Promise((resolve) => chrome.runtime.sendMessage(msg, resolve));
}

async function annotateText(text) {
  const resp = await send({ type: "moguru:annotate", text });
  if (!resp || !resp.ok) return null;
  if (resp.version) knownVersion = resp.version;
  return resp.data;
}

async function renderPage(pdf, pageNum) {
  const page = await pdf.getPage(pageNum);
  const viewport = page.getViewport({ scale: 1.6 });
  const div = document.createElement("div");
  div.className = "page";
  div.dataset.page = pageNum;

  const canvas = document.createElement("canvas");
  const ctx = canvas.getContext("2d");
  canvas.width = viewport.width;
  canvas.height = viewport.height;
  div.style.width = `${viewport.width}px`;
  div.style.height = `${viewport.height}px`;
  div.appendChild(canvas);

  const textLayer = document.createElement("div");
  textLayer.className = "textLayer";
  // pdf.js 4.x TextLayer positions its spans via this CSS variable
  div.style.setProperty("--scale-factor", viewport.scale);
  div.appendChild(textLayer);
  pagesEl.appendChild(div);

  await page.render({ canvasContext: ctx, viewport }).promise;

  // text content -> positioned spans (pdf.js convention)
  const textContent = await page.getTextContent();
  // Ruby editions (e.g. 青空文庫-style PDFs) interleave furigana as separate
  // SMALL text runs beside the kanji. Distinguish by font height vs the
  // page median: readings are excluded from the text we annotate AND from
  // the span map — the tokenizer then sees clean prose.
  function itemHeight(i) {
    if (i.height) return i.height;
    const tx = pdfjsLib.Util.transform(viewport.transform, i.transform);
    return Math.hypot(tx[2], tx[3]);
  }
  const items = textContent.items.filter((i) => i.str && i.str.trim());
  const heights = items.map(itemHeight).filter((h) => h > 0).sort((a, b) => a - b);
  const medianH = heights.length ? heights[Math.floor(heights.length / 2)] : 0;
  const isFurigana = (i) => medianH > 0 && itemHeight(i) < medianH * 0.75;
  // page furniture: pure digits sitting in the top/bottom 6% = page numbers
  const itemTop = (i) => {
    const tx = pdfjsLib.Util.transform(viewport.transform, i.transform);
    return tx[5];
  };
  const isPageFurniture = (i) =>
    /^[0-9\s.]+$/.test(i.str) &&
    (itemTop(i) < viewport.height * 0.06 ||
     itemTop(i) > viewport.height * 0.94);
  const bodyItems = items.filter((i) => !isFurigana(i) && !isPageFurniture(i));

  // Official pdf.js TextLayer: it injects the PDF's EMBEDDED fonts and
  // measures each run, so its glyphs render exactly on the canvas ones —
  // colored tokens then tint the real kanji via screen blending (see the
  // .tinted CSS). Hard-validated against our items; any mismatch or crash
  // falls back to the manual layer below (underlines + approximate tint).
  let spanLists = null;
  if (pdfjsLib.TextLayer) {
    try {
      const tl = new pdfjsLib.TextLayer({
        textContentSource: textContent,
        container: textLayer,
        viewport,
      });
      await tl.render();
      const layerSpans = [...textLayer.querySelectorAll("span")]
        .filter((s) => s.textContent.length);
      const full = textContent.items.map((i) => i.str).join("");
      if (layerSpans.map((s) => s.textContent).join("") === full) {
        textLayer.classList.add("tinted");
        // map every item (whitespace-only ones included, so the char
        // accounting stays exact) to the spans covering its text
        const queue = layerSpans.map((s) => ({ span: s, rest: s.textContent }));
        let qi = 0;
        spanLists = new Map();
        for (const it of textContent.items) {
          const covered = [];
          let need = it.str.length;
          while (need > 0 && qi < queue.length) {
            const q = queue[qi];
            const take = Math.min(q.rest.length, need);
            if (!covered.includes(q.span)) covered.push(q.span);
            q.rest = q.rest.slice(take);
            need -= take;
            if (!q.rest.length) qi++;
          }
          spanLists.set(it, covered);
        }
        for (const it of items) {
          if (isFurigana(it) || isPageFurniture(it)) {
            for (const s of spanLists.get(it) || []) s.dataset.furigana = "1";
          }
        }
      } else {
        textLayer.innerHTML = "";
      }
    } catch (e) {
      console.warn("moguru: official TextLayer failed, falling back", e);
      textLayer.innerHTML = "";
    }
  }

  if (!spanLists) {
    // Manual fallback: runs at exact PDF-declared positions with pinned
    // widths; banding renders as colored underlines (font substitution
    // can't be trusted to sit on the glyphs, so no tint here) —
    // font-independent, ghost-proof, nothing opaque ever drawn.
    textLayer.className = "textLayer";
    const pageSpans = [];
    for (const item of items) {
      const span = document.createElement("span");
      const tx = pdfjsLib.Util.transform(viewport.transform, item.transform);
      const angle = Math.atan2(tx[1], tx[0]);
      const fontHeight = Math.hypot(tx[2], tx[3]);
      span.textContent = item.str;
      span.style.left = `${tx[4]}px`;
      span.style.top = `${tx[5] - fontHeight}px`;
      span.style.fontSize = `${fontHeight}px`;
      if (item.fontName) span.style.fontFamily = item.fontName;
      if (angle) span.style.transform = `rotate(${angle}rad)`;
      // pin the run's width to the PDF-declared extent (unit convention
      // varies across producers — accept whichever sane ratio fits)
      const measured = span.getBoundingClientRect().width;
      if (measured > 0 && item.width > 0) {
        for (const w of [item.width * viewport.scale, item.width]) {
          const r = w / measured;
          if (r >= 0.6 && r <= 1.67) {
            span.style.width = `${w}px`;
            break;
          }
        }
      }
      textLayer.appendChild(span);
      pageSpans.push(span);
    }
    // zip spans against the SAME filtered `items` list they were built from
    // (walking the unfiltered list desynced bands whenever whitespace-only
    // items appeared — bands landed on the wrong runs, wrong lemma in the
    // action card, Anki "no candidate").
    spanLists = new Map();
    for (let k = 0; k < pageSpans.length; k++) {
      spanLists.set(items[k], [pageSpans[k]]);
      if (isFurigana(items[k]) || isPageFurniture(items[k])) {
        pageSpans[k].dataset.furigana = "1";
      }
    }
  }

  // annotate this page's body text (furigana/page numbers excluded).
  // Latin runs are joined WITH a space so foreign words don't glue
  // (…etbeau + Le ciel…); Japanese joins directly (no spaces in JA prose).
  // The offset map accumulates exactly what we join, so bands stay aligned.
  const parts = [];
  for (const i of bodyItems) {
    const prev = parts[parts.length - 1];
    if (
      prev &&
      /[A-Za-z0-9,.!?;:]$/.test(prev) &&
      /^[A-Za-z0-9]/.test(i.str)
    ) {
      parts.push(" ");
    }
    parts.push(i.str);
  }
  const pageText = parts.join("");
  for (let attempt = 0; attempt < 2; attempt++) {
    try {
      const data = await annotateText(pageText);
      if (data) {
        pageTexts.set(pageNum, { text: pageText, tokens: data.tokens });
        applyBands(spanLists, bodyItems, pageText, data.tokens);
        break;
      }
    } catch (e) {
      if (attempt === 1) console.warn("moguru annotate failed", e);
    }
  }
}

// Band the text-layer spans. bodyItems/pageText derive from the SAME join
// (injected Latin spaces included), so token ranges align. On a tinted layer
// each run is SPLIT into per-token child spans so ONLY the banded glyphs take
// color (screen blend recolors the canvas kanji beneath); an item whose text
// is shared across several spans (or a non-tinted fallback layer) underlines
// the whole covered run instead.
function applyBands(spanLists, bodyItems, pageText, tokens) {
  const banded = tokens
    .filter((t) => t.band && t.band !== "plain" && t.band !== "known")
    .sort((a, b) => a.char_start - b.char_start);
  let cursor = 0;
  const itemRanges = [];
  for (const item of bodyItems) {
    itemRanges.push({ item, start: cursor, end: cursor + item.str.length });
    cursor += item.str.length;
    // consume any space(s) injected between Latin runs before the next item
    while (cursor < pageText.length && pageText[cursor] === " ") cursor += 1;
  }
  const firstSpan = (spanLists.get(bodyItems[0]) || [])[0];
  const tinted = firstSpan?.closest(".textLayer")?.classList.contains("tinted");
  for (const { item, start, end } of itemRanges) {
    const covered = spanLists.get(item) || [];
    if (!covered.length) continue;
    const hits = banded.filter((t) => t.char_end > start && t.char_start < end);
    if (!hits.length) continue;
    const sole = covered.length === 1 && covered[0].textContent === item.str;
    if (!covered.length) {
      console.warn("moguru: no spans for item —", JSON.stringify(item.str));
      continue;
    }
    if (!tinted || !sole) {
      // whole-run banding: stack every hit's class (CSS decides the visible
      // color); a run carrying two banded tokens still shows BOTH marks
      for (const span of covered) {
        if (!span.classList.contains("moguru-tok")) {
          span.classList.add("moguru-tok");
          span.dataset.lemma = hits[0].lemma;
          span.dataset.band = hits[0].band;
        }
        for (const t of hits) span.classList.add(`moguru-${t.band}`);
      }
      continue;
    }
    // Injected Latin spaces only ever sit BETWEEN items, never inside one,
    // so pageText offsets map onto span text by pure subtraction.
    const span = covered[0];
    const text = span.textContent;
    const frag = document.createDocumentFragment();
    let pos = 0;
    for (const t of hits) {
      const s = Math.max(t.char_start, start) - start;
      const e = Math.min(t.char_end, end) - start;
      if (s > pos) frag.appendChild(document.createTextNode(text.slice(pos, s)));
      const tokEl = document.createElement("span");
      tokEl.className = `moguru-tok moguru-${t.band}`;
      tokEl.dataset.lemma = t.lemma;
      tokEl.dataset.band = t.band;
      tokEl.textContent = text.slice(s, e);
      frag.appendChild(tokEl);
      pos = e;
    }
    if (pos < text.length) frag.appendChild(document.createTextNode(text.slice(pos)));
    span.textContent = "";
    span.appendChild(frag);
  }
}

async function openPdf(file) {
  pagesEl.innerHTML = "";
  const buf = await file.arrayBuffer();
  const pdf = await pdfjsLib.getDocument({ data: buf }).promise;
  for (let n = 1; n <= Math.min(pdf.numPages, 200); n++) {
    try {
      await renderPage(pdf, n); // sequential keeps memory sane
    } catch (e) {
      console.warn(`moguru: page ${n} failed`, e); // one bad page never stops the book
    }
  }
}

// ---------------------------------------------------------------------------
// text view: re-typeset each page's cleaned text — colored glyphs are real
// DOM text, so banding is exact by construction (no canvas-pixel physics).
// ---------------------------------------------------------------------------

function buildTextPage(pageNum, { text, tokens }) {
  const div = document.createElement("div");
  div.className = "textpage";
  div.dataset.page = pageNum;
  // sentences -> paragraphs; track each paragraph's offset in `text` so
  // token char ranges map straight onto the DOM we build
  const paras = [];
  let start = 0;
  for (let i = 0; i < text.length; i++) {
    if ("。！？".includes(text[i])) {
      paras.push([start, i + 1]);
      start = i + 1;
    }
  }
  if (start < text.length) paras.push([start, text.length]);

  const banded = tokens
    .filter((t) => t.band && t.band !== "plain" && t.band !== "known")
    .sort((a, b) => a.char_start - b.char_start);

  for (const [p0, p1] of paras) {
    const p = document.createElement("p");
    let cursor = p0;
    for (const t of banded) {
      if (t.char_end <= p0 || t.char_start >= p1 || t.char_start < cursor) continue;
      if (t.char_start > cursor) p.appendChild(document.createTextNode(text.slice(cursor, t.char_start)));
      const span = document.createElement("span");
      span.className = `moguru-tok moguru-${t.band}`;
      span.dataset.lemma = t.lemma;
      span.dataset.band = t.band;
      span.textContent = text.slice(t.char_start, t.char_end);
      p.appendChild(span);
      cursor = t.char_end;
    }
    if (cursor < p1) p.appendChild(document.createTextNode(text.slice(cursor, p1)));
    if (p.textContent.trim()) div.appendChild(p);
  }
  const num = document.createElement("div");
  num.className = "pgnum";
  num.textContent = `— ${pageNum} —`;
  div.appendChild(num);
  return div;
}

function rebuildTextPages() {
  textPagesEl.innerHTML = "";
  for (const [pageNum, data] of [...pageTexts].sort((a, b) => a[0] - b[0])) {
    textPagesEl.appendChild(buildTextPage(pageNum, data));
  }
}

document.getElementById("viewToggle").addEventListener("click", () => {
  textMode = !textMode;
  if (textMode) rebuildTextPages();
  pagesEl.style.display = textMode ? "none" : "";
  textPagesEl.style.display = textMode ? "" : "none";
  document.getElementById("viewToggle").textContent = textMode ? "原文表示" : "テキスト表示";
  document.getElementById("writingMode").style.display = textMode ? "" : "none";
  window.scrollTo({ top: 0 });
});

let vertical = false;
document.getElementById("writingMode").addEventListener("click", () => {
  vertical = !vertical;
  textPagesEl.classList.toggle("vertical", vertical);
  document.getElementById("writingMode").textContent = vertical ? "横書き" : "縦書き";
  window.scrollTo({ top: 0 });
});

document.getElementById("file").addEventListener("change", (ev) => {
  const file = ev.target.files[0];
  if (file) openPdf(file);
});

document.getElementById("toggle").addEventListener("click", toggleBands);
document.addEventListener("keydown", (ev) => {
  if (ev.altKey && ev.key.toLowerCase() === "m") toggleBands();
});
function toggleBands() {
  bandsOn = !bandsOn;
  document.querySelectorAll(".moguru-tok").forEach((s) => {
    s.classList.toggle("moguru-bands-off", !bandsOn);
  });
}

// right-click menu for PDF words: same stash flow as pages
document.addEventListener("contextmenu", (ev) => {
  const tok = ev.target.closest(".moguru-tok");
  if (!tok) return;
  const sentence = sentenceOf(tok);
  send({
    type: "moguru:stashToken",
    token: { word: tok.textContent, lemma: tok.dataset.lemma, sentence },
  });
}, true);

// right-click stash still works, but the primary interaction is left-click:
document.addEventListener("click", (ev) => {
  if (ev.target.closest?.(".moguru-pop")) return;
  const tok = ev.target.closest?.("span.moguru-tok");
  if (!tok) { closePop(); return; }
  openPop(tok);
}, true);
document.addEventListener("keydown", (ev) => ev.key === "Escape" && closePop());

let popEl = null;
function closePop() { popEl?.remove(); popEl = null; }
function openPop(tok) {
  closePop();
  const word = tok.textContent;
  const lemma = tok.dataset.lemma || word;
  const sentence = sentenceOf(tok);
  popEl = document.createElement("div");
  popEl.className = "moguru-pop";
  const r = tok.getBoundingClientRect();
  popEl.style.left = `${Math.max(8, r.left)}px`;
  popEl.style.top = `${Math.max(8, r.top - 10)}px`;

  const head = document.createElement("div");
  head.className = "moguru-pop-head";
  const w = document.createElement("span"); w.textContent = word;
  const sm = document.createElement("small"); sm.textContent = lemma;
  const x = document.createElement("button"); x.textContent = "×"; x.onclick = closePop;
  head.append(w, sm, x);

  const actions = document.createElement("div");
  actions.className = "moguru-pop-actions";
  const body = document.createElement("div");
  body.className = "moguru-pop-body"; body.style.display = "none";

  const btn = (label, fn) => {
    const b = document.createElement("button");
    b.textContent = label; b.onclick = fn; actions.appendChild(b);
  };
  btn("説明 explain", async (e) => {
    e.target.disabled = true;
    body.style.display = "block"; body.textContent = "…";
    const r2 = await send({ type: "moguru:explain", word, sentence, lemma });
    body.textContent = "";
    if (!r2 || !r2.ok) { body.textContent = `⚠ ${r2?.error || "unreachable"}`; return; }
    const toks = r2.entries?.tokens || [];
    const t = toks.find((t) => t.lemma) || {};
    body.append(kvDiv("reading", t.reading_kana || ""));
    for (const [l, es] of Object.entries(r2.entries?.entries || {})) {
      const gloss = (es[0]?.senses || []).flatMap((s) => s.gloss || []).slice(0, 3).join("; ");
      body.append(kvDiv("JMdict", `${l}: ${gloss}`));
    }
    if (r2.answer) {
      const ans = document.createElement("div");
      ans.style.marginTop = "6px";
      ans.textContent = r2.answer.slice(0, 900);
      body.appendChild(ans);
    }
  });
  btn("Anki", async (e) => {
    e.target.disabled = true;
    // target = the clicked SURFACE form — /mine's target path matches it in
    // the sentence (the lemma often isn't present in inflected text)
    const r2 = await send({ type: "moguru:mine", text: sentence, target: word, add: true });
    body.style.display = "block"; body.textContent = "";
    const item = (r2?.data?.results || [])[0];
    body.textContent = item?.note_id
      ? `✔ card #${item.note_id} — ${item.candidate.target}`
      : `⚠ ${item?.error || r2?.error || "no candidate"}`;
  });
  btn("既知 known", async (e) => {
    e.target.disabled = true;
    await send({ type: "moguru:mark", lemma });
    closePop();
  });
  popEl.append(head, actions, body);
  pagesEl.appendChild(popEl);
}
function kvDiv(k, v) {
  const div = document.createElement("div");
  const b = document.createElement("b"); b.textContent = `${k}: `;
  div.append(b, document.createTextNode(v || ""));
  return div;
}

function sentenceOf(tok) {
  const para = tok.closest("p");
  const raw = (para && para.closest(".textpage"))
    ? para.textContent
    : (tok.closest(".page")?.innerText || tok.textContent || "");
  // text-layer runs inject whitespace between EVERYTHING (span boundaries) —
  // 「あ る こ と」 is garbage for the model, the card Sentence field, and
  // /mine target matching. Spaces between CJK chars never occur in real
  // prose, so collapse them; keep real Latin word spacing intact.
  return raw
    .replace(/(?<=[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff])\s+(?=[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff])/g, "")
    .replace(/\s+/g, " ")
    .trim()
    .slice(0, 300);
}

// panel display on the extension page: background broadcasts via
// chrome.runtime.sendMessage to extension contexts (content scripts get the
// tabs.sendMessage copy instead).
chrome.runtime.onMessage.addListener((msg) => {
  if (msg.type === "moguru:panel") showPanel(msg.panel);
});

function showPanel(panel) {
  document.querySelector(".moguru-panel")?.remove();
  const el = document.createElement("div");
  el.className = "moguru-panel";
  el.style.position = "fixed";
  el.style.zIndex = 9999;
  const head = document.createElement("div");
  head.className = "moguru-panel-head";
  head.textContent = `潜る — ${panel.word || ""}`;
  const close = document.createElement("button");
  close.textContent = "×";
  close.onclick = () => el.remove();
  head.appendChild(close);
  const body = document.createElement("div");
  body.className = "moguru-panel-body";
  if (panel.error) {
    body.textContent = `⚠ ${panel.error}`;
  } else if (panel.mined || panel.minedError) {
    if (panel.minedError) body.textContent = `⚠ ${panel.minedError}`;
    for (const r of panel.mined || []) {
      const div = document.createElement("div");
      div.textContent = `✔ ${r.candidate.sentence} → card #${r.note_id} (${r.candidate.target})`;
      body.appendChild(div);
    }
  } else {
    const pre = document.createElement("div");
    pre.textContent = panel.answer || JSON.stringify(panel.entries).slice(0, 600);
    body.appendChild(pre);
  }
  el.append(head, body);
  document.body.appendChild(el);
  setTimeout(() => el.remove(), 60_000);
}
