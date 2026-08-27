"""`moguru doctor` — verify the whole install, one PASS/FAIL line per check.

Non-zero exit on any failure so an agent can branch. `--fix` re-runs the
specific failed steps (re-download/rebuild data) rather than the whole install.
"""

from __future__ import annotations

import asyncio
import os
import sys
from typing import Any

from moguru.config import Config, REPO_ROOT


class Report:
    def __init__(self) -> None:
        self.lines: list[tuple[str, str, str]] = []  # (status, name, detail)

    def pass_(self, name: str, detail: str = "") -> None:
        self.lines.append(("PASS", name, detail))

    def fail(self, name: str, detail: str) -> None:
        self.lines.append(("FAIL", name, detail))

    def skip(self, name: str, detail: str = "") -> None:
        self.lines.append(("SKIP", name, detail))

    @property
    def failures(self) -> list[str]:
        return [name for status, name, _ in self.lines if status == "FAIL"]

    def print(self) -> None:
        width = max(len(n) for _, n, _ in self.lines) if self.lines else 0
        for status, name, detail in self.lines:
            mark = {"PASS": "✔", "FAIL": "✘", "SKIP": "–"}[status]
            suffix = f"  {detail}" if detail else ""
            print(f" {mark} [{status:<4}] {name:<{width}}{suffix}")
        fails = len(self.failures)
        if fails:
            print(f"\n{fails} check(s) failed")
        else:
            print("\nall checks passed")


# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------

def check_deps(report: Report) -> None:
    try:
        import fugashi  # noqa: F401
        import mcp  # noqa: F401
        import fsrs  # noqa: F401
        import jaconv  # noqa: F401
    except ImportError as e:
        report.fail("deps", f"missing python package: {e}")
        return
    try:
        import unidic

        if not (os.path.isdir(unidic.DICDIR)
                and os.path.exists(os.path.join(unidic.DICDIR, "dicrc"))):
            report.fail("deps", "UniDic dicdir missing — run "
                                "`uv run python -m unidic download`")
            return
    except ImportError:
        report.fail("deps", "unidic not installed")
        return
    report.pass_("deps", "fugashi + UniDic + mcp + fsrs importable")


def check_dictionaries(report: Report, config: Config) -> dict[str, int]:
    import sqlite3

    counts: dict[str, int] = {}
    if not config.dict_db.exists():
        report.fail("dict.sqlite", f"missing at {config.dict_db} — "
                                   "run `moguru data build`")
    else:
        conn = sqlite3.connect(f"file:{config.dict_db}?mode=ro", uri=True)
        row = conn.execute(
            "SELECT (SELECT COUNT(*) FROM jmdict), (SELECT COUNT(*) FROM kanji), "
            "(SELECT COUNT(*) FROM krad), (SELECT COUNT(*) FROM pitch)"
        ).fetchone()
        conn.close()
        jmdict, kanji, krad, pitch = row
        counts.update(jmdict=jmdict, kanji=kanji, krad=krad, pitch=pitch)
        if jmdict < 100_000:
            report.fail("dict.sqlite", f"JMdict rows look truncated ({jmdict})")
        else:
            report.pass_("dict.sqlite",
                         f"JMdict {jmdict:,} · kanji {kanji:,} · krad {krad:,} · pitch {pitch:,}")
    if not config.freq_db.exists():
        report.fail("freq.sqlite", f"missing at {config.freq_db} — "
                                   "run `moguru data build`")
    else:
        conn = sqlite3.connect(f"file:{config.freq_db}?mode=ro", uri=True)
        n = conn.execute("SELECT COUNT(*) FROM freq").fetchone()[0]
        conn.close()
        counts["freq"] = n
        if n < 50_000:
            report.fail("freq.sqlite", f"freq rows look truncated ({n})")
        else:
            report.pass_("freq.sqlite", f"{n:,} headword ranks")
    return counts


def check_kb(report: Report, config: Config) -> None:
    if config.kb_db.exists():
        report.pass_("kb", str(config.kb_db.relative_to(REPO_ROOT)))
    else:
        report.pass_("kb", "(empty — created on first use)")


def check_srs(report: Report, config: Config) -> None:
    import requests

    if config.srs_backend == "anki":
        try:
            resp = requests.post(
                config.anki_connect_url,
                json={"action": "version", "version": 6}, timeout=4,
            )
            version = resp.json().get("result")
            report.pass_("srs", f"AnkiConnect v{version} at {config.anki_connect_url}")
        except Exception as e:
            report.fail("srs", f"backend=anki but AnkiConnect unreachable: {e}")
    elif config.srs_backend == "builtin":
        state = "exists" if config.srs_db.exists() else "(new)"
        report.pass_("srs", f"builtin FSRS {state}")
    else:
        report.pass_("srs", "backend=none")


