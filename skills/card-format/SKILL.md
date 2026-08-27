---
name: card-format
description: >-
  Canonical note type and field templates for every card the engine creates.
  Use whenever creating a card via srs.add_card so formats stay consistent.
---

# Card Format (Migaku-style)

All skills that create cards use this one definition. The canonical note type
is **target-word** with exactly these fields:

```
Fields: Sentence, TargetWord, Reading, Definition, Audio, Image, PitchAccent, Source
```

| Field | Content | Source of truth |
|---|---|---|
| Sentence | The full i+1 sentence, target word in situ | the immersion text |
| TargetWord | The single unknown lemma being mined | parser lemma |
| Reading | Kana reading of the target word | JMdict readings / parser reading_kana |
| Definition | Gloss per current `defs.mode` policy | dict.lookup_word / lookup_monolingual |
| Audio | Sentence audio clip path (if from media) | media.extract_audio |
| Image | Context screenshot (if from media) | media.capture_context |
| PitchAccent | Reading with accent nucleus, e.g. `たべる [0]` | dict.lookup_pitch (kanjium) |
| Source | Where it came from (media ref / "text") | mining context |

Rules:

- **One target word per card** (MIA sentence-card preference).
- Fields the data cannot ground are left empty — never guessed.
- Deck: the configured deck (default `Moguru 日本語`); tags:
  `mined`, `i+1`, plus the source name when known.
- Register every created note id back into `kb` (`set_srs_note`) so SRS state
  can flow into the knowledge store.
