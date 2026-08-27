"""srs-mcp server — spec §3.5 tools over stdio MCP.

Run: python -m moguru.mcp.srs_mcp.server
"""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP

from . import core

mcp = FastMCP("srs")


@mcp.tool()
def add_card(deck: str, note_type: str, fields: dict[str, str],
             tags: list[str]) -> int:
    """Add a note; returns its note_id."""
    return core.get_backend().add_card(deck, note_type, fields, tags)


@mcp.tool()
def find_notes(query: str) -> list[int]:
    """Find note ids matching a query."""
    return core.get_backend().find_notes(query)


@mcp.tool()
def update_note(note_id: int, fields: dict[str, str]) -> None:
    """Update fields on an existing note."""
    return core.get_backend().update_note(note_id, fields)


@mcp.tool()
def due_cards(deck: str = "") -> list[dict[str, Any]]:
    """Cards currently due in a deck ('' = all decks)."""
    return core.get_backend().due_cards(deck)


@mcp.tool()
def review_note(note_id: int, rating: str) -> dict[str, Any]:
    """Rate a builtin-FSRS card: again | hard | good | easy."""
    return core.get_backend().review_note(note_id, rating)


@mcp.tool()
def import_known() -> list[str]:
    """Extract learned (mature) lemmas -> feeds kb-mcp.mark_known."""
    return core.get_backend().import_known()


if __name__ == "__main__":
    mcp.run()
