"""Skills loading + Phase A adaptivity tests."""

from __future__ import annotations

from moguru.config import REPO_ROOT
from moguru.orchestrator import adaptivity, skills


def test_all_five_skills_load():
    loaded = skills.load_skills(REPO_ROOT / "skills")
    assert set(loaded) == {
        "sentence-mining", "rtk-kanji", "comprehensibility",
        "monolingual-transition", "card-format",
    }
    for s in loaded.values():
        assert s.description, f"{s.name} missing description"
        assert len(s.body) > 200, f"{s.name} body too short"


def test_adaptivity_thresholds():
    assert adaptivity._propose(500, 10) == {
        "mining": {"iplus_threshold": 1},
        "defs": {"mode": "bilingual"},
        "_meta": {"known_words": 500, "mature_words": 10,
                  "mean_understood": None},
    }
    assert adaptivity._propose(2000, 10)["defs"]["mode"] == "mixed"
    assert adaptivity._propose(5000, 3000)["defs"]["mode"] == "monolingual"
    assert adaptivity._propose(3000, 10)["mining"]["iplus_threshold"] == 2
    # §11: real comprehension gates the shift — 2500 cards at 0.8 real
    # comprehension behaves like 2000 "really known" (mixed, not monolingual)
    assert adaptivity._propose(2500, 10, mean_understood=0.8)["defs"]["mode"] == "mixed"
    assert adaptivity._propose(5200, 10, mean_understood=0.8)["defs"]["mode"] == "monolingual"


def test_adaptivity_writes_overlay_and_logs(temp_config):
    from moguru.mcp.kb_mcp import core as kb_core

    for w in ["魚", "水", "空"]:
        kb_core.mark_known(w, "manual")
    result = adaptivity.evaluate(temp_config, apply=True)
    assert result["applied"] is True
    assert result["proposed"]["defs"]["mode"] == "bilingual"
    assert (temp_config.user_dir / "adaptive.yaml").exists()
    log_lines = (temp_config.user_dir / "adaptivity_log.jsonl").read_text().strip().splitlines()
    assert log_lines
    # second run with same state -> no change
    result2 = adaptivity.evaluate(temp_config, apply=True)
    assert result2["changed"] is False


def test_adaptivity_revert(temp_config):
    from moguru.mcp.kb_mcp import core as kb_core

    kb_core.mark_known("魚", "manual")
    adaptivity.evaluate(temp_config, apply=True)
    out = adaptivity.revert(temp_config)
    assert out["reverted"] is True
