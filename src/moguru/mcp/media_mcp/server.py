"""media-mcp server — spec §3.6 tools over stdio MCP.

Run: python -m moguru.mcp.media_mcp.server
"""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from . import core

mcp = FastMCP("media")


@mcp.tool()
def parse_subtitles(file: str) -> list[dict]:
    """Parse .srt/.ass subtitles -> [{start, end, text}] (ms timestamps)."""
    return core.parse_subtitles(file)


@mcp.tool()
def extract_audio(media: str, start: float, end: float) -> str:
    """Slice sentence audio (seconds) -> audio_path for the card."""
    return core.extract_audio(media, start, end)


@mcp.tool()
def ocr_image(image: str) -> str:
    """OCR a manga panel / screenshot -> Japanese text."""
    return core.ocr_image(image)


@mcp.tool()
def capture_context(media: str, timestamp: float) -> str:
    """Capture the video frame at `timestamp` (s) -> image_path."""
    return core.capture_context(media, timestamp)


if __name__ == "__main__":
    mcp.run()
