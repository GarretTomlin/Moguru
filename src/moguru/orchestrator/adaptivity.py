"""Phase A adaptivity (spec §5.2) — no code generation, tuning only.

The system tunes what it mines and how it explains from kb.stats + SRS
performance. Proposals land in data/user/adaptive.yaml (never in the
human-authored config.yaml) and every change is logged, versioned,
reversible: data/user/adaptivity_log.jsonl.

Bounds (declared, within which tuning may move):
  mining.iplus_threshold: 1..2
  defs.mode: bilingual -> mixed -> monolingual
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

import yaml

from moguru.config import Config

# Declared bounds — Phase C proposals may not exceed these.
BOUNDS = {
    "mining.iplus_threshold": (1, 2),
    "defs.mode": ["bilingual", "mixed", "monolingual"],
}


def _propose(known_words: int, mature_words: int,
             mean_understood: float | None = None) -> dict[str, Any]:
    """Thresholds shared with the monolingual-transition skill. §11: when the
    shadow model has data, real comprehension gates the defs.mode shift —
    not card counts alone. Falls back cleanly when it doesn't."""
    if mean_understood is None:
        comprehension_gate = known_words  # legacy: card-count gating
    else:
        # e.g. 2500 known words at 0.9 real comprehension ≈ 2250 "really known"
        comprehension_gate = int(known_words * mean_understood)

    if comprehension_gate > 4000 or (mean_understood or 0) > 0.97 and known_words > 1500:
        defs_mode = "monolingual"
    elif comprehension_gate >= 1500:
        defs_mode = "mixed"
    else:
        defs_mode = "bilingual"
    # Raise the i+1 bar once the learner has real coverage (spec example).
    iplus_threshold = 2 if (comprehension_gate >= 2500 or mature_words >= 1500) else 1
    return {
        "mining": {"iplus_threshold": iplus_threshold},
        "defs": {"mode": defs_mode},
        "_meta": {
            "known_words": known_words,
            "mature_words": mature_words,
            "mean_understood": mean_understood,
        },
    }


def _mean_understood(config: Config) -> float | None:
    """Evidence-weighted mean comprehension across tracked keys (None when
    the shadow store has no confident data yet)."""
    try:
        from moguru.mcp.shadow_mcp import core as shadow_core

        cmap = shadow_core.comprehension_map(config=config)
        mean = cmap.get("mean_p_understood", {}).get("reading")
        if cmap.get("tracked_keys", 0) >= 20 and mean is not None:
            return float(mean)
    except Exception:
        pass
    return None


def evaluate(config: Config | None = None, apply: bool = True) -> dict[str, Any]:
    """Compute the proposal, diff it against the current adaptive overlay,
    and (if changed) write adaptive.yaml + append to the changelog."""
    from moguru.mcp.kb_mcp import core as kb_core
    from moguru.mcp.srs_mcp import core as srs_core

    config = config or Config.load()
    s = kb_core.stats()
    known_words = s["known_words"]

    backend = srs_core.get_backend(config)
    try:
        mature = backend.import_known()
    except Exception:
        mature = []

    proposal = _propose(
        known_words, len(mature), mean_understood=_mean_understood(config)
    )
    proposal_public = {k: v for k, v in proposal.items() if not k.startswith("_")}

    adaptive_path = config.user_dir / "adaptive.yaml"
    current: dict[str, Any] = {}
    if adaptive_path.exists():
        current = yaml.safe_load(adaptive_path.read_text(encoding="utf-8")) or {}

    changed = proposal_public != current
    result = {
        "known_words": known_words,
        "mature_words": len(mature),
        "current": current or None,
        "proposed": proposal_public,
        "changed": changed,
        "applied": False,
    }
    if changed and apply:
        config.user_dir.mkdir(parents=True, exist_ok=True)
        adaptive_path.write_text(
            yaml.safe_dump(proposal_public, allow_unicode=True, sort_keys=True),
            encoding="utf-8",
        )
        entry = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "version": _next_version(config),
            "from": current or None,
            "to": proposal_public,
            "evidence": proposal["_meta"],
            "reversible": True,
        }
        log_path = config.user_dir / "adaptivity_log.jsonl"
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        result["applied"] = True
    return result


def _next_version(config: Config) -> int:
    log_path = config.user_dir / "adaptivity_log.jsonl"
    if not log_path.exists():
        return 1
    try:
        lines = [l for l in log_path.read_text(encoding="utf-8").splitlines() if l.strip()]
        return json.loads(lines[-1]).get("version", 0) + 1
    except (json.JSONDecodeError, IndexError):
        return 1


def revert(config: Config | None = None) -> dict[str, Any]:
    """Revert to the previous adaptive overlay version (or none)."""
    config = config or Config.load()
    log_path = config.user_dir / "adaptivity_log.jsonl"
    adaptive_path = config.user_dir / "adaptive.yaml"
    if not log_path.exists():
        return {"reverted": False, "reason": "no adaptivity history"}
    lines = [l for l in log_path.read_text(encoding="utf-8").splitlines() if l.strip()]
    if not lines:
        return {"reverted": False, "reason": "empty adaptivity history"}
    entries = []
    for l in lines:
        try:
            entries.append(json.loads(l))
        except json.JSONDecodeError:
            continue
    prev = entries[-2]["to"] if len(entries) >= 2 else {}
    if prev:
        adaptive_path.write_text(
            yaml.safe_dump(prev, allow_unicode=True, sort_keys=True), encoding="utf-8"
        )
    elif adaptive_path.exists():
        adaptive_path.unlink()
    entry = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "version": entries[-1].get("version", 0) + 1,
        "from": entries[-1].get("to"),
        "to": prev or None,
        "action": "revert",
        "reversible": True,
    }
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return {"reverted": True, "restored": prev or None}
