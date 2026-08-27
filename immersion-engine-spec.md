# Immersion Engine — Build Specification

> **潜る · Moguru** — an immersion engine for Japanese.

A local, model-driven Japanese immersion assistant built on the AJATT / MIA /
Migaku philosophy. A language model orchestrates a set of **MCP tools**
(deterministic data: parsing + dictionaries + the learner's knowledge state)
and a set of **Skills** (procedures: sentence mining, RTK, comprehensibility
scoring). New capabilities are added as plug-in MCP servers or skills.

> **Reader:** this document is written for the agent that will implement the
> system. It specifies interfaces, data shapes, and build order — not final
> code.

## Companion documents

- [Getting started & usage](docs/getting-started.md) — install, Reader
  extension, daily commands, model swapping
- [Models & Providers spec](docs/models-providers-spec.md) — runtime model
  swapping (main + shadow roles, any provider)
- [Reader spec](docs/reader-spec.md) — the color-coded i+1 browser reader
  (Phase 2 surface)
- [Install & Orchestration spec](docs/install-orchestration-spec.md) — bundle
  manifest, bootstrap installer, host adapters, doctor
- [Shadow Model spec](docs/shadow-spec.md) — the behavioral comprehension
  model (Phase 3)
- [INSTALL.md](INSTALL.md) — the agent-executable install recipe
- [Reader extension guide](surfaces/reader/README.md)

---

## 0. Governing principles (do not violate)

1. **Facts in tools, judgment in the model.** Dictionary entries, readings,
   frequencies, and "does the user know this word" are ALWAYS resolved by MCP
   tool calls against real data. The model must never recite dictionary
   content from its own weights. A tool call returning ground truth is
   required before any reading, definition, or frequency is shown to the
   learner. This is a correctness requirement, not a style choice — a tool
   that teaches a wrong reading is worse than no tool.
2. **The parser is the foundation.** Japanese has no word boundaries. Nothing
   downstream (lookup, i+1, mining) works until text is tokenized and
   deinflected. Build this first.
3. **Adaptivity comes from the knowledge store.** "i+1" is defined *relative
   to what this user knows*. The known-words/known-kanji store is what makes
   this an immersion tool rather than a dictionary.
4. **Model is swappable.** All model-specific behavior sits behind an
   OpenAI-compatible endpoint. Swapping local ↔ larger model must require
   zero changes to tools or skills.
5. **Generated capability is reviewed, never silent.** The "self-evolving"
   layer may draft skills/tools, but generated artifacts land in staging and
   require human approval before mounting. Generated code may never override
   a ground-truth dictionary tool.

---

## 1. Configuration (parameters the builder must expose)

These were left open; expose them as config with the given defaults.

| Key | Default | Options / notes |
|---|---|---|
| `model.local.endpoint` | `http://localhost:11434/v1` | Ollama / LM Studio / llama.cpp, OpenAI-compatible |
| `model.local.name` | `qwen3.6-27b` (or quantized build) | Qwen line is strongest open-weight for JA; Gemma 4 as laptop fallback |
| `model.strong.endpoint` | *unset* | Optional route for hard grammar reasoning (hybrid) |
| `model.routing` | `local_first` | `local_only` \| `local_first` \| `strong_only` |
| `parser.engine` | `mecab_unidic` | `mecab_unidic` (via fugashi) \| `sudachi` \| `ichiran` |
| `srs.backend` | `anki` | `anki` (AnkiConnect) \| `builtin` (FSRS) \| `none` |
| `mining.iplus_threshold` | `1` | Max unknown content words for an i+1 candidate |
| `mining.sentence_len` | `[4, 25]` | Min/max tokens for a minable sentence |
| `defs.mode` | `bilingual` | `bilingual` \| `mixed` \| `monolingual` (transition policy) |
| `model.shadow.endpoint` | `http://localhost:11435/v1` | Dedicated small model for the shadow layer (Phase 3), separate from the orchestrator |
| `model.shadow.name` | `qwen3-8b` | 8B–12B, always-on comprehension inference; keep it small, local, private |
| `shadow.min_samples` | `4` | Encounters before a comprehension estimate is reported as anything but low-confidence |

---

## 2. System architecture

```
                         ┌────────────────────────────┐
                         │      Orchestrator (host)     │
                         │  model + agent loop + skills │
                         └──────────────┬───────────────┘
                                        │ MCP
        ┌───────────────┬───────────────┼───────────────┬───────────────┐
        ▼               ▼               ▼               ▼               ▼
  ┌───────────┐  ┌───────────┐   ┌────────────┐   ┌──────────┐   ┌───────────┐
  │  parser   │  │   dict    │   │ frequency  │   │   kb      │   │   srs     │
  │   MCP     │  │   MCP     │   │    MCP     │   │  MCP      │   │   MCP     │
  │ tokenize/ │  │ JMdict /  │   │ BCCWJ /    │   │ known set │   │ Anki or   │
  │ deinflect │  │ JMnedict/ │   │ JPDB v2    │   │ + stats   │   │ FSRS      │
  │ reading   │  │ KANJIDIC/ │   └────────────┘   └──────────┘   └───────────┘
  └───────────┘  │ Jiten     │
                 └───────────┘         ┌────────────┐   ┌──────────────────┐
                                       │  media MCP │   │  plugin registry │
                                       │ subs/OCR/  │   │  discovers + mounts│
                                       │ audio      │   │  MCP servers/skills│
                                       └────────────┘   └──────────────────┘

Skills (SKILL.md workflows, consumed by the orchestrator):
  sentence-mining · rtk-kanji · comprehensibility · monolingual-transition · card-format
```

---

## 3. MCP servers (tools + signatures)

Signatures are language-agnostic: `name(args) -> return`. Implement in Python
(richest JA NLP ecosystem). Back all dictionaries with SQLite (+ FTS5), or
reuse the Yomitan term-bank format if importing existing dictionary packages.

### 3.1 `parser-mcp` — morphological analysis (BUILD FIRST)
Wraps MeCab+UniDic (via `fugashi`) or SudachiPy. UniDic is preferred because
it carries lemma, reading, and pitch-accent.

```
tokenize(text) -> [ Token ]
  Token = {
    surface, lemma, reading_kana, pos, pos_detail,
    inflection_type, base_form, pitch_accent?, char_start, char_end
  }

deinflect(surface) -> [ candidate_lemma ]          # 見た -> 見る, etc.
segment_sentences(text) -> [ sentence ]
to_reading(text, mode = "hiragana"|"katakana"|"romaji") -> string   # furigana
```

### 3.2 `dict-mcp` — reference lookups (ground truth)
One server, multiple tools, one per source the user listed.

```
lookup_word(query, reading?) -> [ Entry ]          # JMdict (J-E, primary)
  Entry = { headwords[], readings[], senses[{gloss[], pos[], misc[], field[]}], id }

lookup_name(query) -> [ NameEntry ]                # JMnedict (people/places)
lookup_kanji(char) -> KanjiEntry                   # KANJIDIC2
  KanjiEntry = {
    char, on_readings[], kun_readings[], meanings[], nanori[],
    stroke_count, grade, jlpt, freq_rank, radicals[], components[]
  }

lookup_monolingual(query) -> [ JitenEntry ]        # Jiten (J-J, for monolingual mode)
decompose_kanji(char) -> [ Component ]             # radicals/primitives, feeds RTK stories
```

### 3.3 `freq-mcp` — frequency data
```
frequency(lemma, reading?) -> { bccwj_rank?, jpdb_rank?, jpdb_freq_class? }
rank_by_frequency([lemma]) -> [ {lemma, jpdb_rank} ] sorted ascending
```
Learn-frequent-first ordering is driven here.

### 3.4 `kb-mcp` — the learner's knowledge state (makes it adaptive)
SQLite. This is the store the whole system revolves around.

```
is_known(lemma) -> { known, strength, source, first_seen, srs_state? }
get_known_set(filter?) -> [ lemma ]                # fast membership (bloom + table)
known_kanji() -> [ char ]
mark_known(lemma, source)                          # source: "anki" | "manual" | "mined"
record_encounter(lemma, context_sentence)          # for +freq / maturity tracking
stats() -> { known_words, known_kanji, encounters, ... }
```

**Schema (SQLite):**
```sql
CREATE TABLE known_words (
  lemma TEXT PRIMARY KEY, reading TEXT, strength REAL DEFAULT 0,
  source TEXT, first_seen TEXT, last_seen TEXT, encounter_count INTEGER DEFAULT 0,
  srs_note_id INTEGER
);
CREATE TABLE known_kanji (char TEXT PRIMARY KEY, source TEXT, first_seen TEXT);
CREATE TABLE encounters (
  id INTEGER PRIMARY KEY, lemma TEXT, sentence TEXT, media_ref TEXT, ts TEXT
);
```

### 3.5 `srs-mcp` — spaced repetition
Two interchangeable backends selected by `srs.backend`.

```
add_card(deck, note_type, fields{}, tags[]) -> note_id
find_notes(query) -> [ note_id ]
update_note(note_id, fields{})
due_cards(deck) -> [ Card ]
import_known() -> [ lemma ]     # extract learned lemmas -> feeds kb-mcp.mark_known
```
- `anki` backend: talk to **AnkiConnect** (localhost:8765). `import_known`
  reads mature cards' target-word fields.
- `builtin` backend: implement an **FSRS** scheduler over a local `cards`
  table.

### 3.6 `media-mcp` — content pipeline (core; later step of Phase 1)
```
parse_subtitles(file) -> [ {start, end, text} ]    # .srt / .ass
extract_audio(media, start, end) -> audio_path      # sentence audio for cards
ocr_image(image) -> text                            # manga / screenshots
capture_context(media, timestamp) -> image_path
```

### 3.7 `shadow-mcp` — comprehension shadow model (Phase 3)
A **second, behavioral** model of the learner, separate from the flashcard
known-set. `kb-mcp` records what you've made cards for; `shadow-mcp` infers
what you *actually understand in flowing native content* from how you behave
while immersing. The gap between the two is the whole point.

Runs its **own dedicated small model** (8B–12B at `model.shadow.endpoint`)
doing nothing but comprehension inference. It sits on a constant stream of
tiny events, so it must be cheap, always-on, local, and private — never the
orchestrator's job.

```
record_signal(Signal)                              # ingest one behavioral event
comprehension(key, modality) -> Estimate           # key = lemma | grammar_point
  Estimate = { p_understood, confidence, sample_size }
predict_friction(sentence, modality) -> [ Friction ]
  Friction = { span, type: "vocab"|"grammar"|"parse_speed", p_break, reason }
gaps(filter?) -> [ { key, srs_known: bool, p_understood, modality } ]
comprehension_map() -> HeatmapData                 # the shareable, versioned artifact
```

**Signal schema (emitted by Phase 2 surfaces):**
```
Signal = {
  type: "hover"|"pause"|"rewind"|"replay"|"lookup"|"mine"|"skip"|"complete",
  lemma?, grammar_point?, sentence, modality: "reading"|"listening",
  dwell_ms?, playback_speed?, media_ref, ts
}
```

**Design requirements (non-negotiable):**
- **Modality split.** Track reading vs listening comprehension *separately* —
  the most common real gap is "knows it on the page, misses it by ear." A
  single score hides this.
- **Probabilistic, not asserted.** Behavior is noisy: a pause may be thought,
  not confusion. Estimates carry a `confidence` and a `sample_size`; below
  `shadow.min_samples` they report low-confidence. Cross-check inferences
  against hard events (an actual `lookup` or `mine` is strong evidence; a
  bare pause is weak).
- **Predictive hook.** `predict_friction` lets the `sentence-mining` and
  `comprehensibility` skills simulate the learner's read of a sentence
  *before* they hit it and pre-empt the word/grammar that will break them —
  the signature "it read my mind" behavior.
- **Private by construction.** This is the most personal data in the system;
  it never leaves the machine.

*(Full statistical model, data schema, calibration loop, and build steps:
[docs/shadow-spec.md](docs/shadow-spec.md).)*

---

## 4. Skills (SKILL.md workflows)

Each is a folder with a `SKILL.md` (procedure the model follows) and optional
helper scripts. Skills call MCP tools; they hold *policy*, not data.

### 4.1 `sentence-mining` — the core loop
Given a text or subtitle line:
1. `parser-mcp.segment_sentences` → per sentence:
2. `parser-mcp.tokenize` → tokens.
3. Filter to **content words** (drop particles/aux/symbols by POS).
4. For each content lemma: `kb-mcp.is_known`.
5. Compute `unknown_count`. Candidate if
   `unknown_count <= mining.iplus_threshold` AND sentence length ∈
   `mining.sentence_len`.
6. For each candidate's target word: `dict-mcp.lookup_word`,
   `freq-mcp.frequency`, and (if media) `media-mcp.extract_audio` /
   `capture_context`.
7. Emit a card via the `card-format` skill; register with `srs-mcp.add_card`
   and `kb-mcp.record_encounter`.

Rank multiple candidates by target-word frequency (frequent first) then by
known-coverage. Encodes MIA preference: **sentence cards, one target word
each.**

**i+1 selection (pseudocode):**
```
def is_iplus(sentence):
    toks = tokenize(sentence)
    content = [t for t in toks if t.pos in CONTENT_POS]
    unknown = [t for t in content if not kb.is_known(t.lemma).known]
    if not (LEN_MIN <= len(toks) <= LEN_MAX): return None
    if len(unknown) > IPLUS_THRESHOLD: return None
    target = unknown[0] if unknown else None       # 0 unknown = review/known-good
    return Candidate(sentence, target,
                     score=rank(freq(target), coverage=len(content)-len(unknown)))
```

### 4.2 `rtk-kanji`
RTK/Heisig ordering; `dict-mcp.decompose_kanji` for primitives; model
generates a mnemonic **story** from keyword + components; `kb-mcp.known_kanji`
gates which primitives are assumed known. Produces RTK-style cards (keyword →
kanji) or the reverse.

### 4.3 `comprehensibility`
Score a passage before the learner dives in:
```
{ pct_known, iplus_density, unknown_words[], verdict }
verdict ∈ { too_easy, iplus_sweet_spot, too_hard }
```
Uses `tokenize` + `kb.is_known` across the whole text. This is the "is this
content at my level" gate central to comprehensible-input theory.

### 4.4 `monolingual-transition`
Policy that shifts `defs.mode` bilingual → mixed → monolingual as `kb.stats`
crosses thresholds (e.g. > N known words → start showing `lookup_monolingual`
J-J defs with J-E fallback). Encodes the AJATT monolingual endgame.

### 4.5 `card-format`
Defines note types / field templates (Migaku-style). Canonical target-word
note:
```
Fields: Sentence, TargetWord, Reading, Definition, Audio, Image, PitchAccent, Source
```
All skills that create cards use this one definition so formats stay
consistent.

---

## 5. Plugin registry & the "self-evolving" layer

### 5.1 Discovery
`plugins/` directory. Each plugin is a folder with a manifest:
```json
{
  "name": "wanikani-radicals",
  "type": "mcp" | "skill",
  "version": "0.1.0",
  "entry": "server.py" | "SKILL.md",
  "tools": [ { "name": "...", "description": "..." } ],
  "provides_ground_truth": false
}
```
On startup the registry reads every manifest, mounts MCP servers, and exposes
skills to the orchestrator. Adding capability = drop a folder in, restart.

### 5.2 Evolution phases (build in this order, gate each)
- **Phase A — Adaptive (build first).** No code generation. The system tunes
  what it mines and how it explains from `kb.stats` + SRS performance (e.g.
  raise `iplus_threshold` as coverage grows; switch `defs.mode`). This is the
  real AJATT payoff.
- **Phase B — Assisted authoring.** Model may DRAFT a new `SKILL.md` or tool
  stub → written to `staging/`, never auto-mounted. Human approves → moves to
  `plugins/`.
- **Phase C — Guarded auto-tuning.** Model may propose parameter changes to
  existing skills within declared bounds; every change is logged, versioned,
  reversible.

**Rails:** version and changelog everything; generated code runs sandboxed; a
plugin with `provides_ground_truth: false` may never shadow a
dictionary/parser tool; generated skills cannot alter Phase-0 principles.

---

## 6. Recommended tech stack

- **MCP servers:** Python + official MCP SDK. JA libs: `fugashi`+`unidic`,
  `sudachipy`, `jamdict` (JMdict/JMnedict/KANJIDIC access), `jaconv`
  (kana/romaji).
- **Dictionary storage:** SQLite w/ FTS5, or import Yomitan term-bank
  packages.
- **Model host:** Ollama or LM Studio (OpenAI-compatible). llama.cpp for
  tight VRAM.
- **Orchestrator:** an MCP host with an agent loop (any MCP-capable client,
  or custom).
- **SRS bridge:** AnkiConnect (`anki` backend) or an FSRS implementation
  (`builtin`).
- **Media:** `ffmpeg` (audio slicing), a subtitle parser, a JA-capable OCR
  (`manga-ocr`).

---

## 7. Repository layout

```
moguru/
├── config.yaml                     # §1 parameters
├── orchestrator/                   # agent loop, model routing, skill loading
├── mcp/
│   ├── parser_mcp/
│   ├── dict_mcp/                   # JMdict, JMnedict, KANJIDIC, Jiten
│   ├── freq_mcp/                   # BCCWJ, JPDB v2
│   ├── kb_mcp/                     # known-words store (§3.4 schema)
│   ├── srs_mcp/                    # anki | builtin
│   ├── media_mcp/                  # subs/OCR/audio (Phase 1, later step)
│   └── shadow_mcp/                 # comprehension shadow model (Phase 3, §3.7)
├── skills/
│   ├── sentence-mining/SKILL.md
│   ├── rtk-kanji/SKILL.md
│   ├── comprehensibility/SKILL.md
│   ├── monolingual-transition/SKILL.md
│   └── card-format/SKILL.md
├── surfaces/                       # Phase 2 ambient presence (§8)
│   ├── overlay/                    # browser/video subtitle overlay
│   ├── menubar/                    # desktop companion
│   ├── ocr_watcher/                # screen-capture reader (games/manga)
│   └── mobile_share/               # phone share-sheet target
├── plugins/                        # dropped-in MCP servers / skills (§5)
├── staging/                        # Phase B drafts awaiting approval
└── data/
    ├── dictionaries/               # SQLite / term-banks (not in git)
    └── user/                       # kb.sqlite, srs.sqlite, logs (not in git)
```

---

## 8. Ambient presence layer (Phase 2)

Turns the engine from a place you *go to* into a layer that *rides on top of
your immersion*. Architecturally this is a thin client tier — it adds
surfaces and an engine API, and reuses every Phase 1 tool and skill unchanged.
The brain does not move; only the point of contact does.

### 8.1 Engine service boundary (prerequisite)
Phase 1's orchestrator is wrapped in a local service (socket or `localhost`
HTTP) so any surface can drive it:
```
POST /lookup      { text }            -> tokens + entries       (parser + dict)
POST /mine        { text, media_ref? }-> candidates + cards      (sentence-mining)
POST /assess      { text }            -> comprehensibility verdict
POST /ask         { question, context}-> grounded explanation
```
Surfaces are dumb clients of these endpoints; all logic stays in the engine.

