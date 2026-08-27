"""Models & Providers — runtime model swapping with one command.

Every provider — local or hosted — collapses to the same four fields, so
nothing downstream knows or cares whether the model is on your GPU or in a
datacenter:

    { "id", "endpoint", "model", "api_key_env"? }

Providers live in data/user/providers.json; role→provider bindings
(main / shadow) persist in data/user/config.yaml. Keys are pulled from env
vars, never stored inline.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import requests
import yaml

from moguru.config import Config, REPO_ROOT

ROLES = ("main", "shadow")

REQUIRED_FIELDS = ("id", "endpoint", "model")


class ProviderError(Exception):
    """Validation failure with an actionable message (§5)."""


@dataclass
class Provider:
    id: str
    endpoint: str
    model: str
    api_key_env: str | None = None
    runtime: str = field(default="")  # informational: ollama | lmstudio | hosted

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "id": self.id,
            "endpoint": self.endpoint,
            "model": self.model,
        }
        if self.api_key_env:
            out["api_key_env"] = self.api_key_env
        if self.runtime:
            out["runtime"] = self.runtime
        return out

    @property
    def is_local(self) -> bool:
        host = self.endpoint.split("//")[-1].split("/")[0]
        return host.startswith("localhost") or host.startswith("127.0.0.1") or host.startswith("[")


# ---------------------------------------------------------------------------
# Store — data/user/providers.json
# ---------------------------------------------------------------------------

def providers_path(config: Config | None = None) -> Path:
    config = config or Config.load()
    return config.user_dir / "providers.json"


def load_providers(config: Config | None = None) -> list[Provider]:
    config = config or Config.load()
    path = providers_path(config)
    if not path.exists():
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise ProviderError(f"providers.json is corrupt: {e}") from e
    out = []
    for rec in raw:
        missing = [f for f in REQUIRED_FIELDS if f not in rec]
        if missing:
            raise ProviderError(
                f"provider record missing field(s) {missing}: {rec!r}"
            )
        out.append(
            Provider(
                id=str(rec["id"]),
                endpoint=str(rec["endpoint"]).rstrip("/"),
                model=str(rec["model"]),
                api_key_env=rec.get("api_key_env"),
                runtime=rec.get("runtime", ""),
            )
        )
    return out


def save_providers(providers: list[Provider], config: Config | None = None) -> None:
    config = config or Config.load()
    config.user_dir.mkdir(parents=True, exist_ok=True)
    path = providers_path(config)
    path.write_text(
        json.dumps([p.to_dict() for p in providers], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def get_provider(provider_id: str, config: Config | None = None) -> Provider | None:
    return next((p for p in load_providers(config) if p.id == provider_id), None)


def add_provider(provider: Provider, config: Config | None = None) -> None:
    providers = load_providers(config)
    if any(p.id == provider.id for p in providers):
        raise ProviderError(f"provider id {provider.id!r} already exists "
                            "(remove it first or pick another id)")
    if provider.api_key_env and provider.api_key_env not in os.environ:
        raise ProviderError(
            f"api_key_env {provider.api_key_env!r} is not set in the environment — "
            "export it first (keys are never stored inline)"
        )
    providers.append(provider)
    save_providers(providers, config)


def remove_provider(provider_id: str, config: Config | None = None) -> None:
    providers = load_providers(config)
    remaining = [p for p in providers if p.id != provider_id]
    if len(remaining) == len(providers):
        raise ProviderError(f"no provider with id {provider_id!r}")
    # refuse removal while a role still points at it
    bindings = load_bindings(config)
    for role, pid in bindings.items():
        if pid == provider_id:
            raise ProviderError(
                f"provider {provider_id!r} is bound to role {role!r} — "
                f"rebind with `moguru model set {role} <other>` first"
            )
    save_providers(remaining, config)


def detect_runtime(endpoint: str) -> str:
    """Best-effort local-runtime detection so naming/hints follow whatever is
    actually running — the provider abstraction itself stays agnostic."""
    host = endpoint.split("//")[-1].split("/")[0]
    try:
        import requests as _r

        if host.endswith(":11434") and _r.get(
            f"{endpoint.rstrip('/').rsplit('/v1', 1)[0]}/api/tags", timeout=2
        ).ok:
            return "ollama"
    except Exception:
        pass
    if host.endswith(":1234"):
        return "lmstudio"
    if host.startswith("localhost") or host.startswith("127.0.0.1"):
        return "local"
    return "hosted"


def seed_local_provider(config: Config | None = None) -> Provider | None:
    """Auto-register the configured local endpoint as a provider on first
    use, named for the runtime actually detected (agnostic)."""
    config = config or Config.load()
    runtime = detect_runtime(config.local.endpoint)
    provider_id = f"local-{runtime}"
    if get_provider(provider_id, config):
        return None
    if not config.local.endpoint or not config.local.name:
        return None
    provider = Provider(
        id=provider_id,
        endpoint=config.local.endpoint,
        model=config.local.name,
        runtime=runtime,
    )
    try:
        add_provider(provider, config)
    except ProviderError:
        return None
    return provider


# ---------------------------------------------------------------------------
# Role bindings — data/user/config.yaml  { model: { main, shadow } }
# ---------------------------------------------------------------------------

def bindings_path(config: Config | None = None) -> Path:
    config = config or Config.load()
    return config.user_dir / "config.yaml"


def load_bindings(config: Config | None = None) -> dict[str, str]:
    config = config or Config.load()
    path = bindings_path(config)
    if not path.exists():
        return {}
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError:
        return {}
    model = data.get("model") or {}
    return {r: model[r] for r in ROLES if model.get(r)}


def set_binding(role: str, provider_id: str, config: Config | None = None) -> None:
    if role not in ROLES:
        raise ProviderError(f"role must be one of {ROLES}, got {role!r}")
    config = config or Config.load()
    provider = get_provider(provider_id, config)
    if provider is None:
        raise ProviderError(
            f"no provider with id {provider_id!r} — add it first with "
            "`moguru provider add`"
        )
    config.user_dir.mkdir(parents=True, exist_ok=True)
    path = bindings_path(config)
    data: dict[str, Any] = {}
    if path.exists():
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError:
            data = {}
    data.setdefault("model", {})[role] = provider_id
    path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=True),
                    encoding="utf-8")


def resolve_role(role: str, config: Config | None = None) -> tuple[Provider, dict[str, str]]:
    """Role → (provider, headers). Falls back to the repo config's local
    endpoint when no binding exists (backward compatible)."""
    if role not in ROLES:
        raise ProviderError(f"role must be one of {ROLES}, got {role!r}")
    config = config or Config.load()
    bindings = load_bindings(config)
    headers: dict[str, str] = {}
    if bindings.get(role):
        provider = get_provider(bindings[role], config)
        if provider is None:
            raise ProviderError(
                f"role {role!r} is bound to missing provider "
                f"{bindings[role]!r} — `moguru model set {role} <id>` again"
            )
    elif role == "main":
        from moguru.config import ModelConfig

        local: ModelConfig = config.local
        provider = Provider(id="local-ollama", endpoint=local.endpoint,
                            model=local.name, runtime="ollama")
    else:  # shadow unbound: use configured shadow endpoint (Phase 3 default)
        provider = Provider(id="local-shadow", endpoint=config.shadow.endpoint,
                            model=config.shadow.name, runtime="ollama")
    if provider.api_key_env:
        key = os.environ.get(provider.api_key_env)
        if not key:
            raise ProviderError(
                f"{provider.id}: env var {provider.api_key_env} is not set — "
                "export it and retry"
            )
        headers["Authorization"] = f"Bearer {key}"
    return provider, headers


# ---------------------------------------------------------------------------
# §5 validation — live check before saving
# ---------------------------------------------------------------------------

def _list_models(endpoint: str, headers: dict[str, str],
                 timeout: float = 10.0) -> list[str]:
    resp = requests.get(f"{endpoint}/models", headers=headers, timeout=timeout)
    resp.raise_for_status()
    data = resp.json().get("data", [])
    return [m.get("id", "") for m in data]


def validate_provider(provider: Provider, pull: bool = False,
                      timeout: float = 60.0) -> dict[str, Any]:
    """Live-check a provider: endpoint reachable + OpenAI schema, model
    exists (offer to pull if local + missing), key env set, tiny Japanese
    round-trip. Returns a report; raises ProviderError on failure with the
    specific, actionable reason."""
    headers: dict[str, str] = {}
    if provider.api_key_env:
        key = os.environ.get(provider.api_key_env)
        if not key:
            raise ProviderError(
                f"{provider.id}: api_key_env {provider.api_key_env!r} is not set"
            )
        headers["Authorization"] = f"Bearer {key}"

    # 1) endpoint reachable, OpenAI-compatible
    try:
        model_ids = _list_models(provider.endpoint, headers)
    except requests.ConnectionError:
        raise ProviderError(
            f"{provider.id}: cannot reach {provider.endpoint} — is the runtime "
            "running? (e.g. `ollama serve`, LM Studio → Developer → Start Server)"
        ) from None
    except (requests.HTTPError, ValueError) as e:
        raise ProviderError(
            f"{provider.id}: {provider.endpoint} did not answer an OpenAI-compatible "
            f"GET /models ({e})"
        ) from None

    # 2) the named model exists there (pull, with consent, if local+missing)
    runtime = detect_runtime(provider.endpoint)
    if model_ids and provider.model not in model_ids:
        if pull and runtime == "ollama":
            import subprocess

            subprocess.run(["ollama", "pull", provider.model], check=False)
            model_ids = _list_models(provider.endpoint, headers)
        if provider.model not in model_ids:
            hints = {
                "ollama": f" — run `ollama pull {provider.model}`?",
                "lmstudio": " — load the model in LM Studio and (re)start the server",
            }
            hint = hints.get(runtime, "")
            raise ProviderError(
                f"{provider.id}: model {provider.model!r} not found at "
                f"{provider.endpoint}{hint}"
            )

    # 3) tiny Japanese round-trip returns sane output (thinking models may
    #    spend early tokens on hidden reasoning — give headroom + strip it)
    t0 = time.monotonic()
    try:
        resp = requests.post(
            f"{provider.endpoint}/chat/completions",
            headers=headers,
            json={
                "model": provider.model,
                "messages": [
                    {"role": "user",
                     "content": "「OK」とだけ日本語で返してください。思考は不要です。"}
                ],
                "max_tokens": 512,
            },
            timeout=timeout,
        )
        resp.raise_for_status()
    except requests.RequestException as e:
        raise ProviderError(
            f"{provider.id}: chat round-trip failed at {provider.endpoint}: {e}"
        ) from None
    latency_ms = (time.monotonic() - t0) * 1000
    try:
        message = resp.json()["choices"][0]["message"]
        content = (message.get("content") or "").strip()
        if not content:  # thinking-only turn: re-ask without a token ceiling
            resp = requests.post(
                f"{provider.endpoint}/chat/completions",
                headers=headers,
                json={
                    "model": provider.model,
                    "messages": [
                        {"role": "user", "content": "「OK」とだけ日本語で返してください。"}
                    ],
                },
                timeout=timeout,
            )
            resp.raise_for_status()
            message = resp.json()["choices"][0]["message"]
            content = (message.get("content") or "").strip()
    except (KeyError, IndexError, ValueError):
        content = ""
    # strip <think> blocks some local models emit inline
    if "</think>" in content:
        content = content.split("</think>")[-1].strip()
    if not content:
        raise ProviderError(
            f"{provider.id}: round-trip returned empty content — model "
            f"{provider.model!r} may not be loaded"
        )
    return {
        "ok": True,
        "provider": provider.id,
        "endpoint": provider.endpoint,
        "model": provider.model,
        "local": provider.is_local,
        "latency_ms": round(latency_ms),
        "sample": content.strip()[:40],
    }


# ---------------------------------------------------------------------------
# Client plumbing used by ModelRouter
# ---------------------------------------------------------------------------

class ProviderClient:
    """OpenAI-compatible chat client bound to one provider."""

    def __init__(self, provider: Provider, headers: dict[str, str] | None = None,
                 timeout: float = 600.0):
        self.provider = provider
        self.endpoint = provider.endpoint.rstrip("/")
        self.model = provider.model
        self.headers = headers or {}
        self.timeout = timeout

    def chat(self, messages: list[dict], tools: list[dict] | None = None,
             max_tokens: int | None = None,
             extra_body: dict | None = None) -> dict:
        body: dict[str, Any] = {"model": self.model, "messages": messages}
        if tools:
            body["tools"] = tools
        if max_tokens:
            body["max_tokens"] = max_tokens
        # extra runtime-specific keys (e.g. chat_template_kwargs for local
        # llama.cpp servers) — LOCAL ONLY: hosted OpenAI-compatible APIs
        # reject unknown body keys with a 400
        if extra_body and self.provider.is_local:
            body.update(extra_body)
        resp = requests.post(
            f"{self.endpoint}/chat/completions",
            headers=self.headers,
            json=body,
            timeout=self.timeout,
        )
        resp.raise_for_status()
        return resp.json()
