# Enhancement · Moguru Models & Providers

*Swap the main model and the shadow model after install — local or hosted
(Claude, GPT, Gemini, Ollama, …) — with one command each, no reinstall.*

Companion to the install doc. The engine already talks to models over an
OpenAI-compatible endpoint; this makes the choice **runtime config**, not a
file edit, and treats a local runtime and a deployed API as the same kind of
thing.

---

## 1. Two roles, any provider

Moguru has exactly two model slots. They are independent — mix freely (e.g.
Claude for the main brain, a local 8B for the shadow layer).

| Role | Does | Typical pick |
|---|---|---|
| **main** | orchestration, grammar explanation, mining judgment | strong model — Claude, GPT, or a local 27B |
| **shadow** | constant comprehension inference on the signal stream | small + cheap + private — local 8–12B |

**Design rule:** the shadow role should default to **local**. It runs
continuously on your most personal data (what you actually understand);
sending that stream to a hosted API every few seconds is wrong on both cost
and privacy. Main can be hosted or local freely.

## 2. The provider abstraction

Every provider — local or hosted — collapses to the same four fields. That's
the whole trick: nothing downstream knows or cares whether the model is on
your GPU or in a datacenter.

```json
{
  "id": "claude",
  "endpoint": "https://api.anthropic.com/v1",
  "model": "claude-sonnet-4-6",
  "api_key_env": "ANTHROPIC_API_KEY"
}
```

- **Local** (Ollama / LM Studio / llama.cpp): `endpoint` is `localhost`, no
  `api_key_env`.
- **Hosted** (Anthropic / OpenAI / Gemini / OpenRouter / Azure / Bedrock): a
  base URL + `model` + a key pulled from an **env var**, never stored inline.

Providers live in `data/user/providers.json` and are added once, then
referenced by `id`.

## 3. Post-install commands

No file editing required.

```bash
moguru provider add claude \
  --endpoint https://api.anthropic.com/v1 \
  --model claude-sonnet-4-6 \
  --api-key-env ANTHROPIC_API_KEY

moguru provider add local-27b \
  --endpoint http://localhost:11434/v1 --model qwen3.6-27b

moguru model set main   claude
moguru model set shadow local-8b

moguru model list
moguru model test main
```

Changes take effect on the next request — **no restart, no reinstall**.
Role→provider bindings persist in `data/user/config.yaml` (`model.main`,
`model.shadow`).

## 4. First-run wizard

On first launch, if no models are set, ask two quick questions instead of
failing:

1. *Main model* — "local (I'll pull one)" or "hosted (paste endpoint + key)."
2. *Shadow model* — default to a local pull; warn plainly if the user
   insists on hosted.

Detect what's already there (a running Ollama, an `ANTHROPIC_API_KEY` in the
environment) and offer it as the default so the common case is one keypress.

## 5. Validation on set

`moguru model set` refuses silently-broken configs — it does a live check
before saving:

- endpoint reachable and speaks the OpenAI-compatible schema,
- the named model exists at that endpoint (pull it if local and missing, with
  consent),
- for hosted: the `api_key_env` is actually set,
- a tiny Japanese round-trip returns sane output.

Fail with the specific reason ("model not found — run `ollama pull
qwen3-8b`?"), so an agent driving this can self-correct.

## 6. Agent-friendly

Everything above is a flag-driven command with clear exit codes, so the same
*"tell an agent to do it"* flow works for models too:

> "Set my main model to Claude and keep the shadow model local."

The agent runs `provider add` + `model set` + `model test`, reads the
pass/fail, and reports — no menus, no file surgery.

## 7. Build steps

1. **Provider schema + `providers.json`** — the four-field abstraction and
   its store.
2. **`model set` / `model list` / `model test`** — bind roles to providers,
   with §5 validation.
3. **`provider add` / `provider remove`**.
4. **First-run wizard** (§4) with environment detection.
5. **Local-shadow guardrail** — the privacy warning when shadow is pointed at
   a hosted API.

Ship 1–2 first: once roles can be bound to a validated provider at runtime,
hosted-vs-local is already solved; the wizard and guardrails are polish on
top.
