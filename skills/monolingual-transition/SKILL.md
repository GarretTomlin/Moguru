---
name: monolingual-transition
description: >-
  Policy for shifting definitions from bilingual (J-E) to mixed to fully
  monolingual (J-J) as the learner's vocabulary grows — the AJATT endgame.
  Use when defs.mode is in question, when the learner crosses a vocabulary
  milestone, or when they ask to "go monolingual".
---

# Monolingual Transition

All-Japanese definitions are the endgame; jumping too early wastes time on
unknown-in-the-definition words. This skill decides **when** to shift
`defs.mode`, using `kb.stats` (never a guess).

## Thresholds (defaults; Phase A adaptivity may tune within bounds)

| known_words (kb.stats) | defs.mode | Behavior |
|---|---|---|
| < 1,500 | `bilingual` | J-E definitions only |
| 1,500 – 4,000 | `mixed` | J-J first, J-E fallback line under it |
| > 4,000 | `monolingual` | J-J only; unknown-in-definition words become mining targets |

## Procedure

1. `kb.stats()` → `known_words`.
2. Determine the mode for this learner. Compare with current `defs.mode`:
   - If it's time to move up, say so and propose the change (Phase A may
     apply it automatically — every change is logged and reversible).
3. When rendering definitions:
   - `mixed`: `dict.lookup_monolingual` first; append a `—` line with
     `dict.lookup_word` gloss as training wheels.
   - `monolingual`: `dict.lookup_monolingual` only. If the J-J source is not
     configured, say so plainly and fall back — never fabricate a J-J
     definition.
   - In J-J definitions, highlight words the learner doesn't know
     (`kb.is_known`); each is a candidate new mining target.

## Rules

- The transition is data-driven (known-word count), not calendar-driven.
- Learner can always pin a mode in config.yaml; explicit human config wins
  over adaptivity.