### 8.2 Surfaces
- **Overlay** — subtitle/text overlay in the browser or a video player. Hover
  a word → `/lookup`; click → `/mine`. The core immersion interaction; the
  same pattern Yomitan and Migaku proved.
- **Menu-bar companion** — a persistent desktop presence: global hotkey to
  mine the current selection, quick lookup, "assess this page" — no window
  to open.
- **OCR watcher** — captures a screen region and runs `media-mcp.ocr_image`
  for content with no text layer (games, manga, raw video). Feeds the same
  `/mine` path.
- **Mobile share-sheet** — registers as a share target so text from any
  phone app can be sent to the engine.

### 8.3 Design rule: the surface stays out of the way
The immersion is primary; the surface is secondary. It must not pull the
learner out of native content into English chatter. Concretely: quiet by
default, act only on the learner's gesture (hover/click/hotkey), and never
auto-open panels mid-scene. (Proactive "it noticed" behavior — speaking up
unprompted — is deliberately **not** in Phase 2; it belongs to a later
companion phase with its own interruption budget, so it can't be built before
the surfaces it would speak through exist.)

---

## 9. Build order (hand this sequence to the builder)

Three top-level phases. **Ship and use each phase before starting the next** —
Phase 2's surfaces are a client layer over the Phase 1 engine, and Phase 3's
shadow model is fed by the behavioral signals Phase 2's surfaces emit, so it
cannot exist before them.

