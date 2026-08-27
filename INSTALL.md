# INSTALL — Moguru (潜る)

*The same steps as `install.sh`, written as plain imperative instructions an
agent can follow. Hand this folder to any capable agent and say "install
this." No credentials are involved — Moguru is local-first.*

Read `moguru-bundle.json` (the machine manifest) as you go; it is the single
source of truth for servers, data artifacts, and model slots.

## Steps

1. **Prerequisites.** Verify `uv` is on PATH (`command -v uv`). If missing,
   stop and report: "install uv first: curl -LsSf https://astral.sh/uv/install.sh | sh".
2. **Python environment.** From the repo root run `uv sync --extra dev`.
   This creates a Python 3.12 venv with all dependencies (mcp, fugashi,
   unidic, sudachipy, fsrs, pysubs2, jaconv, requests, pyyaml).
3. **Parser dictionary.** Run `uv run python -m unidic download`
   (one-time, ~600 MB; skip if `data/…/unidic/dicdir/dicrc` already exists).
4. **Dictionaries.** Run `uv run moguru data all` — downloads every `data[]`
   artifact from `moguru-bundle.json` into `data/dictionaries/` and builds
   `dict.sqlite` + `freq.sqlite`. Idempotent; existing files are skipped.
5. **Models.** Check `http://localhost:11434/api/tags`. If Ollama is up and
   the manifest's `models[0].name` is missing, ask the user for consent,
   then `ollama pull <name>`. If no runtime is running, report how to add
   one later: `moguru provider add <id> --endpoint <url> --model <name>`
   then `moguru model set main <id>`.
6. **Register into the host you are running in** (optional — only if the
   user wants moguru's MCP servers mounted in an external host):
   `uv run moguru bundle install --host claude-desktop|cursor|openclaw`
   (merge-only; use `--create` if the host config dir doesn't exist), or
   print a paste-able block with `uv run moguru bundle print`.
7. **Verify.** Run `uv run moguru doctor`. Every line must say PASS (SKIP is
   fine for optional items). If any line fails, report it verbatim with its
   detail — do not continue past a failure.
8. **Report back**: doctor summary, the engine port (8766), and the two
   entry commands (`moguru chat`, `moguru serve`).

## Post-install (user-facing, mention in the report)

- `moguru model wizard` — bind main/shadow models (detects running Ollama).
- `moguru serve` — local engine API; the Reader extension talks to this.
- Reader extension: `chrome://extensions` → Developer mode →
  *Load unpacked* → select `surfaces/reader/extension/`.
- `moguru doctor --fix` — re-run failed data steps.
- `moguru uninstall` — unregisters hosts, keeps `data/user/` (kb + progress)
  unless `--purge` is passed explicitly.
