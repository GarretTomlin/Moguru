"""kb-mcp server — spec §3.4 tools over stdio MCP.

Run: python -m moguru.mcp.kb_mcp.server
"""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP

from . import core

mcp = FastMCP("kb")


@mcp.tool()
def is_known(lemma: str) -> dict[str, Any]:
    """Is this lemma in the learner's known set?
    -> { known, strength, source, first_seen, srs_state? }"""
    return core.is_known(lemma)


@mcp.tool()
def get_known_set(source: str | None = None,
                  min_strength: float | None = None) -> list[str]:
    """Known lemmas, optionally filtered by source / minimum strength."""
    filter_: dict[str, Any] = {}
    if source:
        filter_["source"] = source
    if min_strength is not None:
        filter_["min_strength"] = min_strength
    return core.get_known_set(filter_ or None)


@mcp.tool()
def known_kanji() -> list[str]:
    """Kanji the learner already knows."""
    return core.known_kanji()


@mcp.tool()
def mark_known(lemma: str, source: str, reading: str | None = None) -> None:
    """Mark a lemma known. source: 'anki' | 'manual' | 'mined'."""
    core.mark_known(lemma, source, reading)


@mcp.tool()
def mark_kanji_known(char: str, source: str = "manual") -> None:
    """Mark a single kanji as known (gates RTK primitives)."""
    core.mark_kanji_known(char, source)


@mcp.tool()
def record_encounter(lemma: str, context_sentence: str,
                     media_ref: str | None = None) -> None:
    """Record one encounter of a lemma in context (+freq / maturity)."""
    core.record_encounter(lemma, context_sentence, media_ref)


@mcp.tool()
def stats() -> dict[str, Any]:
    """Knowledge-store summary: { known_words, known_kanji, encounters, ... }"""
    return core.stats()


if __name__ == "__main__":
    mcp.run()
