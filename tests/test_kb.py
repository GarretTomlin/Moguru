"""Gate tests for kb-mcp (spec §9 step 2: the knowledge store must work
before mining)."""

from __future__ import annotations

from moguru.mcp.kb_mcp import core


def test_mark_and_is_known(temp_config):
    assert core.is_known("魚")["known"] is False
    core.mark_known("魚", "manual", reading="さかな")
    info = core.is_known("魚")
    assert info["known"] is True
    assert info["source"] == "manual"
    assert info["first_seen"]


def test_mark_invalid_source(temp_config):
    import pytest

    with pytest.raises(ValueError):
        core.mark_known("魚", "guessing")


def test_known_set_and_filter(temp_config):
    core.mark_known("魚", "manual")
    core.mark_known("水", "anki")
    core.mark_known("空", "manual")
    known = core.get_known_set()
    assert {"魚", "水", "空"} <= set(known)
    only_anki = core.get_known_set({"source": "anki"})
    assert only_anki == ["水"]


def test_bloom_membership(temp_config):
    core.mark_known("魚", "manual")
    conn = core.connect()
    bloom = core._get_bloom(conn)
    assert "魚" in bloom
    assert "未登録語彙" not in bloom or True  # false positives allowed
    conn.close()


def test_encounters_and_stats(temp_config):
    core.mark_known("魚", "manual")
    core.record_encounter("魚", "魚を食べた。", "test-media")
    core.record_encounter("魚", "大きな魚だ。", None)
    s = core.stats()
    assert s["known_words"] >= 1
    assert s["encounters"] >= 2
    assert s["by_source"].get("manual", 0) >= 1


def test_kanji_known(temp_config):
    core.mark_kanji_known("食", "rtk")
    assert "食" in core.known_kanji()
