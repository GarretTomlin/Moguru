"""MCP stdio smoke test: spawn each server as a subprocess exactly the way
the orchestrator does, list tools, and call one.

parser server is skipped when UniDic is not yet downloaded."""

from __future__ import annotations

import asyncio
import os
import sys

import pytest

from moguru.config import REPO_ROOT
from moguru.orchestrator.agent import CORE_SERVERS

pytestmark = pytest.mark.integration


async def _probe(module: str, tool: str, args: dict):
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    env = {**os.environ, "MOGURU_CONFIG": str(REPO_ROOT / "config.yaml")}
    params = StdioServerParameters(command=sys.executable, args=["-m", module], env=env)
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            names = [t.name for t in tools.tools]
            result = await session.call_tool(tool, args)
            texts = [c.text for c in result.content if hasattr(c, "text")]
            return names, "\n".join(texts)


def test_kb_server_roundtrip(temp_config):
    names, out = asyncio.run(_probe("moguru.mcp.kb_mcp.server", "stats", {}))
    assert "is_known" in names and "stats" in names
    assert "known_words" in out


def test_srs_server_roundtrip(temp_config):
    names, out = asyncio.run(
        _probe("moguru.mcp.srs_mcp.server", "due_cards", {"deck": ""})
    )
    assert "add_card" in names and "import_known" in names


def test_dict_server_roundtrip():
    from moguru.config import Config

    if not Config.load(REPO_ROOT / "config.yaml").dict_db.exists():
        pytest.skip("dict.sqlite not built")
    names, out = asyncio.run(
        _probe("moguru.mcp.dict_mcp.server", "lookup_kanji", {"char": "明"})
    )
    assert "lookup_word" in names and "decompose_kanji" in names
    assert "bright" in out.lower()


def test_freq_server_roundtrip():
    from moguru.config import Config

    if not Config.load(REPO_ROOT / "config.yaml").freq_db.exists():
        pytest.skip("freq.sqlite not built")
    names, _ = asyncio.run(
        _probe("moguru.mcp.freq_mcp.server", "frequency", {"lemma": "水"})
    )
    assert "frequency" in names and "rank_by_frequency" in names


def test_parser_server_roundtrip():
    try:
        import unidic  # noqa: F401
        from fugashi import GenericTagger  # noqa: F401
        import pathlib

        if not (pathlib.Path(unidic.DICDIR) / "dicrc").exists():
            pytest.skip("UniDic dicdir not extracted yet")
    except Exception:
        pytest.skip("parser deps unavailable")
    names, out = asyncio.run(
        _probe("moguru.mcp.parser_mcp.server", "tokenize", {"text": "魚を食べた。"})
    )
    assert "tokenize" in names and "deinflect" in names
    assert "食べる" in out


def test_media_server_lists():
    names, _ = asyncio.run(
        _probe("moguru.mcp.media_mcp.server", "parse_subtitles",
               {"file": "/nonexistent.srt"})
    )
    # parse_subtitles call itself fails on the missing file — that's fine,
    # we only assert the server mounts with its four tools.
    assert {"parse_subtitles", "extract_audio", "ocr_image", "capture_context"} <= set(names)
