"""Engine service endpoints (main spec §8.1 + Reader §3) via TestClient."""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module", autouse=True)
def require_dbs():
    from moguru.config import Config, REPO_ROOT

    cfg = Config.load(REPO_ROOT / "config.yaml")
    if not (cfg.dict_db.exists() and cfg.freq_db.exists()):
        pytest.skip("dictionaries not built — run `moguru data build`")


@pytest.fixture()
def client(temp_config):
    from starlette.testclient import TestClient

    from moguru.orchestrator import service

    app = service.build_app(temp_config)
    with TestClient(app) as tc:
        yield tc


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200 and r.json()["ok"] is True


def test_annotate_endpoint(client, temp_config):
    from moguru.mcp.kb_mcp import core as kb_core

    # fresh temp kb: seed the rest of the sentence so 水 is the single unknown
    for w in ["猫", "飲む"]:
        kb_core.mark_known(w, "manual")
    r = client.post("/annotate", json={"text": "猫は水を飲んだ。", "page": 3})
    assert r.status_code == 200
    data = r.json()
    assert data["tokens"] and data["sentences"]
    assert data["page"] == 3  # Reader spec §3: page echoes for cache/log keys
    bands = {t["surface"]: t["band"] for t in data["tokens"] if t["band"] != "plain"}
    assert bands.get("水") == "iplus"


def test_mine_with_target_is_authoritative(client, temp_config):
    """Reader spec §3: /mine { text, target } — the clicked word wins, even
    when the sentence is not an auto i+1 candidate."""
    from moguru.mcp.kb_mcp import core as kb_core
    from moguru.mcp.srs_mcp import core as srs_core

    # nothing known -> 3 unknowns -> NOT an auto-candidate…
    sentence = "明日は雨が降るでしょう。"
    r = client.post("/mine", json={"text": sentence, "target": "雨"})
    assert r.status_code == 200
    item = r.json()["results"][0]
    assert item["candidate"]["target"] == "雨"
    assert item["fields"]["TargetWord"] == "雨"
    assert item["fields"]["Sentence"] == sentence
    assert "あめ" in item["fields"]["Reading"]
    # …and it can still be added on request
    r2 = client.post("/mine", json={"text": sentence, "target": "雨", "add": True})
    item2 = r2.json()["results"][0]
    assert "note_id" in item2
    kb = kb_core.stats()
    assert kb["encounters"] >= 1


def test_mine_unknown_target_422(client):
    r = client.post("/mine", json={"text": "猫がいる。", "target": "存在しない語"})
    assert r.status_code == 422
    assert "not found" in r.json()["error"]


def test_mark_known_bumps_version(client):
    v1 = client.get("/known_version").json()["version"]
    r = client.post("/mark_known", json={"lemma": "みらくる語"})
    assert r.status_code == 200 and r.json()["ok"]
    v2 = client.get("/known_version").json()["version"]
    assert v2 != v1


def test_lookup_endpoint(client):
    r = client.post("/lookup", json={"text": "魚を食べた。"})
    assert r.status_code == 200
    data = r.json()
    assert any(t["lemma"] == "食べる" for t in data["tokens"])
    assert "食べる" in data["entries"]


def test_assess_endpoint(client):
    r = client.post("/assess", json={"text": "猫は魚を食べた。明日は雨が降るでしょう。"})
    assert r.status_code == 200
    assert r.json()["verdict"] in {"too_easy", "iplus_sweet_spot", "too_hard"}


def test_mine_endpoint_dry(client):
    from moguru.mcp.kb_mcp import core as kb_core

    kb_core.mark_known("猫", "manual")
    kb_core.mark_known("魚", "manual")
    r = client.post("/mine", json={"text": "猫は魚を食べた。"})
    assert r.status_code == 200
    results = r.json()["results"]
    assert results and results[0]["candidate"]["target"] == "食べる"
    assert "note_id" not in results[0]  # dry run


def test_validation_errors(client):
    assert client.post("/annotate", json={"text": ""}).status_code == 400
    assert client.post("/mark_known", json={}).status_code == 400
    assert client.post("/assess", json={"text": " "}).status_code == 400


def test_signals_endpoint(client):
    r = client.post("/signals", json={"signals": [
        {"type": "hover", "key": "鱼", "sentence": "魚を見た。", "modality": "reading"},
        {"type": "bad-type", "sentence": "x", "modality": "reading"},
    ]})
    assert r.status_code == 200
    data = r.json()
    assert data["total"] == 2 and data["accepted"] == 1
    assert client.post("/signals", json={}).status_code == 400


def test_annotate_known_unstable_band(client, temp_config):
    from moguru.mcp.kb_mcp import core as kb_core
    from moguru.mcp.shadow_mcp import core as shadow_core

    kb_core.mark_known("揺らぐ", "manual")
    # enough hard lookups to pass min_samples and sink p below 0.6
    for _ in range(5):
        shadow_core.record_signal({"type": "lookup", "key": "揺らぐ", "sentence": "揺らぐ語。",
                                   "modality": "reading"}, temp_config)
    r = client.post("/annotate", json={"text": "揺らぐ語を読む。"})
    data = r.json()
    assert "揺らぐ" in data.get("known_unstable", [])
    unstable_tokens = [t for t in data["tokens"] if t["band"] == "known_unstable"]
    assert any(t["lemma"] == "揺らぐ" for t in unstable_tokens)


def test_mine_add_emits_signal(client, temp_config):
    from moguru.mcp.kb_mcp import core as kb_core
    from moguru.mcp.shadow_mcp import core as shadow_core

    for w in ["猫", "魚"]:
        kb_core.mark_known(w, "manual")
    before = shadow_core.comprehension("食べる", "reading", config=temp_config)["sample_size"]
    client.post("/mine", json={"text": "猫は魚を食べた。", "add": True})
    after = shadow_core.comprehension("食べる", "reading", config=temp_config)["sample_size"]
    assert after == before + 1  # §11: mine signal recorded
