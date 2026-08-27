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


def test_mine_text_creates_card(temp_config, monkeypatch):
    monkeypatch.setattr(mining, "_pick_sense_index", lambda *a, **k: 0)
    from moguru.mcp.kb_mcp import core as kb_core
    from moguru.mcp.srs_mcp import core as srs_core

    kb_core.mark_known("魚", "manual")
    results = mining.mine_text("魚を食べた。", temp_config, auto_add=True)
    assert results
    item = results[0]
    assert item["candidate"]["target"] == "食べる"
    fields = item["fields"]
    assert fields["TargetWord"] == "食べる"
    assert fields["Sentence"] == "魚を<b>食べた</b>。"  # target bolded in situ
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


def test_prepare_sentence_migaku_discipline():
    """One clean sentence, target bolded — the 号 card bug (whole-paragraph
    sentence, PDF spacing, every-sense definition dump)."""
    raw = ("序 この書は雑誌 『 思想 』 第九十二号 所載 の論文 である 。 "
           "生きた哲学は現実を理解し得るものでなくてはならぬ 。")
    out = mining._prepare_sentence(raw, "号")
    assert out == ("序この書は雑誌『思想』第九十二<b>号</b>所載の論文である。")
    assert "生きた哲学" not in out  # trimmed to the target's sentence


def test_prepare_sentence_keeps_latin_spacing():
    out = mining._prepare_sentence("これは Apple の製品 である。", "Apple")
    assert out == "これは <b>Apple</b> の製品である。"


def test_render_definition_picks_context_sense(temp_config, monkeypatch):
    entry = {"senses": [
        {"gloss": ["counter for vehicles"]},
        {"gloss": ["issue (of a magazine)", "number of a periodical"]},
        {"gloss": ["sobriquet", "pen-name"]},
    ]}
    monkeypatch.setattr(mining, "_pick_sense_index", lambda *a, **k: 1)
    out = mining._render_definition("号", entry, temp_config, sentence="第九十二号。")
    assert out == "issue (of a magazine); number of a periodical"
    assert "sobriquet" not in out  # one sense, not the whole entry


def test_pick_sense_index_model_number_only(temp_config, monkeypatch):
    from moguru.orchestrator import agent

    class FakeRouter:
        def __init__(self, cfg):
            pass

        def chat(self, messages, tools=None, max_tokens=None, extra_body=None):
            assert max_tokens == 8  # number-only answer, bounded
            return {"choices": [{"message": {"content": "2"}}]}

    monkeypatch.setattr(agent, "ModelRouter", FakeRouter)
    senses = [{"gloss": ["a"]}, {"gloss": ["b"]}, {"gloss": ["c"]}]
    assert mining._pick_sense_index("x", "文。", senses, temp_config) == 2


def test_pick_sense_index_unreachable_falls_back(temp_config, monkeypatch):
    from moguru.orchestrator import agent

    class BoomRouter:
        def __init__(self, cfg):
            raise RuntimeError("model down")

    monkeypatch.setattr(agent, "ModelRouter", BoomRouter)
    senses = [{"gloss": ["a"]}, {"gloss": ["b"]}]
    assert mining._pick_sense_index("x", "文。", senses, temp_config) == 0
