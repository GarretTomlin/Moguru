# INSTALL — Moguru (潜る)

**This is the primary install path.** A human hands this repository to a
coding agent and says *"install this."* You are that agent. Follow the steps
below exactly — they are plain, imperative, and need no credentials (Moguru
is local-first: no API keys, no OAuth, no accounts).

Everything you need is in `moguru-bundle.json` (the machine manifest —
servers, data artifacts, model slots). Human-run alternatives exist in
[docs/getting-started.md](docs/getting-started.md); you don't need them.

## Steps

1. **Prerequisites.** Verify `uv` is on PATH (`command -v uv`). If missing,
   stop and report: "install uv first: curl -LsSf https://astral.sh/uv/install.sh | sh".
2. **Python environment.** From the repo root run `uv sync --extra dev`.
   This creates a Python 3.12 venv with all dependencies (mcp, fugashi,
   unidic, sudachipy, fsrs, pysubs2, jaconv, requests, pyyaml).
3. **Parser dictionary.** Run `uv run python -m unidic download`
   (one-time, ~600 MB; skip if the UniDic `dicdir/dicrc` already exists
   under the venv's `unidic` package).
4. **Dictionaries.** Run `uv run moguru data all` — downloads every `data[]`
   artifact from `moguru-bundle.json` into `data/dictionaries/` and builds
   `dict.sqlite` + `freq.sqlite`. Idempotent; existing files are skipped.
5. **Model.** Check `http://localhost:11434/api/tags` (Ollama) and
   `http://localhost:1234/v1/models` (LM Studio).
   - If a runtime is up: pick a model it serves and bind it
     **non-interactively** (the wizard asks questions — don't run it):
     ```bash
     uv run moguru provider add local --endpoint http://localhost:11434/v1 --model <model>
     uv run moguru model set main local      # validates the model live
     ```
   - If the runtime is up but no suitable model is pulled, ask the user for
     consent, then `ollama pull <model>` and bind as above.
   - If no runtime is running, do not fail the install — report how to add
     one later: `moguru provider add <id> --endpoint <url> --model <name>`
     then `moguru model set main <id>`.
6. **Register into the host you are running in** (optional — only if the
   user wants moguru's MCP servers mounted in an external host):
   `uv run moguru bundle install --host claude-desktop|cursor|openclaw`
   (merge-only; `--create` if the host config dir doesn't exist), or print
   a paste-able block with `uv run moguru bundle print`.
7. **Verify.** Run `uv run moguru doctor`. Every line must say PASS (SKIP is
   fine for optional items — e.g. `shadow` before any signals exist, or
   `service` if `moguru serve` isn't running yet). If any line fails, report
   it verbatim with its detail — do not continue past a failure.
   `moguru doctor --fix` re-runs failed data steps if that's the cause.
8. **Report back**: the doctor summary, the engine port (8766), and the two
   entry commands (`moguru chat`, `moguru serve`).

## What to tell the user afterward

- `moguru chat` — talk to the engine (paste text, mine, ask).
- `moguru serve` — start the engine API; the Reader extension needs this.
- Reader extension: `chrome://extensions` → Developer mode → *Load
  unpacked* → select `surfaces/reader/extension/` — full guide in
  [`surfaces/reader/README.md`](surfaces/reader/README.md).
- Anki users: keep Anki + AnkiConnect running; `moguru import-anki` seeds
  the knowledge store from mature cards.
- `moguru doctor --fix` self-heals failed data steps;
  `moguru uninstall [--purge]` removes everything but keeps
  `data/user/` (learning progress) unless `--purge` is passed.
