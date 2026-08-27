# Spec · Moguru Shadow Model (`shadow-mcp`)

*A private, behavioral model of what the learner **actually understands** in
flowing native content — as opposed to what they've made flashcards for. The
gap between the two is the whole product.*

Self-contained; runs its **own dedicated small model** (8–12B, local) doing
nothing but comprehension inference on a stream of tiny behavioral signals.
Consumes signals emitted by the surfaces and exposes estimates back to
mining, the reader, comprehensibility, and the monolingual transition.

---

## 1. Why it exists

`kb-mcp` records what you have a **card** for. That is a terrible proxy for
comprehension: you have a mature card for 見る but freeze on it in the
て-form in fast speech; you read a word instantly but never catch it by ear.
Every existing immersion tool is blind to this.

The shadow model closes that gap. It infers a **probabilistic, per-word and
per-grammar, per-modality** estimate of real comprehension from how you
behave while immersing, and surfaces where "known on paper" and "holds up in
the wild" diverge. This is what turns AJATT's black box into a **glass box**:
visible progress, real diagnosis, a feedback loop.

Because comprehension inferred from behavior is **noisy**, every estimate is
a belief with a confidence, never an assertion — and the model is designed
to say "I don't know yet" until it has evidence.

---

## 2. Core model

For each **key** in each **modality**, maintain a belief about the
probability the learner understands it.

- **key** = a vocabulary lemma *or* a grammar point (see §5).
  `key_kind ∈ {vocab, grammar}`.
- **modality** ∈ `{reading, listening}` — tracked **separately**. This is
  non-negotiable; the single most common real gap is "knows it by eye,
  misses it by ear," and one merged score hides exactly that.

**Representation — Beta belief per (key, modality):**
Model "understood?" as Bernoulli; keep a conjugate `Beta(α, β)`.

```
p_understood = α / (α + β)
confidence   ∝ α + β            # total evidence accumulated
sample_size  = count of real encounters      # reported alongside; gates trust
```

Start `Beta(1,1)` (uniform — "no idea"). Each signal adds weighted
pseudo-evidence: understood → `α += w`, not-understood → `β += w`, where `w`
is the signal's strength (§4). Below `shadow.min_samples` real encounters,
report the estimate as **low-confidence** regardless of `p`.

This gives a cheap, incremental, always-current statistical core. The small
model (§6) sits on top of it as an *interpreter*, not a replacement.

---

## 3. Division of labor: statistics + a small model

The hard part isn't counting — it's that raw signals are ambiguous. A pause
might be thought or confusion; a rewind might be enjoyment or a missed line.
So:

- **Statistical core (always, cheap):** accumulates Beta evidence from
  *unambiguous* signals (a dictionary lookup is strong "didn't know it"; a
  clean pass-through is weak "did").
- **Small model (async, batched):** interprets *ambiguous* signals in context
  and does prediction. Given a pause + its sentence, it judges whether it's
  likely a comprehension problem and **localizes which token or grammar
  point** caused it, and **classifies the friction type** (vocab / grammar /
  parse-speed) — things a counter cannot do. Its structured output is fed
  back as targeted evidence.

The small model runs on batches on its own endpoint (`model.shadow`), never
per-event, so it stays cheap and local. It is the one place comprehension
data is reasoned over — and it never leaves the machine.

---

## 4. Signals

Emitted by surfaces (reader hover, video rewind, mining, etc.).

```
Signal = {
  type: "hover"|"pause"|"rewind"|"replay"|"lookup"|"mine"|"skip"|"complete",
  key?, key_kind?, sentence, modality: "reading"|"listening",
  dwell_ms?, playback_speed?, media_ref, ts
}
```

**Evidence mapping** (weights configurable; hard evidence dominates soft):

| Signal | Evidence | Strength | Notes |
|---|---|---|---|
| `lookup` / `hover` | not-understood | **hard** | you actively sought the meaning |
| `mine` | not-understood → now learning | **hard** | seeds a learning prior |
| `rewind` / `replay` | not-understood (listening) | medium | repeated on same span → stronger |
| `pause` (dwell ≫ baseline) | not-understood | soft | send to small model to confirm + localize |
| `skip` | ambiguous | very soft | could be difficulty or boredom; barely counts |
| `complete` (passed through, no lookup) | understood | soft | the backbone positive signal |

Soft signals only move the belief materially once **corroborated** — a lone
pause is nearly ignored; a pause the small model attributes to a specific
unknown word, followed later by a lookup of that word, compounds.

---

## 5. Grammar-point tracking

Grammar is a first-class key, not just vocab. Identify grammar points in a
sentence via a hybrid:

- **Pattern lexicon (fast, deterministic):** a table of grammar points, each
  with a matcher over the parser's token/POS sequence (e.g.
  `〜わけにはいかない`, causative-passive, conditional `〜ば`). Covers the
  common core cheaply.
- **Small-model tagging (long tail):** for sentences the lexicon doesn't
  cover, the shadow model tags grammar points it recognizes.

Each identified grammar point becomes a `key_kind = grammar` key with its own
Beta belief per modality — so "you parse this pattern fine when reading but
it collapses in speech" becomes visible and mineable.

---

## 6. Prediction — `predict_friction`

