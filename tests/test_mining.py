"""End-to-end tests for the mining loop (spec §9 step 3) — the core loop,
run only with parser + kb + dict + freq + srs all returning correct data."""

from __future__ import annotations

import pytest

from moguru.orchestrator import mining

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module", autouse=True)
def require_dbs():
    from moguru.config import Config, REPO_ROOT

    cfg = Config.load(REPO_ROOT / "config.yaml")
    if not (cfg.dict_db.exists() and cfg.freq_db.exists()):
        pytest.skip("dictionaries not built — run `moguru data build`")


def test_iplus_one_unknown(temp_config):
    from moguru.mcp.kb_mcp import core as kb_core

    kb_core.mark_known("魚", "manual")
    cand = mining.is_iplus("魚を食べた。", temp_config)
    assert cand is not None
    assert cand.target == "食べる"
    assert cand.coverage >= 1


def test_iplus_rejects_two_unknown(temp_config):
    # both 魚 and 食べる unknown, threshold=1 -> rejected
    cand = mining.is_iplus("魚を食べた。", temp_config)
    assert cand is None


def test_find_candidates_ranks_frequent_first(temp_config):
    from moguru.mcp.kb_mcp import core as kb_core

    for w in ["魚", "今日", "いい", "天気", "ですね"]:
        kb_core.mark_known(w, "manual")
    text = "今日はいい天気ですね。魚を食べた。"
    cands = mining.find_candidates(text, temp_config)
    assert cands, "expected at least one candidate"
    ranks = [c.freq_rank for c in cands if c.freq_rank is not None]
    assert ranks == sorted(ranks)


def test_mine_text_creates_card(temp_config):
    from moguru.mcp.kb_mcp import core as kb_core
    from moguru.mcp.srs_mcp import core as srs_core

    kb_core.mark_known("魚", "manual")
    results = mining.mine_text("魚を食べた。", temp_config, auto_add=True)
    assert results
    item = results[0]
    assert item["candidate"]["target"] == "食べる"
    fields = item["fields"]
    assert fields["TargetWord"] == "食べる"
    assert fields["Sentence"] == "魚を食べた。"
    assert fields["Reading"] == "たべる"
    assert "eat" in fields["Definition"].lower()
    assert "note_id" in item
    # card is registered and due
    due = srs_core.get_backend(temp_config).due_cards(temp_config.anki_deck)
    assert any(c["note_id"] == item["note_id"] for c in due)
    # encounter recorded
    s = kb_core.stats()
    assert s["encounters"] >= 1


def test_assess_text(temp_config):
    from moguru.mcp.kb_mcp import core as kb_core

    # NB: UniDic lemmatizes いい -> 良い, so the *lemma* is what must be known
    for w in ["魚", "食べる", "今日", "良い", "天気"]:
        kb_core.mark_known(w, "manual")
    result = mining.assess_text("今日はいい天気ですね。魚を食べた。", temp_config)
    assert result["pct_known"] == 1.0
    assert result["verdict"] == "too_easy"
    assert result["sentence_count"] == 2
