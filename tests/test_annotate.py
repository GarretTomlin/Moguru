"""Reader band classification (Reader spec §2)."""

from __future__ import annotations

import pytest

from moguru.orchestrator.annotate import annotate_text

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module", autouse=True)
def require_dbs():
    from moguru.config import Config, REPO_ROOT

    cfg = Config.load(REPO_ROOT / "config.yaml")
    if not (cfg.dict_db.exists() and cfg.freq_db.exists()):
        pytest.skip("dictionaries not built — run `moguru data build`")


def _bands(data):
    return {t["surface"]: t["band"] for t in data["tokens"] if t["band"] != "plain"}


def test_band_classification(temp_config):
    from moguru.mcp.kb_mcp import core as kb_core

    for w in ["猫", "魚", "食べる", "飲む"]:
        kb_core.mark_known(w, "manual")
    text = "猫は魚を食べた。猫は水を飲んだ。明日は雨が降るでしょう。"
    data = annotate_text(text, temp_config)
    bands = _bands(data)
    assert bands["猫"] == "known"
    assert bands["魚"] == "known"
    assert bands["食べ"] == "known"
    assert bands["水"] == "iplus"        # single unknown in its sentence
    assert bands["明日"] == "new_hard"   # sentence has 3 unknowns
    assert bands["雨"] == "new_hard"
    assert bands["降る"] == "new_hard"
    # particles stay plain
    assert "は" not in bands
    # sentence offsets reconstruct the text
    s = data["sentences"]
    assert s[0]["char_start"] == 0
    assert text[s[0]["char_start"]:s[0]["char_end"]].endswith("。")
    assert s[0]["unknown_count"] == 0
    assert s[1]["unknown_count"] == 1
    assert s[2]["unknown_count"] == 3


def test_all_known_is_clean(temp_config):
    from moguru.mcp.kb_mcp import core as kb_core

    for w in ["猫", "魚", "食べる"]:
        kb_core.mark_known(w, "manual")
    data = annotate_text("猫は魚を食べた。", temp_config)
    assert all(t["band"] in {"known", "plain"} for t in data["tokens"])
