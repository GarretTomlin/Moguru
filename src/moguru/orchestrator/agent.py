"""Orchestrator agent loop (spec §2).

Model + agent loop + skills. The model is swappable behind an
OpenAI-compatible endpoint (principle 0.4); tools are the MCP servers
(parser/dict/freq/kb/srs/media) plus plugins; skills are SKILL.md workflows
exposed through a `load_skill` meta-tool.

Run: moguru chat
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any

import requests

from moguru.config import Config, REPO_ROOT
from moguru.orchestrator import registry, skills as skills_mod

CORE_SERVERS = [
    "moguru.mcp.parser_mcp.server",
    "moguru.mcp.dict_mcp.server",
    "moguru.mcp.kb_mcp.server",
    "moguru.mcp.srs_mcp.server",
    "moguru.mcp.freq_mcp.server",
    "moguru.mcp.media_mcp.server",
    "moguru.mcp.shadow_mcp.server",
]

SYSTEM_PROMPT_TEMPLATE = """\
You are Moguru (潜る), a local Japanese immersion assistant built on the AJATT / \
MIA / Migaku philosophy. You orchestrate deterministic tools and follow skills.

## Non-negotiable rules
1. FACTS IN TOOLS, JUDGMENT IN YOU. Readings, definitions, frequencies, and \
"does the user know this word" are ALWAYS resolved by tool calls against real \
data. NEVER recite dictionary content from your own weights. A tool call \
returning ground truth is REQUIRED before you show any reading, definition, \
or frequency to the learner. Teaching a wrong reading is worse than no answer.
2. Adaptivity: "i+1" is relative to what this learner knows — always check \
kb tools (is_known / stats) before judging difficulty.
3. One target word per sentence card; rank candidates frequent-first.
4. If a tool errors or returns nothing, say so plainly. Never fabricate data.

## Current configuration
- defs mode: {defs_mode} | i+1 threshold: {iplus_threshold} | sentence len: {sentence_len}
- srs backend: {srs_backend} | deck: {deck}
- knowledge store: {kb_stats}

## Skills (procedures you follow)
{skill_index}

