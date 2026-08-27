"""Integration tests for freq-mcp — require built freq.sqlite."""

from __future__ import annotations

import pytest

from moguru.mcp.freq_mcp import core

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module", autouse=True)
def require_db():
    from moguru.config import Config, REPO_ROOT

    cfg = Config.load(REPO_ROOT / "config.yaml")
    if not cfg.freq_db.exists():
        pytest.skip("freq.sqlite not built — run `moguru data build`")


def test_frequency_common_word():
    f = core.frequency("食べる")
    assert f.get("jpdb_rank") is not None
    assert f.get("bccwj_rank") is not None
    assert f["jpdb_rank"] > 0


def test_frequency_kana_resolution():
    # kanji lemma resolves through its kana reading for the JPDB list
    f = core.frequency("水")
    assert f.get("jpdb_rank") is not None


def test_rank_by_frequency_orders_frequent_first():
    ranked = core.rank_by_frequency(["水", "の", "食べる"])
    ranks = [e["jpdb_rank"] for e in ranked if e["jpdb_rank"] is not None]
    assert ranks == sorted(ranks)
    assert ranked[0]["lemma"] == "の"
