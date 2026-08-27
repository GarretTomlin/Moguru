"""dict-mcp server — spec §3.2 tools over stdio MCP.

Run: python -m moguru.mcp.dict_mcp.server
"""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP

from . import core

mcp = FastMCP("dict")


@mcp.tool()
def lookup_word(query: str, reading: str | None = None) -> list[dict[str, Any]]:
    """JMdict J-E lookup (ground truth). Entry = { headwords[], readings[],
    senses[{gloss[], pos[], misc[], field[]}], id }."""
    return core.lookup_word(query, reading)


@mcp.tool()
def lookup_name(query: str) -> list[dict[str, Any]]:
    """JMnedict name lookup (people/places)."""
    return core.lookup_name(query)


@mcp.tool()
def lookup_kanji(char: str) -> dict[str, Any]:
    """KANJIDIC2 kanji entry: readings, meanings, strokes, grade, jlpt,
    freq_rank, radicals, components."""
    result = core.lookup_kanji(char)
    return result if result is not None else {"error": f"no KANJIDIC2 entry for {char!r}"}


@mcp.tool()
def lookup_monolingual(query: str) -> list[dict[str, Any]]:
    """J-J (monolingual) definition. Requires an imported J-J package."""
    return core.lookup_monolingual(query)


@mcp.tool()
def decompose_kanji(char: str) -> list[dict[str, Any]]:
    """Radicals/primitives composing a kanji — feeds RTK mnemonic stories."""
    return core.decompose_kanji(char)


@mcp.tool()
def lookup_pitch(headword: str, reading: str | None = None) -> list[dict[str, Any]]:
    """kanjium pitch accent entries for a headword."""
    return core.lookup_pitch(headword, reading)


if __name__ == "__main__":
    mcp.run()
