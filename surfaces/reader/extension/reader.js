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
  const bodyItems = items.filter((i) => !isFurigana(i));

  for (const item of items) {
    const span = document.createElement("span");
    const tx = pdfjsLib.Util.transform(viewport.transform, item.transform);
    const angle = Math.atan2(tx[1], tx[0]);
    const fontHeight = Math.hypot(tx[2], tx[3]);
    const left = tx[4], top = tx[5] - fontHeight;
    span.textContent = item.str;
    span.style.left = `${left}px`;
    span.style.top = `${top}px`;
    span.style.fontSize = `${fontHeight}px`;
    if (angle) span.style.transform = `rotate(${angle}rad)`;
    if (isFurigana(item)) span.dataset.furigana = "1";
    textLayer.appendChild(span);
  }

  // annotate this page's body text (furigana excluded) and band the spans
  const pageText = bodyItems.map((i) => i.str).join("");
  try {
    const data = await annotateText(pageText);
    if (data) applyBands(textLayer, bodyItems, data.tokens);
  } catch (e) {
    console.warn("moguru annotate failed", e);
  }
}

// Map engine token offsets back onto textLayer spans. Offsets and the joined
// text come from the same bodyItems list, so they align by construction.
function applyBands(textLayer, items, tokens) {
  const spans = [...textLayer.querySelectorAll("span")].filter(
    (s) => s.dataset.furigana !== "1"
  );
  let cursor = 0;
  const spanRanges = [];
  for (const item of items) {
    const span = spans.shift();
    if (!span) break;
    spanRanges.push({ span, start: cursor, end: cursor + item.str.length });
    cursor += item.str.length;
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
    await renderPage(pdf, n); // sequential keeps memory sane
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
