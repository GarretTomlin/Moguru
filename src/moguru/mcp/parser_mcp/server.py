"""parser-mcp server — spec §3.1 tools over stdio MCP.

Run: python -m moguru.mcp.parser_mcp.server
"""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP

from . import core

mcp = FastMCP("parser")


@mcp.tool()
def tokenize(text: str) -> list[dict[str, Any]]:
    """Tokenize Japanese text into morphs with lemma, reading, POS, pitch.

    Token = { surface, lemma, reading_kana, pos, pos_detail, inflection_type,
              base_form, pitch_accent?, char_start, char_end }
    """
    return core.tokenize(text)


@mcp.tool()
def deinflect(surface: str) -> list[dict[str, Any]]:
    """Return candidate dictionary-form lemmas for an inflected surface
    (見た -> 見る). Each candidate = { lemma, via, }."""
    return core.deinflect(surface)


@mcp.tool()
def segment_sentences(text: str) -> list[str]:
    """Split raw text into sentences on terminal punctuation."""
    return core.segment_sentences(text)


@mcp.tool()
def to_reading(text: str, mode: str = "hiragana") -> str:
    """Whole-text reading ('hiragana' | 'katakana' | 'romaji')."""
    if mode not in {"hiragana", "katakana", "romaji"}:
        raise ValueError(f"mode must be hiragana|katakana|romaji, got {mode!r}")
    return core.to_reading(text, mode)


if __name__ == "__main__":
    mcp.run()
