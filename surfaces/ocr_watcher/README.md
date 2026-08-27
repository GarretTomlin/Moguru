# surfaces/ocr_watcher — Phase 2 (spec §8)

Not built yet. Phase 1 ships first and gets used; Phase 2 then wraps the
engine in a local service and adds client surfaces that call it:

- POST /lookup   { text }           -> tokens + entries   (parser + dict)
- POST /mine     { text, media_ref?}-> candidates + cards (sentence-mining)
- POST /assess   { text }           -> comprehensibility verdict
- POST /ask      { question, context } -> grounded explanation

Every surface emits behavioral signals (shadow-mcp schema, spec §3.7) as a
side effect of use — the data Phase 3 runs on.
