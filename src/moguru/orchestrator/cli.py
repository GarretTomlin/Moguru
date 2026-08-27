"""moguru CLI — the Phase 1 chat-driven interface plus direct verbs.

The direct verbs (lookup/mine/assess/stats/review) execute the same
deterministic pipelines the model drives through MCP tools; they also match
the future Phase 2 service boundary (spec §8.1).
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from moguru import config as cfgmod
from moguru.config import Config


def _load_config(args: argparse.Namespace) -> Config:
    return Config.load(getattr(args, "config", None))


def cmd_data(args: argparse.Namespace) -> None:
    from moguru.orchestrator import data_pipeline

    if args.action == "download":
        data_pipeline.download(_load_config(args))
    elif args.action == "build":
        data_pipeline.build(_load_config(args))
    elif args.action == "build-monolingual":
        data_pipeline.build_monolingual(_load_config(args))
    else:
        data_pipeline.download(_load_config(args))
        data_pipeline.build(_load_config(args))


def cmd_lookup(args: argparse.Namespace) -> None:
    from moguru.mcp.dict_mcp import core as dict_core
    from moguru.mcp.parser_mcp import core as parser_core

    config = _load_config(args)
    text = args.text
    print("== tokens ==")
    for t in parser_core.tokenize(text, config):
        accent = t.get("pitch_accent", "")
        print(
            f"  {t['surface']:<12} lemma={t['lemma']:<10} reading={t['reading_kana']:<12} "
            f"{t['pos']}/{t['pos_detail']}{' pitch=' + str(accent) if accent != '' else ''}"
        )
    print("== deinflection (last token) ==")
    toks = parser_core.tokenize(text, config)
    if toks:
        for cand in parser_core.deinflect(toks[-1]["surface"], config):
            print(f"  {toks[-1]['surface']} -> {cand['lemma']} ({cand['via']})")
    print("== dictionary ==")
    for t in toks:
        if not t["lemma"]:
            continue
        entries = dict_core.lookup_word(t["lemma"])
        for e in entries[:1]:
            head = "/".join(e["headwords"] or e["readings"])
            gloss = "; ".join(g for s in e["senses"][:3] for g in s["gloss"][:2])
            print(f"  {head} [{','.join(e['readings'][:2])}]: {gloss[:150]}")


def cmd_mine(args: argparse.Namespace) -> None:
    from moguru.orchestrator import mining

    config = _load_config(args)
    results = mining.mine_text(
        args.text,
        config,
        media_ref=args.media_ref,
        auto_add=args.add,
    )
    if not results:
        print("no i+1 candidates found "
              f"(threshold={config.iplus_threshold}, len={list(config.sentence_len)})")
        return
    for i, r in enumerate(results, 1):
        c = r["candidate"]
        line = f"{i}. {c['sentence']}"
        if c["target"]:
            line += f"  ⟵ target: {c['target']} (jpdb #{c['freq_rank']}, coverage {c['coverage']})"
        print(line)
        for k in ("Reading", "Definition", "PitchAccent"):
            if r["fields"].get(k):
                print(f"     {k}: {r['fields'][k][:120]}")
        if "note_id" in r:
            print(f"     → card #{r['note_id']} added")
    if not args.add:
        print("\n(dry run — pass --add to create cards)")


def cmd_assess(args: argparse.Namespace) -> None:
    from moguru.orchestrator import mining

    result = mining.assess_text(args.text, _load_config(args))
    print(json.dumps(result, ensure_ascii=False, indent=2))


def cmd_stats(args: argparse.Namespace) -> None:
    from moguru.mcp.kb_mcp import core as kb_core

    print(json.dumps(kb_core.stats(), ensure_ascii=False, indent=2))


def cmd_mark(args: argparse.Namespace) -> None:
    from moguru.mcp.kb_mcp import core as kb_core

    for lemma in args.lemmas:
        kb_core.mark_known(lemma, args.source)
        print(f"known: {lemma} (source={args.source})")


def cmd_import_anki(args: argparse.Namespace) -> None:
    from moguru.mcp.kb_mcp import core as kb_core
    from moguru.mcp.srs_mcp import core as srs_core

    config = _load_config(args)
    try:
        lemmas = srs_core.get_backend(config).import_known()
    except Exception as e:  # AnkiConnect down, etc.
        print(f"import failed: {e}", file=sys.stderr)
        sys.exit(1)
    added = 0
    for lemma in lemmas:
        kb_core.mark_known(lemma, "anki")
        added += 1
    print(f"imported {added} mature lemmas into the knowledge store")


def cmd_due(args: argparse.Namespace) -> None:
    from moguru.mcp.srs_mcp import core as srs_core

    config = _load_config(args)
    cards = srs_core.get_backend(config).due_cards(args.deck or config.anki_deck)
    if not cards:
        print("nothing due 🎉")
        return
    for c in cards:
        print(f"#{c['note_id']}  {c['fields'].get('Sentence', '')[:80]}")
        print(f"      target: {c['fields'].get('TargetWord', '')}")
    print(f"\n{len(cards)} due — review with `moguru review`")


def cmd_review(args: argparse.Namespace) -> None:
    from moguru.mcp.srs_mcp import core as srs_core

    config = _load_config(args)
    backend = srs_core.get_backend(config)
    if config.srs_backend != "builtin":
        print("interactive review only supported for the builtin backend "
              "(Anki owns its own scheduling)")
        return
    deck = args.deck or config.anki_deck
    while True:
        cards = backend.due_cards(deck)
        if not cards:
            print("nothing due 🎉")
            return
        c = cards[0]
        print(f"\n#{c['note_id']} {c['fields'].get('Sentence', '')}")
        input("(press Enter to reveal) ")
        print(f"  target:   {c['fields'].get('TargetWord', '')}")
        print(f"  reading:  {c['fields'].get('Reading', '')}")
        print(f"  meaning:  {c['fields'].get('Definition', '')[:200]}")
        print(f"  pitch:    {c['fields'].get('PitchAccent', '')}")
        while True:
            rating = input("rate [a]gain [h]ard [g]ood [e]asy / [s]kip / [q]uit: ").strip().lower()
            if rating in {"a", "again"}:
                r = "again"; break
            if rating in {"h", "hard"}:
                r = "hard"; break
            if rating in {"g", "good", ""}:
                r = "good"; break
            if rating in {"e", "easy"}:
                r = "easy"; break
            if rating in {"s", "skip"}:
                r = None; break
            if rating in {"q", "quit"}:
                return
        if r is None:
            continue
        outcome = backend.review_note(c["note_id"], r)
        print(f"  → next due {outcome['next_due'][:16]}  (stability {outcome['stability']})")


def cmd_adapt(args: argparse.Namespace) -> None:
    from moguru.orchestrator import adaptivity

    config = _load_config(args)
    if args.revert:
        print(json.dumps(adaptivity.revert(config), ensure_ascii=False, indent=2))
        return
    result = adaptivity.evaluate(config, apply=True)
    print(json.dumps(result, ensure_ascii=False, indent=2))


def cmd_chat(args: argparse.Namespace) -> None:
    from moguru.orchestrator.agent import run_chat_repl

    config = _load_config(args)
    _maybe_wizard(config)
    run_chat_repl(config)


# ---------------------------------------------------------------------------
# Models & Providers (runtime model swapping)
# ---------------------------------------------------------------------------

def cmd_provider(args: argparse.Namespace) -> None:
    from moguru.orchestrator import providers as pm

    config = _load_config(args)
    if args.action == "add":
        provider = pm.Provider(
            id=args.provider_id,
            endpoint=args.endpoint,
            model=args.model,
            api_key_env=args.api_key_env,
            runtime=args.runtime or "",
        )
        pm.add_provider(provider, config)
        print(f"added provider {provider.id!r} -> {provider.model} @ {provider.endpoint}")
    elif args.action == "remove":
        pm.remove_provider(args.provider_id, config)
        print(f"removed provider {args.provider_id!r}")
    elif args.action == "list":
        providers = pm.load_providers(config)
        if not providers:
            print("(no providers — `moguru provider add` or run the wizard)")
        for p in providers:
            key = f" key={p.api_key_env}" if p.api_key_env else ""
            local = "local" if p.is_local else "hosted"
            print(f"{p.id:<16} {p.model:<40} {p.endpoint}  [{local}{key}]")
    else:
        raise SystemExit("provider: add|remove|list required")


SHADOW_WARNING = (
    "⚠  The shadow role runs CONTINUOUSLY on your comprehension data — the most\n"
    "    personal data in the system. Pointing it at a hosted API sends that\n"
    "    stream off-machine every few seconds (cost + privacy).\n"
    "    Pass --i-know to confirm you want a hosted shadow model."
)


def cmd_model(args: argparse.Namespace) -> None:
    from moguru.orchestrator import providers as pm

    config = _load_config(args)
    if args.action == "set":
        provider = pm.get_provider(args.provider_id, config)
        if provider is None:
            raise SystemExit(
                f"error: no provider {args.provider_id!r} — "
                "`moguru provider add` first"
            )
        if args.role == "shadow" and not provider.is_local and not args.i_know:
            raise SystemExit(SHADOW_WARNING)
        print(f"validating {provider.id} ({provider.model} @ {provider.endpoint})…")
        report = pm.validate_provider(provider, pull=args.pull)
        pm.set_binding(args.role, args.provider_id, config)
        print(
            f"model.{args.role} = {args.provider_id}  "
            f"({report['latency_ms']} ms round-trip) — takes effect on next request"
        )
    elif args.action == "list":
        bindings = pm.load_bindings(config)
        providers = {p.id: p for p in pm.load_providers(config)}
        for role in pm.ROLES:
            pid = bindings.get(role)
            if pid and pid in providers:
                p = providers[pid]
                print(f"{role:<8} -> {pid:<16} {p.model} @ {p.endpoint}")
            elif role == "main":
                print(f"{role:<8} -> (config.yaml local: {config.local.name} @ {config.local.endpoint})")
            else:
                print(f"{role:<8} -> (unset; Phase 3 default {config.shadow.name} @ {config.shadow.endpoint})")
    elif args.action == "test":
        bindings = pm.load_bindings(config)
        pid = bindings.get(args.role or "main")
        if pid is None and (args.role or "main") == "main":
            pm.seed_local_provider(config)
            pid = "local-ollama"
        provider = pm.get_provider(pid, config) if pid else None
        if provider is None:
            raise SystemExit(f"error: role {args.role or 'main'} has no provider bound")
        try:
            report = pm.validate_provider(provider)
        except pm.ProviderError as e:
            raise SystemExit(f"FAIL: {e}") from None
        print(
            f"PASS {report['provider']} ({report['model']} @ {report['endpoint']}) "
            f"{report['latency_ms']} ms — sample: {report['sample']}"
        )
    elif args.action == "wizard":
        run_wizard(config)
    else:
        raise SystemExit("model: set|list|test|wizard required")


def run_wizard(config) -> None:
    """First-run wizard (§4): detect what's there, ask two questions."""
    import os

    from moguru.orchestrator import providers as pm

    print("Moguru model wizard — two questions.\n")
    pm.seed_local_provider(config)
    candidates: dict[str, object] = {}

    ollama_up = False
    try:
        import requests

        requests.get("http://localhost:11434/api/tags", timeout=2)
        ollama_up = True
        local = pm.get_provider("local-ollama", config)
        if local:
            candidates["local-ollama"] = local
    except requests.RequestException:
        pass
    if os.environ.get("ANTHROPIC_API_KEY") and not ollama_up:
        print("(detected ANTHROPIC_API_KEY — but no local provider available, "
              "add it manually with `moguru provider add`)")

    ids = list(candidates)
    if not ids:
        print("No model runtime detected (no Ollama on :11434).")
        print("Start one (e.g. `ollama serve` + `ollama pull <model>`), or add a")
        print("hosted provider with `moguru provider add` and re-run this wizard.")
        return

    # 1) main
    print(f"1. Main model — orchestrator brain. Detected: {ids}")
    choice = input(f"   Use {ids[0]!r} as main? [Y/n] ").strip().lower()
    if choice in {"", "y", "yes"}:
        pm.set_binding("main", ids[0], config)
        print(f"   main = {ids[0]}")
    else:
        print("   Run `moguru model set main <provider-id>` after adding one.")

    # 2) shadow — defaults local, warn if hosted insisted
    print("2. Shadow model — continuous comprehension inference (Phase 3).")
    print("   Design rule: keep it LOCAL — it runs on your most personal data.")
    choice = input(f"   Use local {ids[0]!r} as shadow? [Y/n] ").strip().lower()
    if choice in {"", "y", "yes"}:
        pm.set_binding("shadow", ids[0], config)
        print(f"   shadow = {ids[0]}")
    else:
        print("   Add a small local provider (8–12B) and `moguru model set shadow <id>`.")
        print("   A hosted shadow requires `--i-know` and is discouraged.")
    print("\nDone. `moguru model list` to inspect, `moguru model test main` to verify.")


