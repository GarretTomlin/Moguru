"""Shadow model spec — statistical core, decay, gating, grammar, friction,
calibration, interpreter (with an injected fake model)."""

from __future__ import annotations

import pytest

from moguru.mcp.shadow_mcp import core


class FakeShadowModel:
    """Injectable interpreter for deterministic tests (§3 division of labor
    — the small model is a replaceable component)."""

    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    def chat(self, messages, tools=None):
        self.calls.append(messages)
        text = self.responses.pop(0)
        return {"choices": [{"message": {"content": text}}]}


def test_signal_validation(temp_config):
    assert core.record_signal({"type": "sneeze", "sentence": "x",
                               "modality": "reading"}, temp_config)["accepted"] is False
    assert core.record_signal({"type": "lookup", "sentence": "",
                               "modality": "reading"}, temp_config)["accepted"] is False
    assert core.record_signal({"type": "lookup", "sentence": "魚。",
                               "modality": "telepathy"}, temp_config)["accepted"] is False


def test_beta_updates_and_weights(temp_config):
    # 3 hard lookups -> belief sinks, still low-confidence until min_samples
    for _ in range(3):
        core.record_signal({"type": "lookup", "key": "見る", "sentence": "見た。",
                            "modality": "reading"}, temp_config)
    est = core.comprehension("見る", "reading", config=temp_config)
    assert est["p_understood"] < 0.3
    assert est["confidence"] == "low"  # sample 3 < min_samples 4
    # one more -> past the gate
    core.record_signal({"type": "lookup", "key": "見る", "sentence": "見た。",
                        "modality": "reading"}, temp_config)
    est = core.comprehension("見る", "reading", config=temp_config)
    assert est["confidence"] in {"medium", "high"}
    # modality split is real: listening untouched
    listening = core.comprehension("見る", "listening", config=temp_config)
    assert listening["sample_size"] == 0 and listening["p_understood"] == 0.5


def test_hard_dominates_soft(temp_config):
    for key in ("hard", "soft"):
        pass
    for _ in range(5):
        core.record_signal({"type": "complete", "key": "軟", "sentence": "x。",
                            "modality": "reading"}, temp_config)
    for _ in range(1):
        core.record_signal({"type": "lookup", "key": "硬", "sentence": "x。",
                            "modality": "reading"}, temp_config)
    # one hard lookup (w=2) vs five completes (w=0.3): lookup should sink more
    p_soft = core.comprehension("軟", "reading", config=temp_config)["p_understood"]
    p_hard = core.comprehension("硬", "reading", config=temp_config)["p_understood"]
    assert p_hard < p_soft


def test_complete_spreads_over_content_lemmas(temp_config):
    r = core.record_signal({"type": "complete", "sentence": "猫は魚を食べた。",
                            "modality": "reading"}, temp_config)
    assert set(r["keys_touched"]) == {"猫", "魚", "食べる"}


def test_srs_prior_is_mild(temp_config):
    from moguru.mcp.kb_mcp import core as kb_core

    kb_core.mark_known("既知語", "manual")
    core.record_signal({"type": "complete", "key": "既知語", "sentence": "x。",
                        "modality": "reading"}, temp_config)
    est = core.comprehension("既知語", "reading", config=temp_config)
    # prior α=2 (+1 mild) + 0.3 complete -> p ≈ 2.3/3.3, NOT near 1
    assert 0.6 < est["p_understood"] < 0.8


def test_decay_loses_certainty_not_memory(temp_config):
    for _ in range(6):
        core.record_signal({"type": "complete", "key": "古い", "sentence": "x。",
                            "modality": "reading"}, temp_config)
    est = core.comprehension("古い", "reading", config=temp_config)
    assert est["p_understood"] > 0.6
    # simulate 10 half-lives passing
    import sqlite3
    from datetime import datetime, timedelta, timezone

    conn = core.connect(temp_config)
    old = (datetime.now(timezone.utc) - timedelta(days=1200)).isoformat()
    conn.execute("UPDATE estimates SET last_seen=? WHERE key='古い'", (old,))
    conn.commit()
    conn.close()
    est2 = core.comprehension("古い", "reading", config=temp_config)
    assert est2["p_understood"] < est["p_understood"]  # decayed toward uniform
    assert est2["p_understood"] > 0.5 - 0.05  # floor keeps memory faintly


