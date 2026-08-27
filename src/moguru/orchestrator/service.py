"""Engine service boundary (main spec §8.1 + Reader spec §3).

Wraps the Phase 1 engine in a local HTTP service so any surface can drive it:

    POST /lookup      { text }                -> tokens + entries
    POST /mine        { text, media_ref?, add? } -> candidates + cards
    POST /assess      { text }                -> comprehensibility verdict
    POST /ask         { question, context }   -> grounded explanation
    POST /annotate    { text }                -> banded tokens + sentences
    POST /mark_known  { lemma }               -> kb.mark_known(lemma, "reader")
    GET  /known_version                        -> cache key for reader repaints
    GET  /health                                -> doctor hook

Surfaces are dumb clients of these endpoints; all logic stays in the engine.
Built on starlette (already present via the MCP SDK) — zero new dependencies.

Run: moguru serve   (default http://localhost:8766)
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from moguru.config import Config

_config: Config | None = None


def config() -> Config:
    global _config
    if _config is None:
        _config = Config.load()
    return _config


async def health(request: Request) -> JSONResponse:
    return JSONResponse({"ok": True, "service": "moguru"})


async def known_version(request: Request) -> JSONResponse:
    from moguru.mcp.kb_mcp import core as kb_core

    conn = kb_core.connect()
    try:
        row = conn.execute(
            "SELECT COUNT(*) c, COALESCE(MAX(last_seen), '') m FROM known_words"
        ).fetchone()
    finally:
        conn.close()
    digest = hashlib.sha1(f"{row['c']}:{row['m']}".encode()).hexdigest()[:12]
    return JSONResponse({"version": digest, "known_words": row["c"]})


async def annotate(request: Request) -> JSONResponse:
    from moguru.mcp.parser_mcp import core as parser_core
    from moguru.orchestrator.annotate import annotate_text
    from moguru.mcp.shadow_mcp import core as shadow_core

    body = await request.json()
    text = (body or {}).get("text", "")
    if not text.strip():
        return JSONResponse({"error": "text required"}, status_code=400)
    data = annotate_text(text, config())
    page = (body or {}).get("page")  # Reader spec §3: echo for cache/log keys
    if page is not None:
        data["page"] = page

    # Shadow integration (§11): known_unstable = paper-known but behaviorally
    # shaky — Reader spec step 6, only now fed by real data.
    try:
        lemmas = sorted({t["lemma"] for t in data["tokens"]
                         if t["band"] == "known" and t["lemma"]})
        if lemmas:
            cfg = config()
            estimates = shadow_core.comprehension_batch(lemmas, "reading", "vocab", cfg)
            unstable = {
                e["key"] for e in estimates
                if e["sample_size"] >= cfg.shadow_min_samples
                and e["p_understood"] < 0.6
            }
            if unstable:
                data["known_unstable"] = sorted(unstable)
                for t in data["tokens"]:
                    if t.get("lemma") in unstable:
                        t["band"] = "known_unstable"
    except Exception:
        pass  # shadow store unavailable — plain annotate result stands

    return JSONResponse(data)


async def signals(request: Request) -> JSONResponse:
    """Batch behavioral signals from surfaces (§4 schema) -> shadow-mcp."""
    from moguru.mcp.shadow_mcp import core as shadow_core

    body = await request.json()
    batch = (body or {}).get("signals", [])
    if not isinstance(batch, list) or not batch:
        return JSONResponse({"error": "signals[] required"}, status_code=400)
    results = []
    for signal in batch[:200]:
        results.append(shadow_core.record_signal(signal, config()))
    accepted = sum(1 for r in results if r.get("accepted"))
    return JSONResponse({"accepted": accepted, "total": len(results),
                         "results": results})


async def mark_known(request: Request) -> JSONResponse:
    from moguru.mcp.kb_mcp import core as kb_core

    body = await request.json()
    lemma = (body or {}).get("lemma", "").strip()
    if not lemma:
        return JSONResponse({"error": "lemma required"}, status_code=400)
    kb_core.mark_known(lemma, "reader")
    return JSONResponse({"ok": True, "lemma": lemma, "source": "reader"})


async def lookup(request: Request) -> JSONResponse:
    from moguru.mcp.dict_mcp import core as dict_core
    from moguru.mcp.parser_mcp import core as parser_core

    body = await request.json()
    text = (body or {}).get("text", "")
    if not text.strip():
        return JSONResponse({"error": "text required"}, status_code=400)
    cfg = config()
    tokens = parser_core.tokenize(text, cfg)
    entries: dict[str, Any] = {}
    for t in tokens:
        lemma = t["lemma"]
        if not lemma or lemma in entries or not parser_core.is_content_word(t):
            continue
        try:
            entries[lemma] = dict_core.lookup_word(lemma)[:2]
        except Exception:
            entries[lemma] = []
    return JSONResponse({"tokens": tokens, "entries": entries})


async def mine(request: Request) -> JSONResponse:
    from moguru.orchestrator import mining

    body = await request.json() or {}
    text = body.get("text", "")
    if not text.strip():
        return JSONResponse({"error": "text required"}, status_code=400)
    add = bool(body.get("add", False))
    target = body.get("target")
    if target:
        # Reader spec §3: the clicked word is authoritative — mine exactly
        # that word in that sentence, auto-candidate or not. (mine_* emits
        # the §11 `mine` signal itself — one emission, one place.)
        item = mining.mine_with_target(
            text, target, config(),
            media_ref=body.get("media_ref"), auto_add=add,
        )
        if "error" in item:
            return JSONResponse(item, status_code=422)
        return JSONResponse({"results": [item]})
    results = mining.mine_text(
        text,
        config(),
        media_ref=body.get("media_ref"),
        auto_add=add,
    )
    return JSONResponse({"results": results})


async def assess(request: Request) -> JSONResponse:
    from moguru.orchestrator import mining

    body = await request.json()
    text = (body or {}).get("text", "")
    if not text.strip():
        return JSONResponse({"error": "text required"}, status_code=400)
    return JSONResponse(mining.assess_text(text, config()))


def _ask_grounding(question: str, cfg: Config) -> str:
    """Local dictionary entries for the 「…」-quoted word in the question.

    The reader's explain popover asks about one word — SQLite answers that in
    milliseconds, so the model never needs tools to fetch it.
    """
    m = re.search(r"「(.+?)」", question)
    word = (m.group(1).strip() if m else "").strip()
    if not word:
        return ""
    from moguru.mcp.dict_mcp import core as dict_core
    from moguru.mcp.parser_mcp import core as parser_core

    def clip(obj: Any, n: int = 1200) -> str:
        s = json.dumps(obj, ensure_ascii=False)
        return s if len(s) <= n else s[:n] + "…"

    parts: list[str] = []
    try:
        toks = parser_core.tokenize(word, cfg)
        lemma = toks[0]["lemma"] if toks and toks[0].get("lemma") else word
    except Exception:
        lemma = word
    for query in dict.fromkeys([word, lemma]):  # dedupe, keep order
        try:
            entries = dict_core.lookup_word(query)[:2]
            if entries:
                parts.append(f"辞書エントリ({query}): {clip(entries)}")
        except Exception:
            pass
    try:
        pitch = dict_core.lookup_pitch(word) or dict_core.lookup_pitch(lemma)
        if pitch:
            parts.append(f"アクセント: {clip(pitch)}")
    except Exception:
        pass
    for ch in word:
        if not re.match(r"[\u4e00-\u9fff]", ch):  # kanji only
            continue
        try:
            kanji = dict_core.lookup_kanji(ch)
            if kanji:
                parts.append(f"漢字情報({ch}): {clip(kanji, 800)}")
        except Exception:
            pass
    return "\n".join(parts)


async def ask(request: Request) -> JSONResponse:
    body = await request.json()
    question = (body or {}).get("question", "").strip()
    context = (body or {}).get("context", "").strip()
    if not question:
        return JSONResponse({"error": "question required"}, status_code=400)

    # Plain no-tools completion grounded in local dictionary entries.
    # (Routing /ask through the full agent loop shipped all ~40 MCP tool
    # schemas with every explain — a 27B thinking model then burned minutes
    # on prompt processing + tool-call rounds for lookups SQLite does in
    # milliseconds. Tools stay for `moguru chat`, where they belong.)
    from moguru.orchestrator.agent import ModelRouter

    grounding = _ask_grounding(question, config())
    system = (
        "あなたは日本語学習支援アシスタントです。与えられた辞書情報だけを根拠に、"
        "簡潔に答えてください。読み方・意味・アクセントを箇条書きで示し、各項目に"
        "短い英語の説明 (short English gloss) を添えてください — the learner reads "
        "Japanese but wants an English gloss for each item. 推論は短く。"
    )
    user = f"文脈: {context}\n質問: {question}" if context else question
    if grounding:
        user = f"{grounding}\n\n{user}"
    router = ModelRouter(config())
    messages: list[dict] = [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]
    # Thinking models (qwen3.8 via LM Studio) can burn an entire small
    # budget on reasoning_content and return EMPTY content with
    # finish_reason=length. Escalate the budget across attempts instead of
    # failing empty; ask local runtimes to skip thinking outright (unknown
    # body keys are ignored by llama.cpp-family servers, but hosted APIs
    # would 400 — so it's gated to local providers inside the client).
    try:
        answer = ""
        for budget in (1600, 3200):
            response = router.chat(
                messages, max_tokens=budget,
                extra_body={"chat_template_kwargs": {"enable_thinking": False}},
            )
            answer = (response["choices"][0]["message"].get("content") or "")
            answer = answer.split("</think>")[-1].strip()
            if answer:
                break
            messages.append({"role": "assistant", "content": ""})
            messages.append({"role": "user", "content": "（続けてください。）"})
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=502)
    return JSONResponse({"answer": answer, "grounded": bool(grounding)})


def build_app(cfg: Config | None = None) -> Starlette:
    global _config
    _config = cfg or Config.load()
    middleware = [
        Middleware(
            CORSMiddleware,
            allow_origin_regex=r"^(chrome-extension://.*|http://localhost:\d+)$",
            allow_methods=["GET", "POST"],
            allow_headers=["*"],
        )
    ]
    routes = [
        Route("/health", health, methods=["GET"]),
        Route("/known_version", known_version, methods=["GET"]),
        Route("/annotate", annotate, methods=["POST"]),
        Route("/mark_known", mark_known, methods=["POST"]),
        Route("/lookup", lookup, methods=["POST"]),
        Route("/mine", mine, methods=["POST"]),
        Route("/assess", assess, methods=["POST"]),
        Route("/ask", ask, methods=["POST"]),
        Route("/signals", signals, methods=["POST"]),
    ]
    return Starlette(routes=routes, middleware=middleware)


def run_server(port: int = 8766) -> None:
    import uvicorn

    app = build_app()
    print(f"潜る moguru engine service on http://localhost:{port}")
    print("endpoints: /lookup /mine /assess /ask /annotate /mark_known /known_version /health")
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")