def _maybe_wizard(config) -> None:
    """First launch with no bindings and no providers → offer the wizard."""
    from moguru.orchestrator import providers as pm

    if pm.load_bindings(config) or pm.load_providers(config):
        return
    import sys

    if not sys.stdin.isatty():
        pm.seed_local_provider(config)
        return
    answer = input(
        "\nNo models configured. Run the first-run wizard now? [Y/n] "
    ).strip().lower()
    if answer in {"", "y", "yes"}:
        run_wizard(config)
    else:
        pm.seed_local_provider(config)


def cmd_plugins(args: argparse.Namespace) -> None:
    from moguru.orchestrator import registry

    plugins, warnings = registry.scan()
    for w in warnings:
        print(f"⚠ {w}", file=sys.stderr)
    if not plugins:
        print("(no plugins mounted)")
    for p in plugins:
        print(f"{p.name} v{p.version} [{p.type}] entry={p.entry} "
              f"ground_truth={p.provides_ground_truth} tools={[t['name'] for t in p.tools]}")


# ---------------------------------------------------------------------------
# Install & orchestration: bundle / doctor / update / uninstall
# ---------------------------------------------------------------------------

def cmd_bundle(args: argparse.Namespace) -> None:
    import json as _json

    from moguru.orchestrator import bundle as bm

    try:
        manifest = bm.load_manifest()
    except bm.BundleError as e:
        raise SystemExit(f"error: {e}") from None
    if args.action == "print":
        block = bm.mcp_servers_block(manifest)
        print(_json.dumps({"mcpServers": block}, ensure_ascii=False, indent=2))
    elif args.action == "install":
        path = bm.install_into_host(args.host, manifest, create=args.create)
        print(f"merged {len(manifest['servers'])} servers into {path} "
              "(existing entries preserved; backup written)")
    elif args.action == "uninstall":
        path = bm.uninstall_from_host(args.host)
        print(f"removed moguru servers from {path}")


