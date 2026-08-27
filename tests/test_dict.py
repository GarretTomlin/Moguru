"""Integration tests for dict-mcp — require built dict.sqlite
(`moguru data build`)."""

from __future__ import annotations

import pytest

from moguru.mcp.dict_mcp import core

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module", autouse=True)
def require_db():
    from moguru.config import Config, REPO_ROOT

    cfg = Config.load(REPO_ROOT / "config.yaml")
    if not cfg.dict_db.exists():
        pytest.skip("dict.sqlite not built — run `moguru data build`")


def test_lookup_word_taberu():
    entries = core.lookup_word("食べる")
    assert entries, "no JMdict entry for 食べる"
    e = entries[0]
    assert "食べる" in e["headwords"]
    assert any("たべる" == r for r in e["readings"])
    assert any("eat" in g.lower() for s in e["senses"] for g in s["gloss"])


def test_lookup_word_with_reading_filter():
    entries = core.lookup_word("橋", "はし")
    assert entries
    # same kanji, different reading -> filtered out
    none = core.lookup_word("橋", "こんなはずじゃ")
    assert none == []


def test_lookup_kanji_shoku():
    e = core.lookup_kanji("食")
    assert e is not None
    assert e["stroke_count"] == 9
    assert any("eat" in m.lower() for m in e["meanings"])
    assert "ショク" in e["on_readings"]


def test_decompose_kanji():
    comps = core.decompose_kanji("明")
    chars = [c["component"] for c in comps]
    assert "日" in chars and "月" in chars
    # 食 is its own classical radical (#184) — data-grounded self-composition
    assert [c["component"] for c in core.decompose_kanji("食")] == ["食"]


def test_lookup_pitch():
    p = core.lookup_pitch("食べる", "たべる")
    assert p and p[0]["accents"]


def test_lookup_name():
    names = core.lookup_name("田中")
    assert names, "no JMnedict entry for 田中"


def test_lookup_monolingual_not_configured():
    with pytest.raises(FileNotFoundError):
        core.lookup_monolingual("食べる")
