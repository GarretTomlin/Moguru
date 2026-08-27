"""Skill loading (spec §2: skills are SKILL.md workflows consumed by the
orchestrator). Progressive disclosure: names + descriptions go into the
system prompt; full bodies load on demand via the load_skill meta-tool.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from moguru.config import REPO_ROOT


@dataclass
class Skill:
    name: str
    description: str
    body: str
    path: Path


_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)


def _parse_frontmatter(text: str) -> tuple[dict[str, str], str]:
    m = _FRONTMATTER_RE.match(text)
    if not m:
        return {}, text
    meta: dict[str, str] = {}
    for line in m.group(1).splitlines():
        if line.startswith("name:"):
            meta["name"] = line.split(":", 1)[1].strip()
        elif line.startswith("description:"):
            # may span lines until next key; take inline + folded basic form
            meta.setdefault("description", line.split(":", 1)[1].strip())
    return meta, text[m.end():]


def load_skills(skills_dir: Path | None = None) -> dict[str, Skill]:
    skills_dir = skills_dir or (REPO_ROOT / "skills")
    out: dict[str, Skill] = {}
    if not skills_dir.exists():
        return out
    for skill_file in sorted(skills_dir.glob("*/SKILL.md")):
        text = skill_file.read_text(encoding="utf-8")
        meta, body = _parse_frontmatter(text)
        name = meta.get("name", skill_file.parent.name)
        desc = meta.get("description", "").strip()
        # Fold multi-line YAML descriptions
        desc = re.sub(r"\s+", " ", desc)
        out[name] = Skill(name=name, description=desc, body=body.strip(),
                          path=skill_file)
    return out