def cmd_doctor(args: argparse.Namespace) -> None:
    from moguru.orchestrator import doctor

    config = _load_config(args)
    raise SystemExit(
        doctor.run_doctor(config, fix=args.fix, skip_servers=args.no_servers)
    )


def cmd_update(args: argparse.Namespace) -> None:
    from moguru.orchestrator import data_pipeline

    config = _load_config(args)
    print("updating data (idempotent)…")
    data_pipeline.download(config)
    data_pipeline.build(config)
    print("done — dictionaries are current")


def cmd_uninstall(args: argparse.Namespace) -> None:
    import shutil

    from moguru.config import REPO_ROOT
    from moguru.orchestrator import bundle as bm

    config = _load_config(args)
    for host in ("claude-desktop", "cursor", "openclaw"):
        try:
            path = bm.uninstall_from_host(host)
            if path.exists():
                print(f"unregistered from {host}")
        except bm.BundleError:
            pass
    if args.purge:
        shutil.rmtree(config.user_dir, ignore_errors=True)
        print(f"purged {config.user_dir} (kb + progress — gone)")
    else:
        print(f"kept {config.user_dir} (your kb + progress; "
              "pass --purge to remove it too)")
    if args.remove_data:
        shutil.rmtree(config.dictionaries_dir, ignore_errors=True)
        print("removed bundled dictionaries")
    print("uninstall complete")


