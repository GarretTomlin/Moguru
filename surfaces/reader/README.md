# Moguru Reader — extension install guide

See i+1 at a glance on any Japanese page or PDF: known words stay clean,
**green** = the one new word in an otherwise-known sentence (mine it now),
**amber** = this sentence is above your level, **dotted violet** = you know
it on paper but stumble on it in practice (the shadow model's call).

Right-click any word → **Explain** (reading + definition + grounded grammar
answer) · **Send to Anki** (i+1 card) · **Mark known** (clears the highlight
everywhere).

## Install

**Prerequisite:** the engine running locally —

```bash
cd Moguru
uv run moguru serve        # starts http://localhost:8766 — leave it running
```

**Load the extension (Chrome / Edge / Brave):**

1. Open `chrome://extensions` (Edge: `edge://extensions`).
2. Turn on **Developer mode** (top-right toggle).
3. Click **Load unpacked**.
4. Select this folder: `Moguru/surfaces/reader/extension/`.
5. Done. Pin "Moguru Reader" if you want quick access to its options.

Nothing is sent anywhere except to your own engine on `localhost`.

## Use

- **Browse any Japanese page.** Words are color-coded as you scroll
  (lazy — only what's near the viewport gets annotated).
- **Right-click a colored word** for the menu. *Send to Anki* creates the
  card for exactly the word you clicked, sentence included, and the page
  repaints (the word stops glowing because you're now learning it).
- **Alt+M** toggles all highlighting off for distraction-free reading.
- **Options** (click the extension icon): engine URL, band colors, on/off.
- **PDFs:** click the extension icon → **Moguru PDF reader…** → open a PDF.
  It renders with PDF.js and annotates the selectable text layer the same
  way. (Scanned PDFs have no text layer — those need OCR, which is not wired
  into the reader yet.)

## How it behaves

- The content script only touches visible Japanese text — inputs, code
  blocks, and already-processed nodes are skipped.
- Annotations are cached per text chunk and invalidated when your known set
  changes (mine or mark a word → affected pages repaint).
- While you read, the extension quietly emits behavioral signals (hover,
  lookup, clean pass-through) to the engine — that's the data your
  [shadow model](../../docs/shadow-spec.md) learns your *actual*
  comprehension from. It stays in `data/user/shadow.sqlite` on your machine.

## Troubleshooting

| Symptom | Fix |
|---|---|
| Nothing gets colored | Is `moguru serve` running? Check <http://localhost:8766/health> |
| "engine 403/EOF" in panel | Engine URL in options doesn't match the port you started |
| Page still stale after marking known | Reload the page — caches key on the known-set version and refresh on the next annotate |
| Anki card didn't appear | Anki must be **running** with AnkiConnect (`:8765`); check `moguru doctor` |

## Uninstall

`chrome://extensions` → Moguru Reader → Remove. (Your Anki cards and engine
data are untouched.)
