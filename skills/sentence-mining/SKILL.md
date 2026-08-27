---
name: sentence-mining
description: >-
  The core mining loop. Given raw Japanese text or a subtitle line, find i+1
  sentences (at most one unknown content word relative to the learner's known
  set), ground the target word in dictionary + frequency data, and emit a
  Migaku-style sentence card. Use whenever the learner shares immersion text
  to mine or asks "what should I learn from this".
---

# Sentence Mining (i+1)

You are executing the MIA-style mining loop. **Every reading, definition, and
frequency below MUST come from a tool call** (`parser`, `dict`, `freq`, `kb`).
Never recite dictionary content from memory — a wrong reading taught to the
learner is worse than no answer.

## Procedure

Given a text or subtitle line:

1. `parser.segment_sentences(text)` → per sentence:
2. `parser.tokenize(sentence)` → tokens.
3. Filter to **content words** — drop particles / auxiliaries / symbols by POS
   (nouns, verbs, adjectives, adverbs, prenouns count; pronouns and numbers do
   not).
4. For each content lemma: `kb.is_known(lemma)`.
5. Compute `unknown_count`. The sentence is a candidate iff
   `unknown_count <= mining.iplus_threshold` AND token count is within
   `mining.sentence_len` (config: default 4–25 tokens).
6. For each candidate's target word: `dict.lookup_word`,
   `freq.frequency`, and — when the text came with media —
   `media.extract_audio` / `media.capture_context`.
7. Emit the card via the **card-format** skill; register with
   `srs.add_card` and `kb.record_encounter`. Mined words enter the known set
   at low strength (they are "being learned", not "known").

Rank multiple candidates by **target-word frequency (frequent first)**, then
by known-coverage. **One target word per card** — if a sentence has two
unknown words, it is not i+1 for this learner; skip it or let
`iplus_threshold` decide.

## i+1 selection (exact rule)

```
def is_iplus(sentence):
    toks = tokenize(sentence)
    content = [t for t in toks if t.pos in CONTENT_POS]
    unknown = [t for t in content if not kb.is_known(t.lemma).known]
    if not (LEN_MIN <= len(toks) <= LEN_MAX): return None
    if len(unknown) > IPLUS_THRESHOLD: return None
    target = unknown[0] if unknown else None   # 0 unknown = review/known-good
    return Candidate(sentence, target,
                     score=rank(freq(target), coverage=len(content)-len(unknown)))
```

## Output to the learner

Present candidates as a numbered list: sentence (with the target word
highlighted), reading, accent, the JMdict definition, JPDB rank, and why it
qualifies (e.g. "1 unknown word: 食べる"). Ask before mass-adding cards; add on
confirmation with `srs.add_card` using the card-format fields.

## Shadow integration (Phase 3)

- Before presenting candidates, call `shadow.predict_friction` on each —
  pre-empt the word/grammar that will actually break the learner ("it read my
  mind"), not just what the card count says is unknown. Surface `friction`
  in your ranking rationale.
- Card creation emits a `mine` signal automatically (the engine does this in
  `mine_text`/`/mine add:true`) — never double-emit from the model side.
- Treat `shadow.gaps` as re-mining targets: paper-known but shaky-in-practice
  words deserve fresh sentence cards.
