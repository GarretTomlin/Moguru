# Getting started & usage

Everything technical lives here; the [README](../README.md) stays clean.

## Install (clone → running)

```bash
git clone https://github.com/GarretTomlin/Moguru.git && cd Moguru
uv sync --extra dev                    # Python 3.12 env (uv: https://astral.sh/uv)
uv run python -m unidic download       # one-time: UniDic dictionary (~600 MB)
uv run moguru data all                 # fetch + build dictionaries (~400 MB)
uv run moguru model wizard             # bind your model (detects a running Ollama)
uv run moguru doctor                   # every line should say PASS
```

Prerequisites: [uv](https://astral.sh/uv), a model runtime (Ollama / LM
Studio — any OpenAI-compatible endpoint), and — for the Anki path —
[Anki](https://apps.ankiweb.net) with the
[AnkiConnect](https://ankiweb.net/shared/info/2055492159) add-on (code
`2055492159`) running.

No API keys, no accounts. Dictionaries are fetched at install time and never
committed (see [LICENSE](../LICENSE) for data attribution). You can also hand
the whole folder to an agent and say *"install this"* — it follows
[INSTALL.md](../INSTALL.md).

## The Reader (browser extension)

Full guide: [`surfaces/reader/README.md`](../surfaces/reader/README.md).

1. `uv run moguru serve` and leave it running (engine API on :8766).
2. `chrome://extensions` → Developer mode → **Load unpacked** → select
   `surfaces/reader/extension/`.
3. Browse any Japanese page: known words stay clean, **i+1 words glow green**,
   amber clusters mean "above your level", dotted-violet = paper-known but
   behaviorally shaky. Right-click any word → **Explain** / **Send to Anki**
   / **Mark known**. **Alt+M** toggles.

## Daily use

```bash
moguru chat                      # model-driven session: paste text, mine, ask
moguru lookup 魚を食べた。       # tokens + readings + definitions (deterministic)
moguru mine --add 魚を食べた。   # i+1 candidates -> Anki cards
moguru assess "$(cat text.txt)"  # too_easy / iplus_sweet_spot / too_hard
moguru due && moguru review      # review (builtin FSRS; or inside Anki)
moguru mark 魚 水 --source manual# seed what you already know
moguru import-anki               # seed kb from mature Anki cards
moguru rtk 明                    # RTK decomposition + primitive gating
moguru stats                     # knowledge-store summary
moguru plugins                   # mounted plugins
```

## The shadow model — what you *truly* understand

`kb` records what you made cards for; the shadow model infers what you
actually get in flowing native content — per word **and** grammar point, with
reading and listening tracked separately. It learns from your behavior
automatically: Reader hovers, lookups, scroll-pasts, and every card you mine
feed it. Never synced, never uploaded.

```bash
moguru shadow gaps               # paper-known vs shaky-in-practice
moguru shadow explain --key 食べる --modality reading   # WHY it believes this
moguru shadow map                # versioned comprehension heatmap
moguru shadow calibration        # decile curve + Brier score
moguru shadow interpret          # small-model pass over ambiguous signals
```

## Models — swap the brain, one command

Local or hosted (Claude, GPT, Gemini, Ollama, LM Studio…), no reinstall:

```bash
moguru provider add claude --endpoint https://api.anthropic.com/v1 \
  --model claude-sonnet-4-6 --api-key-env ANTHROPIC_API_KEY
moguru provider add local-27b --endpoint http://localhost:11434/v1 --model <model>
moguru model set main claude     # validates live before saving
moguru model set shadow local-8b # hosted shadow requires --i-know (privacy rail)
moguru model list && moguru model test main
```

Keys come from env vars, never stored inline. `model set` refuses broken
configs with the specific fix ("model not found — `ollama pull …`?").

## Configuration (`config.yaml`)

| Key | Default | Notes |
|---|---|---|
| `model.local.endpoint` | `http://localhost:11434/v1` | any OpenAI-compatible runtime |
| `model.routing` | `local_first` | `local_only` \| `local_first` \| `strong_only` |
| `parser.engine` | `mecab_unidic` | `sudachi` also supported |
| `srs.backend` | `anki` | auto-creates the `target-word` note type; `builtin` FSRS \| `none` |
| `mining.iplus_threshold` | `1` | max unknown content words per candidate |
| `mining.sentence_len` | `[4, 25]` | token bounds for a minable sentence |
| `defs.mode` | `bilingual` | `mixed` / `monolingual` via the transition policy |
| `shadow.*` | see file | min_samples, decay half-life, evidence weights, calibration window |

## Engine service (what surfaces talk to)

```bash
moguru serve        # http://localhost:8766
```

Endpoints: `/lookup` `/mine` `/assess` `/ask` `/annotate` `/mark_known`
`/known_version` `/signals` `/health`. Surfaces are dumb clients; all logic
stays in the engine.

## Maintain / verify / remove

```bash
moguru doctor [--fix]             # per-line PASS/FAIL health check
moguru bundle print               # paste-able mcpServers block for any MCP host
moguru bundle install --host cursor [--create]
moguru update                     # refresh dictionary data
moguru uninstall [--purge]        # keeps data/user (kb + progress) by default
```

## What's inside

| Layer | Pieces |
|---|---|
| **MCP servers** (facts, ground truth) | parser (fugashi+UniDic) · dict (JMdict/JMnedict/KANJIDIC2/krad/kanjium pitch) · freq (JPDB v2.2 + BCCWJ) · kb (known set + bloom) · srs (AnkiConnect \| FSRS) · media (subs/audio/OCR) · shadow (behavioral comprehension) |
| **Skills** (judgment, procedure) | sentence-mining · rtk-kanji · comprehensibility · monolingual-transition · card-format |
| **Surfaces** | engine HTTP service · Reader extension + PDF reader |
| **Rails** | plugin registry with ground-truth protection · logged, reversible adaptivity · doctor + bundle manifest |

Principle 0: **facts in tools, judgment in the model.** Readings,
definitions, frequencies, and "does the user know this word" are always
resolved by tool calls against real data — never recited from model weights.

## Tests

```bash
uv run pytest -q
```