def check_model(report: Report, config: Config) -> None:
    from moguru.orchestrator import providers as pm

    try:
        provider, headers = pm.resolve_role("main", config)
    except pm.ProviderError as e:
        report.fail("model", str(e))
        return
    try:
        ids = pm._list_models(provider.endpoint, headers, timeout=5)
    except Exception as e:
        report.fail("model", f"{provider.id}: {provider.endpoint} unreachable ({e.__class__.__name__})")
        return
    if ids and provider.model not in ids:
        from moguru.orchestrator.providers import detect_runtime

        runtime = detect_runtime(provider.endpoint)
        hint = ""
        if runtime == "ollama":
            hint = f" — run `ollama pull {provider.model}`?"
        elif runtime == "lmstudio":
            hint = " — load it in LM Studio and (re)start the server"
        report.fail("model",
                    f"{provider.model!r} not served at {provider.endpoint}{hint}")
    else:
        report.pass_("model", f"{provider.model} @ {provider.endpoint}")


async def _probe_server(module: str, tool: str, args: dict) -> tuple[bool, str]:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    env = {**os.environ, "MOGURU_CONFIG": str(REPO_ROOT / "config.yaml")}
    params = StdioServerParameters(command=sys.executable, args=["-m", module],
                                   env=env)
    try:
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.call_tool(tool, args)
                texts = [c.text for c in result.content if hasattr(c, "text")]
                return (not getattr(result, "isError", False),
                        "\n".join(texts)[:80])
    except Exception as e:
        return False, str(e)[:120]


def check_shadow(report: Report, config: Config) -> None:
    """Phase 3: shadow store + dedicated small model + calibration honesty."""
    from moguru.orchestrator import providers as pm

    db = config.user_dir / "shadow.sqlite"
    if not db.exists():
        report.skip("shadow", "(no signals yet — store created on first use)")
        return
    try:
        from moguru.mcp.shadow_mcp import core as shadow_core

        cal = shadow_core.calibration(config)
        detail = f"{db.relative_to(REPO_ROOT)}"
        if cal.get("n"):
            detail += f" · calibration n={cal['n']} brier={cal['brier_score']}"
        report.pass_("shadow", detail)
    except Exception as e:
        report.fail("shadow", str(e)[:100])
    try:
        provider, headers = pm.resolve_role("shadow", config)
        ids = pm._list_models(provider.endpoint, headers, timeout=5)
        if provider.model in ids:
            report.pass_("shadow-model", f"{provider.model} @ {provider.endpoint}")
        else:
            report.fail("shadow-model",
                        f"{provider.model!r} not served at {provider.endpoint}")
    except Exception:
        report.skip("shadow-model",
                    "unreachable — stat core runs without it (queue persists)")


async def check_servers(report: Report, manifest: dict[str, Any]) -> None:
    module_by_id = {
        "parser": "moguru.mcp.parser_mcp.server",
        "dict": "moguru.mcp.dict_mcp.server",
        "freq": "moguru.mcp.freq_mcp.server",
        "kb": "moguru.mcp.kb_mcp.server",
        "srs": "moguru.mcp.srs_mcp.server",
        "media": "moguru.mcp.media_mcp.server",
        "shadow": "moguru.mcp.shadow_mcp.server",
    }
    for s in manifest["servers"]:
        sid = s["id"]
        health = s.get("health")
        if not health:
            report.skip(f"server:{sid}", "no health probe declared")
            continue
        ok, detail = await _probe_server(
            module_by_id[sid], health["tool"], health["args"]
        )
        if ok:
            report.pass_(f"server:{sid}", f"{health['tool']} → {detail[:60]}")
        else:
            report.fail(f"server:{sid}", detail)


def check_service(report: Report, port: int = 8766) -> None:
    import requests

    try:
        resp = requests.get(f"http://localhost:{port}/health", timeout=2)
        report.pass_("service", f"engine service on :{port}")
    except Exception:
        report.skip("service", f"not running on :{port} (start with `moguru serve`)")


# ---------------------------------------------------------------------------
# Entry
# ---------------------------------------------------------------------------

def run_doctor(config: Config | None = None, fix: bool = False,
               skip_servers: bool = False) -> int:
    from moguru.orchestrator import bundle as bundle_mod

    config = config or Config.load()
    report = Report()
    try:
        manifest = bundle_mod.load_manifest()
        report.pass_("manifest", f"moguru-bundle.json v{manifest['version']}")
    except bundle_mod.BundleError as e:
        report.fail("manifest", str(e))
        manifest = {"servers": []}

    check_deps(report)
    check_dictionaries(report, config)
    check_kb(report, config)
    check_srs(report, config)
    check_model(report, config)
    check_shadow(report, config)
    if not skip_servers:
        asyncio.run(check_servers(report, manifest))
    check_service(report)

    report.print()
    if report.failures and fix:
        print("\n--fix: re-running failed data steps…")
        from moguru.orchestrator import data_pipeline

        needs = any("sqlite" in f for f in report.failures)
        if needs:
            data_pipeline.download(config)
            data_pipeline.build(config)
            print("data re-downloaded/re-built — run `moguru doctor` again")
    return 1 if report.failures else 0
