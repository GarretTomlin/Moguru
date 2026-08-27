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
