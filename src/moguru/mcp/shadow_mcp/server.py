"""shadow-mcp server — spec §7 tools over stdio MCP.

Run: python -m moguru.mcp.shadow_mcp.server
"""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP

from . import core

mcp = FastMCP("shadow")


@mcp.tool()
def record_signal(signal: dict[str, Any]) -> dict[str, Any]:
    """Ingest one behavioral event (§4 Signal schema).
    -> { accepted, keys_touched[] }"""
    return core.record_signal(signal)


@mcp.tool()
def comprehension(key: str, modality: str, key_kind: str = "vocab") -> dict[str, Any]:
    """Belief about understanding one key in one modality.
    Estimate = { p_understood, confidence, sample_size, last_seen }."""
    return core.comprehension(key, modality, key_kind)


@mcp.tool()
def comprehension_batch(keys: list[str], modality: str,
                        key_kind: str = "vocab") -> list[dict[str, Any]]:
    """Bulk estimates — feeds the reader's /annotate known_unstable band."""
    return core.comprehension_batch(keys, modality, key_kind)


@mcp.tool()
def predict_friction(sentence: str, modality: str = "reading") -> list[dict[str, Any]]:
    """Simulate the learner's read of an unseen sentence; what breaks?
    Friction = { span, type: vocab|grammar|parse_speed, p_break, reason }."""
    return core.predict_friction(sentence, modality)


@mcp.tool()
def gaps(modality: str | None = None, key_kind: str = "vocab") -> list[dict[str, Any]]:
    """Paper-known (kb) vs shaky-in-practice — the whole point."""
    return core.gaps({"modality": modality, "key_kind": key_kind})


@mcp.tool()
def comprehension_map(scope: str | None = None) -> dict[str, Any]:
    """Versioned heatmap artifact of real comprehension (both modalities)."""
    return core.comprehension_map(scope)


@mcp.tool()
def explain_estimate(key: str, modality: str, key_kind: str = "vocab") -> dict[str, Any]:
    """Transparency: the evidence trail behind an estimate."""
    return core.explain_estimate(key, modality, key_kind)


@mcp.tool()
def calibration() -> dict[str, Any]:
    """Is the model any good? Decile curve + Brier score."""
    return core.calibration()


@mcp.tool()
def interpret_pending(limit: int = 16) -> dict[str, Any]:
    """Run the small-model interpreter over the ambiguous-signal queue."""
    return core.interpret_pending(limit)


if __name__ == "__main__":
    mcp.run()
