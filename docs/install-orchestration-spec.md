# Enhancement · Moguru Install & Orchestration

*One command — or hand the folder to an agent and say "install."*

Modeled on the OpenClaw pattern: every capability is an MCP server declared
in one manifest; installing = registering those servers into whatever host
you run and fetching data + models. Because Moguru is **local-first (no API
keys, no OAuth, no accounts)**, the install has no credential ritual — the
only heavy steps are downloading dictionaries and pulling local models.

Three install paths, same manifest underneath:

1. **One-liner bootstrap** — `curl | bash`.
2. **Drop-in config** — paste a block into your MCP host.
3. **Agent-install** — copy the folder to an agent and say *"install this."*
   ← the headline

---

## 1. The bundle manifest — `moguru-bundle.json`

Single source of truth. Everything else reads from this.

```json
{
  "name": "moguru",
  "version": "0.1.0",
  "servers": [
    { "id": "parser", "command": "python", "args": ["-m", "moguru.parser_mcp"], "health": "tokenize:ok" },
    { "id": "dict",   "command": "python", "args": ["-m", "moguru.dict_mcp"],   "health": "lookup:ok" },
    { "id": "freq",   "command": "python", "args": ["-m", "moguru.freq_mcp"] },
    { "id": "kb",     "command": "python", "args": ["-m", "moguru.kb_mcp"] },
    { "id": "srs",    "command": "python", "args": ["-m", "moguru.srs_mcp"], "config": { "backend": "anki" } },
    { "id": "media",  "command": "python", "args": ["-m", "moguru.media_mcp"] },
    { "id": "shadow", "command": "python", "args": ["-m", "moguru.shadow_mcp"], "optional": true }
  ],
  "data": [
    { "id": "jmdict",   "url": "…", "sha256": "…", "dest": "data/dictionaries/jmdict.sqlite" },
    { "id": "kanjidic", "url": "…", "sha256": "…", "dest": "data/dictionaries/kanjidic.sqlite" }
  ],
  "models": [
    { "role": "orchestrator", "name": "qwen3.6-27b", "runtime": "ollama" },
    { "role": "shadow",       "name": "qwen3-8b",    "runtime": "ollama", "optional": true }
  ],
  "hosts": ["openclaw", "claude-desktop", "cursor", "generic"]
}
```

Secrets (none required today) would go through `${ENV_VAR}` refs, never
inline — nothing leaks into backups or git.

## 2. Bootstrap installer — `install.sh`

One command:
```bash
curl -fsSL https://…/moguru/install.sh | bash
```
It reads the manifest and runs, **idempotently** (safe to re-run to update),
in order:

1. **Prereqs** — check Node / Python / a model runtime (Ollama); offer to
   install what's missing.
2. **Data** — download each `data[]` artifact, verify `sha256`, place at
   `dest`. Skip if present.
3. **Models** — pull each `models[]` entry via its runtime. Skip if present.
4. **Register** — write the `servers[]` into every detected host via its
   adapter (§3).
5. **Start & verify** — launch the servers and run `moguru doctor` (§5).

Fail loud and early: if a step can't complete, stop with a specific,
actionable message rather than leaving a half-registered host.

## 3. Host adapters

Each adapter translates the manifest's `servers[]` into one host's config
format. Adding a new host = adding one adapter; the manifest never changes.

| Host | Writes to | Shape |
|---|---|---|
| **OpenClaw** | `~/.openclaw/openclaw.json` | merge under `mcpServers`, restart gateway |
| **Claude Desktop** | `claude_desktop_config.json` | merge under `mcpServers` |
| **Cursor** | `.cursor/mcp.json` | merge under `mcpServers` |
| **Generic** | stdout | print the block to paste anywhere MCP-compatible |

Adapters **merge, not overwrite** — never clobber a user's other servers —
and are idempotent (re-running updates Moguru's entries in place).

## 4. Agent-install path ("copy it over and say install")

The headline experience. Ship two files at the repo root:

- **`moguru-bundle.json`** — the machine manifest (§1).
- **`INSTALL.md`** — the *same* steps written as plain imperative
  instructions an agent can follow (prereqs → fetch data → pull models →
  register into the host it's running in → run `moguru doctor` → report).

Flow: drop the folder into the agent's workspace, say **"install this."**
The agent reads `INSTALL.md`, executes against `moguru-bundle.json`, and
confirms with the health check. Nothing is agent-specific — any capable
coding/agent runtime can do it because the recipe is explicit and there are
no credentials to broker.

Because Moguru is itself a set of MCP servers + skills, it also drops into a
hub cleanly: publish it so an OpenClaw-style agent can install by name
(`clawhub install moguru`-style) without even copying files.

## 5. Verify & recover — `moguru doctor`

The command the installer (and the agent) runs to prove success. Checks and
clear pass/fail per line, with non-zero exit on any failure so an agent can
branch:

- each server responds to its `health` probe,
- each dictionary DB exists and has a sane row count (not a truncated
  download),
- each model is reachable in its runtime,
- if `srs.backend = anki`, AnkiConnect answers on `:8765`.

`moguru doctor --fix` re-runs the specific failed steps (re-download a bad
artifact, re-pull a missing model) rather than the whole install.

## 6. Update & uninstall

- **Update:** re-run the bootstrap (or `moguru update`) — idempotent; only
  changed data, models, and server entries are touched.
- **Uninstall:** `moguru uninstall` removes Moguru's entries from each host
  config and deletes bundled data/models, but **keeps `data/user/` (your kb
  + progress) by default**, with `--purge` to remove that too. Losing months
  of learning history to an uninstall would be unforgivable.

## 7. Build steps

1. **`moguru-bundle.json` + a loader** — the manifest and its
   schema/validation.
2. **Generic adapter** — print-the-block; makes the manifest usable in any
   host immediately.
3. **`moguru doctor`** — health checks; build this early, the installer
   leans on it.
4. **`install.sh`** — prereqs → data → models → register → verify,
   idempotent.
5. **OpenClaw + Claude Desktop + Cursor adapters**.
6. **`INSTALL.md`** — the agent-readable recipe; test by handing the folder
   to an agent cold.
7. **Hub publish** + `update` / `uninstall`.

Ship steps 1–3 first: a validated manifest, a paste-able config, and a
working doctor make every later path just "wire it up."