The signature "it read my mind" feature. Given a sentence you *haven't* hit
yet, simulate your read and call what will break you.

```
predict_friction(sentence, modality) -> [ Friction ]
  Friction = { span, type: "vocab"|"grammar"|"parse_speed", p_break, reason }
```

1. Tokenize; extract vocab keys + grammar keys (§5).
2. For each key, pull its `comprehension(key, modality)` estimate.
3. Flag low-`p_understood` keys as friction (type from `key_kind`).
4. For **listening**, add `parse_speed` friction when the sentence is
   long/dense and the learner's listening estimates lag their reading
   estimates.
5. Small-model holistic pass: catch **interactions** — a stack of
   individually-known words that combine into something confusing — which
   per-key stats miss.
6. Return ranked by `p_break`.

Consumers pre-empt: mining prioritizes these; the reader can pre-gloss them;
the engine can explain them before you stumble.

---

## 7. MCP interface

```
record_signal(Signal) -> { accepted, keys_touched[] }
comprehension(key, modality) -> Estimate
  Estimate = { p_understood, confidence, sample_size, last_seen }
comprehension_batch(keys[], modality) -> [ Estimate ]        # bulk, for the reader's /annotate
predict_friction(sentence, modality) -> [ Friction ]         # §6
gaps(filter?) -> [ Gap ]
  Gap = { key, key_kind, srs_known, p_understood, modality, delta }   # paper-known vs shaky
comprehension_map(scope?) -> Heatmap                         # the shareable, versioned artifact
explain_estimate(key, modality) -> { evidence[], reasoning } # transparency: *why* it believes this
calibration() -> { curve, brier_score, n }                   # is the model any good? (§10)
```

`explain_estimate` matters for trust: in a probabilistic system the learner
must be able to ask "why do you think I don't know this?" and see the actual
evidence trail.

---

## 8. Data model (SQLite)

```sql
CREATE TABLE signals (
  id INTEGER PRIMARY KEY, ts TEXT, type TEXT,
  key TEXT, key_kind TEXT, modality TEXT,
  sentence TEXT, media_ref TEXT, dwell_ms INTEGER, playback_speed REAL, weight REAL
);
CREATE TABLE estimates (
  key TEXT, key_kind TEXT, modality TEXT,
  alpha REAL DEFAULT 1, beta REAL DEFAULT 1,
  sample_size INTEGER DEFAULT 0, last_seen TEXT, updated_at TEXT,
  PRIMARY KEY (key, key_kind, modality)
);
CREATE TABLE grammar_points ( id INTEGER PRIMARY KEY, name TEXT, matcher TEXT, notes TEXT );
CREATE TABLE calibration_log ( ts TEXT, key TEXT, modality TEXT, predicted REAL, observed INTEGER );
```

Lives entirely under `data/user/shadow.sqlite`. Never synced, never uploaded.

---

## 9. Cold start, decay, confounds

- **Cold start / priors:** unseen key → `Beta(1,1)`, low confidence.
  Optionally seed a *mild* prior toward understood if a mature SRS card
  exists — but only mild, because distrusting exactly that assumption is
  the point of the whole component.
- **Decay:** apply gentle exponential decay to evidence over elapsed time
  without exposure (half-life configurable), so stale estimates lose
  confidence rather than staying falsely certain — mirroring real
  forgetting, and reconcilable with FSRS state.
- **Confounds, handled explicitly:**
  - pause = thinking vs confusion → small-model disambiguation + require
    corroboration,
  - rewind = enjoyment vs miss → weight *repeated* rewinds of the same span
    higher,
  - fast reading = skimming vs fluent → cross-check against lookup rate,
  - paper-known but ear-weak → the modality split captures it by
    construction.

---

## 10. Calibration (does it actually work?)

A probabilistic model that's never checked is just vibes. Continuously log
`(predicted p_understood, observed outcome)` — a later lookup/mine is a "not
understood" outcome; a clean pass is "understood." Compute a **calibration
curve** and a **Brier score** over recent predictions, exposed via
`calibration()` and surfaced in `moguru doctor`. If calibration drifts, the
evidence weights (§4) are the tuning knobs.

---

## 11. Integration points

- **Reader `/annotate`** → `comprehension_batch` adds the `known_unstable`
  band (paper-known, behaviorally shaky) to the color-coding.
- **`sentence-mining`** → calls `predict_friction` to prioritize and
  pre-empt; emits a `mine` signal on card creation.
- **`comprehensibility`** → blends shadow estimates with `kb.is_known` so
  "at my level" means *actually* comprehended, not just carded.
- **`monolingual-transition`** → gates the J-E → J-J shift on real
  comprehension, not card counts.

---

## 12. Config

| Key | Default | Notes |
|---|---|---|
| `model.shadow.endpoint` | `http://localhost:11435/v1` | dedicated small model |
| `model.shadow.name` | `qwen3-8b` | 8–12B, local, private |
| `shadow.min_samples` | `4` | encounters before an estimate is more than low-confidence |
| `shadow.decay_half_life_days` | `120` | evidence decay without exposure |
| `shadow.weights` | *(table §4)* | per-signal evidence strengths; the calibration tuning knobs |
| `shadow.calibration_window` | `500` | recent predictions scored for the Brier / curve |
