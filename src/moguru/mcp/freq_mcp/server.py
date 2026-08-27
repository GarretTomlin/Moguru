"""freq-mcp server — spec §3.3 tools over stdio MCP.

Run: python -m moguru.mcp.freq_mcp.server
"""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP

from . import core

mcp = FastMCP("freq")


@mcp.tool()
def frequency(lemma: str, reading: str | None = None) -> dict[str, Any]:
    """Frequency of a lemma -> { bccwj_rank?, jpdb_rank?, jpdb_freq_class? }."""
    return core.frequency(lemma, reading)


@mcp.tool()
def rank_by_frequency(lemmas: list[str]) -> list[dict[str, Any]]:
    """Rank lemmas by JPDB frequency, ascending (frequent first)."""
    return core.rank_by_frequency(lemmas)


if __name__ == "__main__":
    mcp.run()