### Phase 1 — Core engine (chat-driven)
The complete working system, driven by a text/chat interface.

1. **`parser-mcp`** + **`dict-mcp`** — tokenize/deinflect + JMdict/KANJIDIC
   lookup. Testable immediately: paste a sentence, get tokens with readings
   and definitions.
2. **`kb-mcp`** + **`srs-mcp.import_known`** — stand up the knowledge store
   and seed it from the user's existing Anki. Now "known vs unknown" works.
3. **`sentence-mining` skill** + i+1 algorithm + `card-format` — the core
   loop end to end.
4. **`freq-mcp`** — wire frequency into mining rank order.
5. **`rtk-kanji` skill**.
6. **`comprehensibility`** + **`monolingual-transition`** skills.
7. **`media-mcp`** — subtitles → audio/screenshot → richer cards.
8. **Plugin registry** + **Phase A adaptivity**. Then Phase B/C behind
   approval.

Each step is independently demonstrable; do not proceed to mining (step 3)
before the parser and knowledge store (steps 1–2) return correct data, since
mining correctness depends entirely on them.

### Phase 2 — Ambient presence (live where you immerse)
Move the engine out of the chat box and onto the content (§8). No new brain —
these are **client surfaces** that call the same MCP tools and skills built
in Phase 1. Build in this order:

