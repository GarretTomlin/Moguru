"""Models & Providers spec — store, bindings, validation, guardrails."""

from __future__ import annotations

import pytest

from moguru.orchestrator import providers as pm


def test_provider_store_roundtrip(temp_config):
    pm.add_provider(
        pm.Provider(id="p1", endpoint="http://localhost:11434/v1",
                    model="m1", runtime="ollama"),
        temp_config,
    )
    got = pm.get_provider("p1", temp_config)
    assert got and got.model == "m1" and got.is_local
    with pytest.raises(pm.ProviderError):
        pm.add_provider(
            pm.Provider(id="p1", endpoint="http://x/v1", model="m"), temp_config
        )


def test_provider_key_env_must_exist(temp_config, monkeypatch):
    monkeypatch.delenv("TOTALLY_FAKE_KEY", raising=False)
    with pytest.raises(pm.ProviderError, match="TOTALLY_FAKE_KEY"):
        pm.add_provider(
            pm.Provider(id="hosted", endpoint="https://api.example.com/v1",
                        model="m", api_key_env="TOTALLY_FAKE_KEY"),
            temp_config,
        )


def test_corrupt_providers_file(temp_config):
    path = pm.providers_path(temp_config)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(pm.ProviderError, match="corrupt"):
        pm.load_providers(temp_config)


def test_role_bindings_and_removal_guard(temp_config):
    pm.add_provider(
        pm.Provider(id="local", endpoint="http://localhost:11434/v1",
                    model="m", runtime="ollama"),
        temp_config,
    )
    pm.set_binding("main", "local", temp_config)
    assert pm.load_bindings(temp_config) == {"main": "local"}
    with pytest.raises(pm.ProviderError, match="bound to role"):
        pm.remove_provider("local", temp_config)


def test_resolve_role_fallback_and_headers(temp_config, monkeypatch):
    # no binding → falls back to config.yaml local endpoint
    provider, headers = pm.resolve_role("main", temp_config)
    assert provider.endpoint == temp_config.local.endpoint
    assert headers == {}

    # hosted binding with key set → Bearer header
    monkeypatch.setenv("FAKE_TEST_KEY", "sk-test")
    pm.add_provider(
        pm.Provider(id="hosted", endpoint="https://api.example.com/v1",
                    model="claude-x", api_key_env="FAKE_TEST_KEY"),
        temp_config,
    )
    pm.set_binding("main", "hosted", temp_config)
    provider, headers = pm.resolve_role("main", temp_config)
    assert headers["Authorization"] == "Bearer sk-test"
    assert not provider.is_local


def test_validate_provider_dead_endpoint(temp_config):
    provider = pm.Provider(id="dead", endpoint="http://localhost:9/v1", model="m")
    with pytest.raises(pm.ProviderError, match="cannot reach"):
        pm.validate_provider(provider)


def test_validate_provider_missing_model_hint(temp_config):
    provider = pm.Provider(id="oll", endpoint="http://localhost:11434/v1",
                           model="definitely-not-a-real-model-xyz")
    with pytest.raises(pm.ProviderError, match="ollama pull"):
        pm.validate_provider(provider)


def test_shadow_hosted_guardrail_is_cli_level(temp_config):
    """The guardrail lives in `moguru model set shadow` (SHADOW_WARNING +
    --i-know); here we verify the pieces it depends on."""
    assert "shadow" in pm.ROLES and "main" in pm.ROLES
    provider = pm.Provider(id="h", endpoint="https://api.example.com/v1",
                           model="m")
    assert not provider.is_local  # what triggers the warning


def test_hosted_runtime_preset_gemini(temp_config, monkeypatch):
    """`--runtime gemini` fills endpoint + key name; the key itself is only
    ever referenced by env-var name (never stored)."""
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    pm.add_provider(
        pm.Provider(id="gemini", endpoint="", model="gemini-2.5-flash",
                    runtime="gemini"),
        temp_config,
    )
    (g,) = [p for p in pm.load_providers(temp_config) if p.id == "gemini"]
    assert g.endpoint == ("https://generativelanguage.googleapis.com"
                          "/v1beta/openai")
    assert g.api_key_env == "GEMINI_API_KEY"
    assert not g.is_local
    pm.remove_provider("gemini", temp_config)


def test_hosted_runtime_preset_explicit_values_win(temp_config, monkeypatch):
    monkeypatch.setenv("MY_KEY", "test-key")
    pm.add_provider(
        pm.Provider(id="custom", endpoint="https://proxy.example.com/v1",
                    model="m", api_key_env="MY_KEY", runtime="gemini"),
        temp_config,
    )
    (p,) = [p for p in pm.load_providers(temp_config) if p.id == "custom"]
    assert p.endpoint == "https://proxy.example.com/v1"  # explicit wins
    assert p.api_key_env == "MY_KEY"
    pm.remove_provider("custom", temp_config)


@pytest.mark.integration
def test_validate_live_ollama(temp_config):
    import requests

    try:
        tags = requests.get("http://localhost:11434/api/tags", timeout=2).json()
    except requests.RequestException:
        pytest.skip("Ollama not running")
    models = [m["name"] for m in tags.get("models", [])]
    if not models:
        pytest.skip("Ollama running but no models pulled")
    provider = pm.Provider(
        id="live", endpoint="http://localhost:11434/v1", model=models[0],
    )
    report = pm.validate_provider(provider)
    assert report["ok"] and report["local"] and report["latency_ms"] > 0


def test_list_models_normalizes_google_prefix(monkeypatch):
    """Google's OpenAI-compat /models lists ids as "models/<name>" while
    chat accepts the bare name — validation must compare normalized ids."""
    class FakeResp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"data": [{"id": "models/gemini-3.1-pro-preview"},
                             {"id": "local-model"}]}

    monkeypatch.setattr(pm.requests, "get", lambda *a, **k: FakeResp())
    ids = pm._list_models("https://x.example/v1", {})
    assert "gemini-3.1-pro-preview" in ids
    assert not any(i.startswith("models/") for i in ids)
