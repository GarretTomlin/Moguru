---
name: comprehensibility
description: >-
  Score a passage's difficulty relative to the learner's knowledge state
  before they dive in — the "is this content at my level" gate from
  comprehensible-input theory. Use when the learner asks whether some content
  (article, episode, chapter) suits their level.
---

# Comprehensibility Gate

Score a passage **before** the learner invests time in it.

## Procedure

1. `parser.segment_sentences` + `parser.tokenize` across the whole text.
2. For every content-word lemma: `kb.is_known`.
3. Compute and report:

```
{ pct_known, iplus_density, unknown_words[], verdict }
verdict ∈ { too_easy, iplus_sweet_spot, too_hard }
```

- `pct_known` — share of content-word tokens known.
- `iplus_density` — share of sentences that are i+1 candidates
  (≤ threshold unknown content words, length in bounds).
- `unknown_words` — unknown lemmas, most frequent first (use
  `freq.rank_by_frequency`); cap the list at ~20 and show counts.
- `verdict`:
  - `too_easy` — pct_known ≥ 98% with almost no i+1 sentences; mine lightly
    or pick harder content.
  - `iplus_sweet_spot` — the target zone (~90–98% known, healthy i+1
    density); dive in.
  - `too_hard` — pct_known < 90%; suggest easier content or intensive
    lookups.

## Output

One short verdict line, the numbers behind it, and the top unknown words with
frequencies. Optionally offer to pre-teach the top 5 unknowns via
sentence-mining on this same text.