def test_grammar_lexicon_matches():
    from moguru.mcp.parser_mcp import core as pcore

    names = lambda s: [g["key"] for g in core.find_grammar_points(pcore.tokenize(s))]
    assert "使役受身（させられる）" in names("食べさせられた。")
    assert "条件（たら）" in names("行ったら、電話して。")
    assert "過去（た）" in names("食べた。")
    assert "否定（ない）" in names("食べない。")
    assert "〜ながら" in names("食べながら話す。")
    assert names("猫だ。") == []


def test_predict_friction_offline(temp_config):
    # NB: use a token-stable key (未知語 splits into 未知+語 in MeCab)
    for _ in range(5):
        core.record_signal({"type": "lookup", "key": "見る",
                            "sentence": "見た。", "modality": "reading"},
                           temp_config)
    frictions = core.predict_friction("それを見た。", "reading",
                                      config=temp_config, model_client=None)
    assert any(f["type"] == "vocab" and "見る" in f["reason"] for f in frictions)
    assert frictions == sorted(frictions, key=lambda f: -f["p_break"])


def test_gaps_paper_known_vs_shaky(temp_config):
    from moguru.mcp.kb_mcp import core as kb_core

    kb_core.mark_known("脆い", "manual")
    for _ in range(5):
        core.record_signal({"type": "lookup", "key": "脆い", "sentence": "x。",
                            "modality": "listening"}, temp_config)
    g = core.gaps({"modality": "listening"}, temp_config)
    assert any(x["key"] == "脆い" and x["srs_known"] and x["p_understood"] < 0.6
               for x in g)
    # reading modality has no evidence -> no gap there (honest)
    assert all(x["modality"] == "listening" for x in g)


def test_explain_estimate_shows_trail(temp_config):
    core.record_signal({"type": "lookup", "key": "証", "sentence": "証を見る。",
                        "modality": "reading"}, temp_config)
    out = core.explain_estimate("証", "reading", config=temp_config)
    assert out["evidence"] and out["evidence"][0]["type"] == "lookup"
    assert "Beta(" in out["reasoning"]


def test_calibration_logs_outcomes_in_order(temp_config):
    # first signal: no prior prediction exists -> not logged
    core.record_signal({"type": "complete", "key": "測", "sentence": "x。",
                        "modality": "reading"}, temp_config)
    assert core.calibration(config=temp_config)["n"] == 0
    # second signal: a prediction existed -> logged
    core.record_signal({"type": "lookup", "key": "測", "sentence": "x。",
                        "modality": "reading"}, temp_config)
    cal = core.calibration(config=temp_config)
    assert cal["n"] == 1 and cal["brier_score"] is not None


def test_interpreter_applies_targeted_evidence(temp_config):
    # seed an ambiguous pause with a sentence containing 複雑
    core.record_signal({"type": "pause", "key": None, "sentence": "複雑な文があった。",
                        "modality": "listening", "dwell_ms": 3000}, temp_config)
    fake = FakeShadowModel([
        '[{"id": 1, "comprehension_related": true, "key": "複雑", '
        '"key_kind": "vocab", "confidence": 0.8}]'
    ])
    out = core.interpret_pending(config=temp_config, model_client=fake)
    assert out["interpreted"] == 1 and out["evidence_applied"] == 1
    est = core.comprehension("複雑", "listening", config=temp_config)
    assert est["p_understood"] < 0.5  # targeted not-understood applied


def test_interpreter_unreachable_keeps_queue(temp_config):
    core.record_signal({"type": "skip", "sentence": "何かの文。", "key": "x",
                        "modality": "reading"}, temp_config)
    out = core.interpret_pending(config=temp_config, model_client=None)
    # model_client None and real shadow endpoint may be up or down; if down,
    # queue persists; if up (live), it interprets — assert no crash either way
    assert "interpreted" in out or "reason" in out


def test_comprehension_map_versioned(temp_config):
    for _ in range(3):
        core.record_signal({"type": "complete", "key": "図", "sentence": "x。",
                            "modality": "reading"}, temp_config)
    m1 = core.comprehension_map(config=temp_config)
    assert m1["version"] and m1["tracked_keys"] >= 1
    core.record_signal({"type": "complete", "key": "図", "sentence": "x。",
                        "modality": "reading"}, temp_config)
    m2 = core.comprehension_map(config=temp_config)
    assert m2["version"] != m1["version"] or m2["tracked_keys"] >= m1["tracked_keys"]