9.  **Engine service boundary** — expose the Phase 1 orchestrator over a
    local socket/HTTP API so external surfaces can drive it.
10. **Overlay surface** — browser/video subtitle overlay: hover a word →
    lookup, click → mine.
11. **Menu-bar companion** — always-available desktop capture point.
12. **OCR watcher** — screen-capture reader via `media-mcp.ocr_image`.
13. **Mobile share-sheet** — send text from any phone app into the engine.

Each surface is independently shippable and degrades gracefully. Every
surface also **emits behavioral signals** (`shadow-mcp` schema) as a side
effect of use — this is the data Phase 3 runs on, so instrument it here even
though nothing consumes it yet.

### Phase 3 — Comprehension shadow model (`shadow-mcp`, §3.7)
The behavioral model of what the learner actually understands, on its own
dedicated small model. Build in this order:

14. **`shadow-mcp` store + signal ingestion** — `record_signal`; persist the
    event stream from Phase 2 surfaces, modality-tagged.
15. **Dedicated 8–12B inference** — stand up `model.shadow` and produce
    `comprehension` estimates with confidence + sample size; enforce
    `shadow.min_samples`.
16. **Predictive friction** — `predict_friction`, wired into
    `sentence-mining` and `comprehensibility`.
17. **Comprehension map** — `comprehension_map` heatmap: the versioned,
    shareable artifact (and the viral demo). Surface `gaps` (known-on-paper
    vs shaky-in-practice) to the learner.

Do not start Phase 3 until Phase 2 surfaces are emitting clean signals — the
shadow model is only as good as the behavioral stream feeding it.