Load a skill's full procedure with the `load_skill` tool when its task comes \
up — then follow it exactly.
"""


class OpenAICompatClient:
    """Minimal OpenAI-compatible chat client (Ollama / LM Studio / llama.cpp)."""

    def __init__(self, endpoint: str, model: str, timeout: float = 600.0):
        self.endpoint = endpoint.rstrip("/")
        self.model = model
        self.timeout = timeout

    def chat(self, messages: list[dict], tools: list[dict] | None = None,
             max_tokens: int | None = None) -> dict:
        body: dict[str, Any] = {"model": self.model, "messages": messages}
        if tools:
            body["tools"] = tools
        if max_tokens:
            body["max_tokens"] = max_tokens
        resp = requests.post(
            f"{self.endpoint}/chat/completions", json=body, timeout=self.timeout
        )
        resp.raise_for_status()
        return resp.json()


class ModelRouter:
    """Role-resolved model routing (Models & Providers spec).

    The `main` role resolves through data/user/providers.json bindings and
    falls back to the repo config's local endpoint. `model.routing` governs
    fallback: local_only (main only) | local_first (main, then strong) |
    strong_only (strong if configured, else main). Changes take effect on
    the next request — no restart.
    """

    def __init__(self, config: Config):
        from moguru.orchestrator import providers as providers_mod

        self.config = config
        self.clients: list[OpenAICompatClient | providers_mod.ProviderClient] = []
        try:
            provider, headers = providers_mod.resolve_role("main", config)
            main_client = providers_mod.ProviderClient(provider, headers)
        except providers_mod.ProviderError as e:
            print(f"[models] {e} — falling back to config.yaml local endpoint",
                  file=sys.stderr)
            main_client = OpenAICompatClient(config.local.endpoint, config.local.name)
        if config.routing == "strong_only" and config.strong:
            self.clients.append(
                OpenAICompatClient(config.strong.endpoint, config.strong.name)
            )
            self.clients.append(main_client)
        else:
            self.clients.append(main_client)
            if config.routing == "local_first" and config.strong:
                self.clients.append(
                    OpenAICompatClient(config.strong.endpoint, config.strong.name)
                )

    def chat(self, messages: list[dict], tools: list[dict] | None = None,
             max_tokens: int | None = None) -> dict:
        last_error: Exception | None = None
        for client in self.clients:
            try:
                return client.chat(messages, tools, max_tokens=max_tokens)
            except requests.RequestException as e:
                last_error = e
                continue
        raise RuntimeError(f"no model endpoint reachable: {last_error}")


class MountedServer:
    """One MCP server subprocess + session."""

    def __init__(self, session, name: str):
        self.session = session
        self.name = name


class Engine:
    """The whole engine: mounted MCP tools + skills + model loop."""

    def __init__(self, config: Config | None = None):
        self.config = config or Config.load()
        self.skills = skills_mod.load_skills()

    def system_prompt(self) -> str:
        try:
            from moguru.mcp.kb_mcp import core as kb_core

            kb_stats = str(kb_core.stats())
        except Exception:
            kb_stats = "(unavailable)"
        index = "\n".join(
            f"- {s.name}: {s.description}" for s in self.skills.values()
        ) or "(none)"
        return SYSTEM_PROMPT_TEMPLATE.format(
            defs_mode=self.config.defs_mode,
            iplus_threshold=self.config.iplus_threshold,
            sentence_len=list(self.config.sentence_len),
            srs_backend=self.config.srs_backend,
            deck=self.config.anki_deck,
            kb_stats=kb_stats,
            skill_index=index,
        )

    # -- MCP mounting ------------------------------------------------------

    def _server_commands(self) -> list[tuple[str, list[str]]]:
        """(unique-name, argv-tail) — all servers run as `sys.executable <tail>`."""
        cmds = [(f"core:{m.split('.')[-2]}", ["-m", m]) for m in CORE_SERVERS]
        plugins, warnings = registry.scan()
        for w in warnings:
            print(f"[registry] {w}", file=sys.stderr)
        for p in plugins:
            if p.type == "mcp":
                cmds.append((f"plugin:{p.name}", [str(p.path / p.entry)]))
        return cmds

    async def _mount_all(self):
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client

        self._contexts: list[Any] = []
        self._sessions: dict[str, ClientSession] = {}
        env = {**os.environ, "MOGURU_CONFIG": str(default_config_path())}

        for name, args in self._server_commands():
            params = StdioServerParameters(command=sys.executable, args=args, env=env)
            ctx = stdio_client(params)
            read, write = await ctx.__aenter__()
            self._contexts.append(ctx)
            session_ctx = ClientSession(read, write)
            session = await session_ctx.__aenter__()
            self._contexts.append(session_ctx)
            await session.initialize()
            self._sessions[name] = session

    async def _unmount_all(self):
        for ctx in reversed(getattr(self, "_contexts", [])):
            try:
                await ctx.__aexit__(None, None, None)
            except Exception:
                pass
        self._contexts = []
        self._sessions = {}

    async def _collect_tools(self) -> tuple[list[dict], dict[str, str]]:
        """Aggregate tool schemas; returns (openai_tools, name->server)."""
        openai_tools: list[dict] = []
        owner: dict[str, str] = {}
        seen: set[str] = set()
        for server_name, session in self._sessions.items():
            result = await session.list_tools()
            for t in result.tools:
                if t.name in seen:
                    print(
                        f"[registry] duplicate tool {t.name!r} from {server_name} "
                        "ignored (first mount wins)",
                        file=sys.stderr,
                    )
                    continue
                seen.add(t.name)
                owner[t.name] = server_name
                schema = t.inputSchema or {"type": "object", "properties": {}}
                openai_tools.append(
                    {
                        "type": "function",
                        "function": {
                            "name": t.name,
                            "description": t.description or "",
                            "parameters": schema,
                        },
                    }
                )
        # Meta-tool: load a skill body on demand.
        skill_names = list(self.skills)
        openai_tools.append(
            {
                "type": "function",
                "function": {
                    "name": "load_skill",
                    "description": (
                        "Load the full SKILL.md procedure for a skill by name. "
                        f"Available: {', '.join(skill_names)}"
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "name": {
                                "type": "string",
                                "enum": skill_names,
                            }
                        },
                        "required": ["name"],
                    },
                },
            }
        )
        owner["load_skill"] = "<orchestrator>"
        return openai_tools, owner

    async def _call_tool(self, name: str, args: dict[str, Any]) -> str:
        if name == "load_skill":
            skill = self.skills.get(args.get("name", ""))
            if skill is None:
                return json.dumps({"error": f"no such skill: {args.get('name')}"})
            return json.dumps(
                {"name": skill.name, "description": skill.description,
                 "procedure": skill.body},
                ensure_ascii=False,
            )
        server_name = self._tool_owner.get(name)
        if server_name is None:
            return json.dumps({"error": f"unknown tool: {name}"})
        result = await self._sessions[server_name].call_tool(name, args)
        parts = [
            c.text if hasattr(c, "text") else str(c)
            for c in (result.content or [])
        ]
        payload = "\n".join(parts)
        if getattr(result, "isError", False):
            return json.dumps({"error": payload}, ensure_ascii=False)
        return payload

    # -- Agent loop ----------------------------------------------------------

    async def chat(self, user_message: str, history: list[dict] | None = None,
                   max_turns: int = 12) -> str:
        """One user turn through the tool-calling loop; returns final text."""
        router = ModelRouter(self.config)
        messages = history or []
        messages.append({"role": "user", "content": user_message})
        final_text = ""
        for _turn in range(max_turns):
            response = router.chat(messages, self._openai_tools)
            choice = response["choices"][0]["message"]
            if choice.get("tool_calls"):
                messages.append(choice)
                for tc in choice["tool_calls"]:
                    fn = tc["function"]
                    try:
                        args = json.loads(fn.get("arguments") or "{}")
                    except json.JSONDecodeError:
                        args = {}
                    print(f"  ⚙ {fn['name']}({json.dumps(args, ensure_ascii=False)[:120]})")
                    output = await self._call_tool(fn["name"], args)
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": tc.get("id", ""),
                            "content": output,
                        }
                    )
                continue
            final_text = (choice.get("content") or "").strip()
            if not final_text:
                # some models emit an empty turn after tool results — nudge
                messages.append({"role": "assistant", "content": ""})
                messages.append(
                    {
                        "role": "user",
                        "content": "（続けてください — ツール結果をもとに日本語で答えてください。）",
                    }
                )
                continue
            messages.append({"role": "assistant", "content": final_text})
            break
        else:
            final_text = "(reached tool-call turn limit)"
        self.last_messages = messages
        return final_text

    # -- Lifecycle ------------------------------------------------------------

    async def __aenter__(self) -> "Engine":
        await self._mount_all()
        self._openai_tools, self._tool_owner = await self._collect_tools()
        return self

    async def __aexit__(self, *exc) -> None:
        await self._unmount_all()


def default_config_path() -> Path:
    return REPO_ROOT / "config.yaml"


def run_chat_repl(config: Config | None = None) -> None:
    """`moguru chat` — the Phase 1 chat-driven interface."""
    config = config or Config.load()

    async def _run() -> None:
        async with Engine(config) as engine:
            print(f"潜る moguru — chat (model: {config.local.name} @ {config.local.endpoint})")
            print("Type your Japanese text / questions. Ctrl-D to exit.\n")
            history: list[dict] = [
                {"role": "system", "content": engine.system_prompt()}
            ]
            while True:
                try:
                    user = input("あなた> ").strip()
                except (EOFError, KeyboardInterrupt):
                    print()
                    break
                if not user:
                    continue
                if user in {"exit", "quit", ":q"}:
                    break
                try:
                    answer = await engine.chat(user, history)
                except RuntimeError as e:
                    print(f"[model error] {e}")
                    continue
                print(f"\nmoguru> {answer}\n")

    asyncio.run(_run())
