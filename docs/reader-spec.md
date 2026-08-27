# Enhancement · Moguru Reader

*A color-coded in-browser reader for PDFs and docs: see i+1 at a glance,
right-click any word for an explanation, send the sentence to Anki.*

This is an **ambient-presence surface** (a client over the engine). It adds
no new "brain" — it renders a document and calls the existing engine to
classify and explain. Self-contained; drop it in alongside the other
surfaces.

---

## 1. What it does

Open a PDF or web document in the browser and every word is painted by how
hard it is **for you**:

- **Known** — in your known-set → unstyled (clean page).
- **i+1** — the one unknown word in an otherwise-known sentence → highlighted
  as a mining target. This is the actionable band: "learn this now."
- **New / hard** — unknown, in a sentence with several unknowns → flagged in
  a different color: "too hard for now, come back later."

The result is a heatmap of your reading: sparse i+1 highlights are your sweet
spot; dense new/hard clusters tell you the passage is above your level.
Right-click any word to get a grounded explanation or send the sentence to
Anki as a card.

## 2. Color-band classification

Per token from the parser, plus a per-sentence unknown count:

```
def band(token, sentence):
    if kb.is_known(token.lemma).known:        return "known"
    unknowns = count_unknown_content_words(sentence)   # via kb.is_known
    if unknowns <= mining.iplus_threshold:    return "iplus"      # actionable
    return "new_hard"
```

Only content words are colored (particles/aux left plain, via POS). Bands and
default colors (make them user-configurable):

| Band | Meaning | Default style |
|---|---|---|
| `known` | you know it | none |
| `iplus` | the single new word here — mine it | green underline / highlight |
| `new_hard` | new, but sentence is too hard yet | amber / red highlight |

> **Shadow-model hook:** once `shadow-mcp` exists, add a `known_unstable`
> band — a word you know on paper but the shadow model says you actually
> stumble on. Render it as a subtle dotted marker so "false-known" words
> surface while you read.

## 3. Engine API addition

One new endpoint on the local engine service; everything else reuses
existing tools.

```
POST /annotate
  body:   { text, page?: int }              # send a page (PDF) or a chunk (web doc)
  return: {
    tokens: [ { surface, char_start, char_end, lemma, reading, band } ],
    sentences: [ { char_start, char_end, unknown_count } ]
  }
```

Internally: `parser.tokenize` → `kb.is_known` per lemma → sentence unknown
counts → band per token. Reuse the `comprehensibility` skill's logic; don't
reinvent it.

Right-click actions map to endpoints you already have:

| Menu item | Call |
|---|---|
| **Explain** | `/lookup { word }` (reading + definition) + `/ask { question, context: sentence }` (grammar in context) |
| **Send to Anki** | `/mine { text: sentence, target: word }` → `srs.add_card` |
| **Mark known** | `kb.mark_known(lemma, "reader")` → clears the highlight everywhere |
| **Play audio** *(opt.)* | `media.extract_audio` if a source is attached |

## 4. Rendering

- **PDFs:** render with **PDF.js**; it exposes a selectable text layer. Wrap
  each token span in the text layer and apply the band style. (Scanned PDFs
  have no text layer → run `media.ocr_image` first, then annotate the OCR
  output.)
- **Web docs / HTML:** a content script walks visible text nodes and wraps
  tokens in styled `<span>`s — the Yomitan/browser-extension approach. Skip
  inputs, code blocks, and already-processed nodes.
- **Legend + toggle:** a small fixed legend explains the colors; a hotkey
  toggles highlighting off for distraction-free reading.

## 5. Performance & correctness

- **Annotate by viewport/page, not whole document** — request `/annotate`
  per visible page or chunk, lazily as the reader scrolls. Debounce.
- **Fast membership:** back `kb.is_known` with a bloom filter + table so
  per-token checks are cheap across a full page.
- **Cache** annotations per page keyed by a hash of (text + known-set
  version).
- **Re-annotate on change:** when you mine or mark a word known, bump the
  known-set version and repaint affected pages incrementally — a word you
  just learned should stop glowing.

## 6. Build steps

1. **`/annotate` endpoint** — parser + kb + band classifier. Testable with
   curl before any UI.
2. **Reader shell** — PDF.js viewer (and/or the HTML content script). Get
   text on screen.
3. **Paint bands + legend** — apply styles from `/annotate`; add the toggle.
4. **Right-click menu** — wire Explain / Send to Anki / Mark known to the
   endpoints above.
5. **Incremental re-annotate** — repaint on known-set version change.
6. *(later)* **Shadow band** — add `known_unstable` once `shadow-mcp` is
   live.

Each step is demo-able on its own; step 1 is useful even from the command
line.
