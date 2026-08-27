---
name: rtk-kanji
description: >-
  RTK/Heisig-style kanji study: decompose a kanji into primitives, check
  which primitives the learner already knows, generate a mnemonic story, and
  produce keyword↔kanji cards. Use when the learner asks to study kanji,
  learn a specific kanji, or continue an RTK sequence.
---

# RTK Kanji

Heisig method: every kanji is a **composition of primitives** plus a keyword,
remembered through a vivid **story**. The engine's job is to ground the
composition in real data and gate it on what the learner knows.

## Procedure

1. `dict.decompose_kanji(char)` → the kanji's radicals/primitives (krad data).
2. `kb.known_kanji()` → which primitives are already **assumed known**. Unknown
   primitives in the decomposition must themselves be learned first (RTK
   ordering) — surface them.
3. `dict.lookup_kanji(char)` → on/kun readings, meanings (keyword candidates),
   stroke count, grade, JLPT, frequency rank. Pick one concise English
   **keyword** from the meanings.
4. **Generate the mnemonic story**: you (the model) write it — keyword +
   components + vivid imagery. Keep primitives' established keywords stable.
   This is judgment work, not data work; composition and readings come from
   tools only.
5. Produce the card via **card-format** conventions but as an RTK note type:
   - `keyword → kanji` (production) or `kanji → keyword` (recognition),
     learner's choice (default: both directions via two notes, tags `rtk`).
6. `kb.mark_kanji_known` only when the learner reports it learned (or its card
   matures in SRS).

## Rules

- Never invent a decomposition — `decompose_kanji` is ground truth. If it
  returns nothing, say so and study the kanji as an atomic primitive.
- Stories reference only primitives the learner knows or is learning now.
- Respect RTK ordering pressure: prefer teaching unknown primitives before
  complex kanji containing them.
