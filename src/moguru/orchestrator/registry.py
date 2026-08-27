"""Plugin registry (spec §5.1).

`plugins/` directory. Each plugin is a folder with a manifest:

    {
      "name": "wanikani-radicals",
      "type": "mcp" | "skill",
      "version": "0.1.0",
      "entry": "server.py" | "SKILL.md",
      "tools": [ { "name": "...", "description": "..." } ],
      "provides_ground_truth": false
    }

Rails (spec §5.2): a plugin with `provides_ground_truth: false` may never
shadow a dictionary/parser tool. Enforced at mount time here — the registry
rejects the colliding tool and reports it, rather than loading it.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from moguru.config import REPO_ROOT

# Tool names owned by the built-in ground-truth servers. A non-ground-truth
# plugin may never register any of these.
GROUND_TRUTH_TOOLS = {
    # parser-mcp
    "tokenize", "deinflect", "segment_sentences", "to_reading",
    # dict-mcp
    "lookup_word", "lookup_name", "lookup_kanji", "lookup_monolingual",
    "decompose_kanji", "lookup_pitch",
    # freq-mcp
    "frequency", "rank_by_frequency",
    # kb-mcp
    "is_known", "get_known_set", "known_kanji", "mark_known",
    "mark_kanji_known", "record_encounter", "stats",
    # srs-mcp
    "add_card", "find_notes", "update_note", "due_cards", "review_note",
    "import_known",
    # media-mcp
    "parse_subtitles", "extract_audio", "ocr_image", "capture_context",
}


@dataclass
class Plugin:
    name: str
    type: str                 # "mcp" | "skill"
    version: str
    entry: str
    tools: list[dict]
    provides_ground_truth: bool
    path: Path

    @property
    def command(self) -> list[str] | None:
        """Spawn command for MCP plugins (run with the project's Python)."""
        if self.type != "mcp":
            return None
        import sys

        return [sys.executable, str(self.path / self.entry)]


def scan(plugins_dir: Path | None = None) -> tuple[list[Plugin], list[str]]:
    """Read every manifest. Returns (plugins, warnings)."""
    plugins_dir = plugins_dir or (REPO_ROOT / "plugins")
    warnings: list[str] = []
    plugins: list[Plugin] = []
    if not plugins_dir.exists():
        return plugins, warnings
    for manifest_path in sorted(plugins_dir.glob("*/manifest.json")):
        try:
            data = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            warnings.append(f"bad manifest {manifest_path}: {e}")
            continue
        p = Plugin(
            name=data.get("name", manifest_path.parent.name),
            type=data.get("type", ""),
            version=data.get("version", "0.0.0"),
            entry=data.get("entry", ""),
            tools=data.get("tools", []),
            provides_ground_truth=bool(data.get("provides_ground_truth", False)),
            path=manifest_path.parent,
        )
        if p.type not in {"mcp", "skill"}:
            warnings.append(f"{p.name}: unknown type {p.type!r}, skipped")
            continue
        if not (p.path / p.entry).exists():
            warnings.append(f"{p.name}: entry {p.entry!r} missing, skipped")
            continue
        # Ground-truth rail: non-ground-truth plugins may not shadow core tools.
        if not p.provides_ground_truth:
            clash = [t["name"] for t in p.tools if t.get("name") in GROUND_TRUTH_TOOLS]
            if clash:
                warnings.append(
                    f"{p.name}: tool(s) {clash} shadow ground-truth tools — "
                    "plugin refused (spec §5.2 rail)"
                )
                continue
        plugins.append(p)
    return plugins, warnings
