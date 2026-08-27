#!/usr/bin/env bash
# 潜る Moguru — bootstrap installer (idempotent; safe to re-run to update).
#
#   curl -fsSL <repo>/install.sh | bash
#
# Steps: prereqs → data → models → register → verify. Fails loud and early
# with an actionable message rather than leaving a half-registered host.
set -euo pipefail

cd "$(dirname "$0")"
echo "== Moguru installer =="

# 1) prereqs ---------------------------------------------------------------
if ! command -v uv >/dev/null 2>&1; then
  echo "✘ uv not found — install it first:  curl -LsSf https://astral.sh/uv/install.sh | sh"
  exit 1
fi
echo "✔ uv $(uv --version | awk '{print $2}')"

echo "→ python env (uv sync)…"
uv sync --extra dev

# 2) data ------------------------------------------------------------------
echo "→ dictionaries (download + build; ~1 GB, skipped when present)…"
uv run moguru data all

# 3) models ----------------------------------------------------------------
if curl -fsS -m 2 http://localhost:11434/api/tags >/dev/null 2>&1; then
  echo "✔ Ollama running on :11434"
  MAIN_MODEL=$(uv run python -c "import json;print(json.load(open('moguru-bundle.json'))['models'][0]['name'])")
  if curl -fsS http://localhost:11434/api/tags | grep -q "\"${MAIN_MODEL}\""; then
    echo "✔ main model present: ${MAIN_MODEL}"
  else
    echo "⚠ main model not pulled yet: ${MAIN_MODEL}"
    echo "    run:  ollama pull \"${MAIN_MODEL}\""
    echo "    (or `moguru model set main <provider>` for another model)"
  fi
else
  echo "⚠ no Ollama on :11434 — add a provider later with:"
  echo "    moguru provider add <id> --endpoint <url> --model <name>"
fi

# 4) register into detected hosts -------------------------------------------
# Only merges into hosts whose config already exists; use
# `moguru bundle install --host <name> --create` to force-create.
for host in claude-desktop cursor openclaw; do
  if uv run moguru bundle install --host "$host" 2>/dev/null; then
    echo "✔ registered into ${host}"
  else
    echo "– ${host} config not present (skipped; `moguru bundle install --host ${host} --create` to force)"
  fi
done
echo "→ generic block (paste into any MCP host):"
uv run moguru bundle print

# 5) verify ----------------------------------------------------------------
echo "→ doctor…"
uv run moguru doctor --no-servers || true
echo
echo "== done =="
echo "  chat:      uv run moguru chat"
echo "  serve:     uv run moguru serve        # engine API on :8766"
echo "  models:    uv run moguru model wizard"
echo "  reader:    load surfaces/reader/extension in chrome://extensions"
