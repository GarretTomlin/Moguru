// Moguru PDF reader — extension page rendering PDFs with pdf.js, annotating
// the selectable text layer with engine bands. Extension pages share the
// background worker's engine access via chrome.runtime messages.

import * as pdfjsLib from "./lib/pdf.min.mjs";
pdfjsLib.GlobalWorkerOptions.workerSrc = "lib/pdf.worker.min.mjs";

const pagesEl = document.getElementById("pages");
let bandsOn = true;
let knownVersion = null;

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

  // Manual text layer with the official viewer's alignment trick: after
  // laying out each span (embedded fontFamily when available), MEASURE it
  // and scale to the item's declared width. That is what makes colored
  // glyphs sit precisely on the canvas glyphs — no offset ghost copy —
  // without depending on pdf.js internals.
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
    if (angle) span.dataset.angle = angle;
    textLayer.appendChild(span);

    // fit-to-width: scale the rendered run to the PDF's declared width.
    // item.width's unit convention varies; pick whichever candidate ratio
    // is sane.
    const measured = span.getBoundingClientRect().width;
    if (measured > 0 && item.width > 0) {
      for (const expected of [item.width * viewport.scale, item.width]) {
        const sx = expected / measured;
        if (sx >= 0.6 && sx <= 1.67) {
          span.style.transform =
            (angle ? `rotate(${angle}rad) ` : "") + `scaleX(${sx})`;
          break;
        }
      }
    }
    pageSpans.push(span);
  }

  // zip spans with the FULL items array (same order), mark excluded runs,
  // and build the body-span list the band offsets will map onto.
  const spanByItem = new Map();
  const fullItems = textContent.items;
  for (let k = 0; k < pageSpans.length && k < fullItems.length; k++) {
    spanByItem.set(fullItems[k], pageSpans[k]);
    if (isFurigana(fullItems[k]) || isPageFurniture(fullItems[k])) {
      pageSpans[k].dataset.furigana = "1";
    }
  }
  const bodySpans = bodyItems
    .map((i) => spanByItem.get(i))
    .filter(Boolean);

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
        applyBands(bodySpans, bodyItems, pageText, data.tokens);
        break;
      }
    } catch (e) {
      if (attempt === 1) console.warn("moguru annotate failed", e);
    }
  }
}

// Band the official text-layer spans. spans/bodyItems/pageText derive from
// the SAME join (injected Latin spaces included), so token ranges align.
function applyBands(spans, items, pageText, tokens) {
  let cursor = 0;
  const spanRanges = [];
  for (let k = 0; k < items.length && k < spans.length; k++) {
    spanRanges.push({
      span: spans[k],
      start: cursor,
      end: cursor + items[k].str.length,
    });
    cursor += items[k].str.length;
    // consume any space(s) injected between Latin runs before the next item
    while (cursor < pageText.length && pageText[cursor] === " ") cursor += 1;
  }
  for (const tok of tokens) {
    if (!tok.band || tok.band === "plain" || tok.band === "known") continue;
    for (const { span, start, end } of spanRanges) {
      if (tok.char_end <= start || tok.char_start >= end) continue;
      if (!span.classList.contains("moguru-tok")) {
        span.classList.add("moguru-tok");
        span.dataset.lemma = tok.lemma;
        span.dataset.band = tok.band;
      }
      span.classList.add(`moguru-${tok.band}`);
    }
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
    const r2 = await send({ type: "moguru:mine", text: sentence, target: lemma, add: true });
    body.style.display = "block"; body.textContent = "";
    const item = (r2?.data?.results || [])[0];
    body.textContent = item?.note_id
      ? `✔ card #${item.note_id} — ${item.candidate.target}`
      : `⚠ ${item?.error || "no candidate"}`;
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
  const page = tok.closest(".page");
  const text = (page?.innerText || tok.textContent || "").replace(/\s+/g, " ").trim();
  return text.slice(0, 300);
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