def cmd_rtk(args: argparse.Namespace) -> None:
    from moguru.mcp.dict_mcp import core as dict_core
    from moguru.mcp.kb_mcp import core as kb_core

    char = args.kanji
    if len(char) != 1:
        print("pass exactly one kanji, e.g. `moguru rtk 食`")
        return
    entry = dict_core.lookup_kanji(char)
    if entry is None:
        print(f"no KANJIDIC2 entry for {char}")
        return
    known = set(kb_core.known_kanji())
    print(f"{char}: {'/'.join(entry['meanings'][:4])} "
          f"(strokes {entry['stroke_count']}, grade {entry['grade']}, "
          f"freq #{entry['freq_rank']})")
    print(f"  on: {'、'.join(entry['on_readings'][:5])}")
    print(f"  kun: {'、'.join(entry['kun_readings'][:5])}")
    comps = dict_core.decompose_kanji(char)
    for c in comps:
        mark = "✓known" if c["component"] in known else "✗ new"
        meaning = f" ({c['meaning']})" if c.get("meaning") else ""
        print(f"  · {c['component']}{meaning} — {mark}")


def cmd_shadow(args: argparse.Namespace) -> None:
    import json as _json

    from moguru.mcp.shadow_mcp import core as shadow_core

    config = _load_config(args)
    if args.action == "record":
        result = shadow_core.record_signal({
            "type": args.type, "key": args.key,
            "key_kind": args.key_kind, "sentence": args.sentence,
            "modality": args.modality, "media_ref": args.media_ref,
        }, config)
        print(_json.dumps(result, ensure_ascii=False))
    elif args.action == "interpret":
        print(_json.dumps(shadow_core.interpret_pending(config=config),
                          ensure_ascii=False))
    elif args.action == "gaps":
        for g in shadow_core.gaps({"modality": args.modality}, config):
            print(f"  {g['key']:<12} {g['modality']:<9} p={g['p_understood']:<7} "
                  f"delta={g['delta']}")
    elif args.action == "map":
        print(_json.dumps(shadow_core.comprehension_map(config=config),
                          ensure_ascii=False, indent=2))
    elif args.action == "explain":
        out = shadow_core.explain_estimate(args.key, args.modality,
                                           args.key_kind, config)
        print(out["reasoning"])
        for e in out["evidence"]:
            print(f"  {e['ts'][:19]}  {e['type']:<8} w={e['weight']}")
    elif args.action == "calibration":
        print(_json.dumps(shadow_core.calibration(config), ensure_ascii=False,
                          indent=2))
    else:
        raise SystemExit("shadow: record|interpret|gaps|map|explain|calibration")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="moguru",
        description="潜る Moguru — a local, model-driven Japanese immersion engine",
    )
    p.add_argument("--config", default=None, help="path to config.yaml")
    sub = p.add_subparsers(dest="command", required=True)

    sp = sub.add_parser("chat", help="interactive model-driven session")
    sp.set_defaults(func=cmd_chat)

    # --- models & providers -------------------------------------------
    sp = sub.add_parser("provider", help="manage model providers")
    sp.add_argument("action", choices=["add", "remove", "list"])
    sp.add_argument("provider_id", nargs="?", help="provider id")
    sp.add_argument("--endpoint", help="OpenAI-compatible base URL")
    sp.add_argument("--model", help="model name at the endpoint")
    sp.add_argument("--api-key-env", default=None,
                    help="env var holding the API key (hosted only)")
    sp.add_argument("--runtime", default=None,
                    help="ollama | lmstudio | gemini | openai | openrouter "
                         "(hosted runtimes fill the endpoint + key name)")
    sp.set_defaults(func=cmd_provider)

    sp = sub.add_parser("model", help="bind/test model roles (main | shadow)")
    sp.add_argument("action", choices=["set", "list", "test", "wizard"])
    sp.add_argument("role", nargs="?", default=None, choices=["main", "shadow"])
    sp.add_argument("provider_id", nargs="?", help="provider id for `set`")
    sp.add_argument("--pull", action="store_true",
                    help="consent: pull the model if local and missing")
    sp.add_argument("--i-know", action="store_true",
                    help="confirm a hosted shadow model despite the privacy warning")
    sp.set_defaults(func=cmd_model)

    sp = sub.add_parser("lookup", help="tokenize + look up a sentence")
    sp.add_argument("text")
    sp.set_defaults(func=cmd_lookup)

    sp = sub.add_parser("mine", help="find i+1 candidates / create cards")
    sp.add_argument("text")
    sp.add_argument("--add", action="store_true", help="create SRS cards")
    sp.add_argument("--media-ref", default=None)
    sp.set_defaults(func=cmd_mine)

    sp = sub.add_parser("assess", help="comprehensibility verdict for a passage")
    sp.add_argument("text")
    sp.set_defaults(func=cmd_assess)

    sub.add_parser("stats", help="knowledge-store stats").set_defaults(func=cmd_stats)

    sp = sub.add_parser("mark", help="mark lemmas known")
    sp.add_argument("lemmas", nargs="+")
    sp.add_argument("--source", default="manual", choices=["manual", "anki", "mined"])
    sp.set_defaults(func=cmd_mark)

    sp = sub.add_parser("import-anki", help="seed kb from mature SRS/Anki cards")
    sp.set_defaults(func=cmd_import_anki)

    sp = sub.add_parser("due", help="list due cards")
    sp.add_argument("--deck", default=None)
    sp.set_defaults(func=cmd_due)

    sp = sub.add_parser("review", help="interactive review (builtin FSRS)")
    sp.add_argument("--deck", default=None)
    sp.set_defaults(func=cmd_review)

    sp = sub.add_parser("adapt", help="run Phase A adaptivity now")
    sp.add_argument("--revert", action="store_true")
    sp.set_defaults(func=cmd_adapt)

    sp = sub.add_parser("plugins", help="list mounted plugins")
    sp.set_defaults(func=cmd_plugins)

    sp = sub.add_parser("rtk", help="RTK decomposition for one kanji")
    sp.add_argument("kanji")
    sp.set_defaults(func=cmd_rtk)

    sp = sub.add_parser("serve", help="run the engine HTTP service (surfaces)")
    sp.add_argument("--port", type=int, default=8766)
    sp.set_defaults(func=lambda args: __import__(
        "moguru.orchestrator.service", fromlist=["run_server"]
    ).run_server(args.port))

    sp = sub.add_parser("shadow", help="comprehension shadow model (Phase 3)")
    sp.add_argument("action", choices=["record", "interpret", "gaps", "map",
                                       "explain", "calibration"])
    sp.add_argument("--type", default="lookup",
                    choices=["hover", "pause", "rewind", "replay", "lookup",
                             "mine", "skip", "complete"])
    sp.add_argument("--key", default=None)
    sp.add_argument("--key-kind", default="vocab", choices=["vocab", "grammar"])
    sp.add_argument("--sentence", default="")
    sp.add_argument("--modality", default="reading", choices=["reading", "listening"])
    sp.add_argument("--media-ref", default=None)
    sp.set_defaults(func=cmd_shadow)

    sp = sub.add_parser("data", help="download / build dictionary data")
    sp.add_argument("action", nargs="?", default="all",
                    choices=["download", "build", "build-monolingual", "all"])
    sp.set_defaults(func=cmd_data)

    sp = sub.add_parser("bundle", help="install manifest into MCP hosts")
    sp.add_argument("action", choices=["print", "install", "uninstall"])
    sp.add_argument("--host", default=None,
                    choices=["claude-desktop", "cursor", "openclaw"])
    sp.add_argument("--create", action="store_true",
                    help="create the host config dir if missing")
    sp.set_defaults(func=cmd_bundle)

    sp = sub.add_parser("doctor", help="verify the whole install")
    sp.add_argument("--fix", action="store_true",
                    help="re-run failed data steps")
    sp.add_argument("--no-servers", action="store_true",
                    help="skip slow MCP server probes")
    sp.set_defaults(func=cmd_doctor)

    sub.add_parser("update", help="refresh dictionary data (idempotent)")\
        .set_defaults(func=cmd_update)

    sp = sub.add_parser("uninstall", help="unregister from hosts, remove data")
    sp.add_argument("--purge", action="store_true",
                    help="also delete data/user (kb + progress)")
    sp.add_argument("--remove-data", action="store_true",
                    help="also delete bundled dictionaries")
    sp.set_defaults(func=cmd_uninstall)

    return p


def main(argv: list[str] | None = None) -> Any:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    main()
