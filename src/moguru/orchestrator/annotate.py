"""Band classification for the Reader surface (Reader spec §2).

Per token from the parser plus a per-sentence unknown count:

    known     — in your known-set → unstyled (clean page)
    iplus     — the one unknown word in an otherwise-known sentence → mine it
    new_hard  — unknown in a sentence with several unknowns → come back later

Only content words carry a band (particles/aux left plain, via POS). Reuses
the same parser + kb logic as mining/comprehensibility — nothing reinvented.
"""

from __future__ import annotations

from typing import Any

from moguru.config import Config
from moguru.mcp.kb_mcp import core as kb_core
from moguru.mcp.parser_mcp import core as parser_core


def annotate_text(text: str, config: Config | None = None) -> dict[str, Any]:
    """→ { tokens: [{surface, char_start, char_end, lemma, reading, band}],
             sentences: [{char_start, char_end, unknown_count}] }"""
    from moguru import config as cfgmod

    config = config or cfgmod.Config.load()
    known = set(kb_core.get_known_set())

    # -- sentences with char offsets (segment_sentences strips, so re-locate) --
    sentences: list[dict[str, Any]] = []
    cursor = 0
    for seg in parser_core.segment_sentences(text):
        idx = text.find(seg, cursor)
        start = idx if idx >= 0 else cursor
        sentences.append({"char_start": start, "char_end": start + len(seg),
                          "text": seg})
        cursor = start + len(seg)

    # -- tokens + per-sentence unknown counts --
    tokens_out: list[dict[str, Any]] = []
    for sent in sentences:
        toks = parser_core.tokenize(sent["text"], config)
        content = [t for t in toks if parser_core.is_content_word(t)]
        unknown = [t for t in content if t["lemma"] not in known]
        sent["unknown_count"] = len(unknown)
        sent.pop("text", None)
        for t in toks:
            tok = {
                "surface": t["surface"],
                "char_start": sent["char_start"] + t["char_start"],
                "char_end": sent["char_start"] + t["char_end"],
                "lemma": t["lemma"],
                "reading": t["reading_kana"],
                "pos": t["pos"],
            }
            if not parser_core.is_content_word(t):
                tok["band"] = "plain"  # particles/aux/symbols stay unstyled
            elif t["lemma"] in known:
                tok["band"] = "known"
            elif len(unknown) <= config.iplus_threshold:
                tok["band"] = "iplus"
            else:
                tok["band"] = "new_hard"
            tokens_out.append(tok)

    return {"tokens": tokens_out, "sentences": sentences}
