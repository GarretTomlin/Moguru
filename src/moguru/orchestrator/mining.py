"""sentence-mining engine — the core loop (spec §4.1).

Deterministic in-process implementation of the i+1 algorithm. The
sentence-mining SKILL.md documents the same procedure for the model; both
produce identical candidates. Encodes the MIA preference: sentence cards,
one target word each.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from moguru import config as cfgmod
from moguru.config import Config
from moguru.mcp.dict_mcp import core as dict_core
from moguru.mcp.freq_mcp import core as freq_core
from moguru.mcp.kb_mcp import core as kb_core
from moguru.mcp.parser_mcp import core as parser_core

_CJK = "\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff"

# Canonical note fields (spec §4.5) — every card-producing path uses these.
CARD_FIELDS = [
    "Sentence", "TargetWord", "Reading", "Definition", "Audio", "Image",
    "PitchAccent", "Source",
]


@dataclass
class Candidate:
    sentence: str
    target: str | None
    target_reading: str | None = None
    score: float = 0.0
    freq_rank: int | None = None
    coverage: int = 0
    unknown: list[str] = field(default_factory=list)
    tokens: list[dict[str, Any]] = field(default_factory=list)
    friction: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        out = {
            "sentence": self.sentence,
            "target": self.target,
            "target_reading": self.target_reading,
            "freq_rank": self.freq_rank,
            "coverage": self.coverage,
            "unknown": self.unknown,
            "score": self.score,
        }
        if self.friction:
            out["friction"] = self.friction
        return out


def is_iplus(sentence: str, config: Config | None = None,
             known: set[str] | None = None) -> Candidate | None:
    """Spec §4.1 pseudocode, verbatim semantics.

    - content words only (particles/aux/symbols dropped by POS)
    - candidate iff unknown_count <= iplus_threshold
      and LEN_MIN <= len(tokens) <= LEN_MAX
    - 0 unknown = review/known-good sentence
    """
    config = config or cfgmod.Config.load()
    len_min, len_max = config.sentence_len

    toks = parser_core.tokenize(sentence, config)
    content = [t for t in toks if parser_core.is_content_word(t)]

    if known is None:
        unknown = [t for t in content if not kb_core.is_known(t["lemma"])["known"]]
    else:
        unknown = [t for t in content if t["lemma"] not in known]
    if not (len_min <= len(toks) <= len_max):
        return None
    if len(unknown) > config.iplus_threshold:
        return None
    target = unknown[0]["lemma"] if unknown else None
    target_reading = unknown[0]["reading_kana"] if unknown else None
    coverage = len(content) - len(unknown)
    return Candidate(
        sentence=sentence,
        target=target,
        target_reading=target_reading,
        coverage=coverage,
        unknown=[t["lemma"] for t in unknown],
        tokens=toks,
    )


def _rank(candidates: list[Candidate], config: Config) -> None:
    """Rank by target-word frequency (frequent first), then coverage (spec §4.1)."""
    targets = [c.target for c in candidates if c.target]
    freqs = {e["lemma"]: e["jpdb_rank"] for e in freq_core.rank_by_frequency(targets)}
    for c in candidates:
        c.freq_rank = freqs.get(c.target) if c.target else None
        # score: lower is better — frequent target first, then high coverage
        c.score = (c.freq_rank is None, c.freq_rank or 0, -c.coverage)
    candidates.sort(key=lambda c: c.score)


def find_candidates(text: str, config: Config | None = None) -> list[Candidate]:
    """segment_sentences -> tokenize -> is_known -> i+1 candidates."""
    config = config or cfgmod.Config.load()
    candidates = [
        c
        for s in parser_core.segment_sentences(text)
        if (c := is_iplus(s, config)) is not None
    ]
    _rank(candidates, config)
    _annotate_friction(candidates, config)
    return candidates


def _annotate_friction(candidates: list[Candidate], config: Config) -> None:
    """§11: sentence-mining consults predict_friction to prioritize and
    pre-empt. Best-effort — the shadow store being down must never block
    mining."""
    try:
        from moguru.mcp.shadow_mcp import core as shadow_core

        for c in candidates:
            frictions = shadow_core.predict_friction(c.sentence, "reading", config)
            if frictions:
                c.friction = [
                    {"type": f["type"], "p_break": f["p_break"], "reason": f["reason"]}
                    for f in frictions[:3]
                ]
    except Exception:
        pass


def build_card_fields(candidate: Candidate, config: Config | None = None,
                      media: dict[str, str] | None = None) -> dict[str, str]:
    """Assemble the canonical target-word note (spec §4.5) from ground truth:
    dictionary + frequency + pitch lookups. Never from model memory."""
    config = config or cfgmod.Config.load()
    media = media or {}
    fields: dict[str, str] = {k: "" for k in CARD_FIELDS}
    # bold/segment by the SURFACE form — the lemma (食べる) never appears in
    # an inflected sentence (食べた); the candidate's own tokens carry it
    target = candidate.target
    surface = target
    toks = candidate.tokens or []
    for i, t in enumerate(toks):
        if t.get("lemma") == target and t.get("surface"):
            surface = t["surface"]
            # conjugation auxiliaries belong to the word form (食べ + た →
            # 食べた); particles (を/が) never do
            for nxt in toks[i + 1:]:
                if nxt.get("pos") == "助動詞" and nxt.get("surface"):
                    surface += nxt["surface"]
                else:
                    break
            break
    fields["Sentence"] = _prepare_sentence(candidate.sentence, surface)
    fields["Source"] = media.get("source", "text")

    if not target:
        return fields  # review/known-good sentence card: sentence + source only

    fields["TargetWord"] = target

    entries = dict_core.lookup_word(target)
    reading = ""
    if entries:
        first = entries[0]
        readings = first["readings"] or []
        reading = readings[0] if readings else (candidate.target_reading or "")
        fields["Reading"] = reading
        fields["Definition"] = _render_definition(
            target, first, config, sentence=fields["Sentence"],
        )
    elif candidate.target_reading:
        fields["Reading"] = candidate.target_reading

    pitch = dict_core.lookup_pitch(target, reading or None)
    if pitch:
        accents = pitch[0]["accents"]
        fields["PitchAccent"] = f"{pitch[0]['reading']} [{','.join(map(str, accents))}]"
    elif candidate.tokens:
        for t in candidate.tokens:
            if t["lemma"] == target and t.get("pitch_accent") is not None:
                fields["PitchAccent"] = f"{t['reading_kana']} [{t['pitch_accent']}]"
                break

    fields["Audio"] = media.get("audio", "")
    fields["Image"] = media.get("image", "")
    return fields


_CJK = "\u3000-\u30ff\u3400-\u4dbf\u4e00-\u9fff"  # kana, CJK punct (。『』、), kanji


def _prepare_sentence(sentence: str, target: str) -> str:
    """Migaku/MIA card discipline: ONE clean sentence, target in situ.

    PDF text layers and span-wrapped web pages inject whitespace between
    every glyph — collapse it (real Latin spacing survives). Then trim to
    the single sentence containing the target, and bold the target so the
    card reads as one i+1 fact, not a paragraph.
    """
    text = re.sub(rf"(?<=[{_CJK}])\s+(?=[{_CJK}])", "", sentence or "")
    text = re.sub(r"\s+", " ", text).strip()
    if target:
        for seg in re.split(r"(?<=[。！？!?])", text):
            if target in seg:
                text = seg.strip()
                break
        if target in text:
            text = text.replace(target, f"<b>{target}</b>", 1)
    return text


def _pick_sense_index(target: str, sentence: str, senses: list[dict],
                      config: Config) -> int:
    """Which JMdict sense fits THIS sentence — the main model answers with a
    NUMBER only. The definition text itself never comes from model memory
    (build_card_fields contract); the model just points at ground truth.
    Best-effort: unreachable model / gibberish -> sense 0."""
    if len(senses) <= 1:
        return 0
    lines = [
        f"{i}: {'; '.join(s.get('gloss', [])[:4])[:120]}"
        for i, s in enumerate(senses)
    ]
    prompt = (
        f"次の文脈で単語「{target}」に最も当てはまる意味の番号だけを返してください。\n"
        f"文: {sentence}\n意味:\n" + "\n".join(lines) +
        "\n答えは番号のみ（例: 2）。"
    )
    try:
        from moguru.orchestrator.agent import ModelRouter

        resp = ModelRouter(config).chat(
            [{"role": "user", "content": prompt}], max_tokens=8,
        )
        content = resp["choices"][0]["message"].get("content") or ""
        m = re.search(r"\d+", content)
        idx = int(m.group()) if m else 0
        return idx if 0 <= idx < len(senses) else 0
    except Exception:
        return 0


def _render_definition(target: str, entry: dict[str, Any],
                       config: Config, sentence: str = "") -> str:
    """defs.mode transition policy (bilingual | mixed | monolingual), with
    Migaku-style context selection: the ONE sense fitting the sentence, not
    the whole dictionary entry dumped onto the card."""
    mode = config.defs_mode
    senses = entry.get("senses") or []
    idx = _pick_sense_index(target, sentence, senses, config) if sentence else 0
    chosen = senses[idx] if 0 <= idx < len(senses) else (senses[0] if senses else None)
    bilingual = "; ".join((chosen or {}).get("gloss", []))[:300]
    if mode == "bilingual":
        return bilingual
    jj = ""
    if mode in {"mixed", "monolingual"}:
        try:
            mono = dict_core.lookup_monolingual(target)
            if mono:
                jj = mono[0]["definition"][:300]
        except (FileNotFoundError, Exception):
            jj = ""
    if mode == "monolingual":
        return jj or bilingual or "(no definition found)"
    return f"{jj}\n—\n{bilingual}" if jj else bilingual


def mine_text(
    text: str,
    config: Config | None = None,
    media_ref: str | None = None,
    auto_add: bool = False,
    deck: str | None = None,
    tags: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Full mining pass (spec §4.1 steps 1–7).

    Returns [{candidate…, note_id?, fields…}]. With auto_add, cards are
    registered via srs-mcp.add_card and encounters recorded in kb-mcp.
    """
    from moguru.mcp.srs_mcp import core as srs_core

    config = config or cfgmod.Config.load()
    results: list[dict[str, Any]] = []
    for cand in find_candidates(text, config):
        fields = build_card_fields(
            cand, config, media={"source": media_ref} if media_ref else None
        )
        item = {"candidate": cand.to_dict(), "fields": fields}
        if auto_add:
            note_id = srs_core.get_backend(config).add_card(
                deck=deck or config.anki_deck,
                note_type="target-word",
                fields=fields,
                tags=tags or ["mined", "i+1"],
            )
            item["note_id"] = note_id
            kb_core.record_encounter(
                cand.target or "", cand.sentence, media_ref
            )
            if cand.target:
                kb_core.set_srs_note(cand.target, note_id)
                # mined words enter the known set at low strength
                _mark_mined_weak(cand.target, fields.get("Reading", ""))
                _emit_mine_signal(cand.target, cand.sentence, media_ref, config)
        results.append(item)
    return results


def _emit_mine_signal(target: str, sentence: str, media_ref: str | None,
                       config: Config) -> None:
    """§11: mining emits a `mine` signal on card creation (best-effort)."""
    try:
        from moguru.mcp.shadow_mcp import core as shadow_core

        shadow_core.record_signal(
            {"type": "mine", "key": target, "key_kind": "vocab",
             "sentence": sentence, "modality": "reading",
             "media_ref": media_ref},
            config,
        )
    except Exception:
        pass


def _mark_mined_weak(lemma: str, reading: str) -> None:
    conn = kb_core.connect()
    try:
        from datetime import datetime, timezone

        now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            """INSERT INTO known_words (lemma, reading, strength, source, first_seen, last_seen)
               VALUES (?,?,0.1,'mined',?,?)
               ON CONFLICT(lemma) DO NOTHING""",
            (lemma, reading, now, now),
        )
        conn.commit()
    finally:
        conn.close()


def mine_with_target(
    sentence: str,
    target: str,
    config: Config | None = None,
    media_ref: str | None = None,
    auto_add: bool = False,
    deck: str | None = None,
    tags: list[str] | None = None,
) -> dict[str, Any]:
    """Mine a specific word in a specific sentence — the Reader's right-click
    'Send to Anki' (spec Reader §3: /mine { text, target }). The learner's
    click is authoritative: this works even when the sentence is not an
    auto-candidate (e.g. a new_hard sentence they chose anyway). Fields stay
    grounded in dictionary/frequency/pitch tools."""
    from moguru.mcp.srs_mcp import core as srs_core

    config = config or cfgmod.Config.load()
    toks = parser_core.tokenize(sentence, config)
    tok = next(
        (t for t in toks if t["lemma"] == target or t["surface"] == target), None
    )
    if tok is None:
        return {"error": f"{target!r} not found in sentence"}
    cand = Candidate(
        sentence=sentence,
        target=tok["lemma"],
        target_reading=tok["reading_kana"],
        coverage=sum(1 for t in toks if parser_core.is_content_word(t)) - 1,
        unknown=[],
        tokens=toks,
    )
    fields = build_card_fields(
        cand, config, media={"source": media_ref} if media_ref else None
    )
    item: dict[str, Any] = {"candidate": cand.to_dict(), "fields": fields}
    if auto_add:
        note_id = srs_core.get_backend(config).add_card(
            deck=deck or config.anki_deck,
            note_type="target-word",
            fields=fields,
            tags=tags or ["mined", "i+1", "reader"],
        )
        item["note_id"] = note_id
        kb_core.record_encounter(tok["lemma"], sentence, media_ref)
        kb_core.set_srs_note(tok["lemma"], note_id)
        _mark_mined_weak(tok["lemma"], fields.get("Reading", ""))
        _emit_mine_signal(tok["lemma"], sentence, media_ref, config)
    return item


# ---------------------------------------------------------------------------
# comprehensibility (spec §4.3)
# ---------------------------------------------------------------------------

def assess_text(text: str, config: Config | None = None) -> dict[str, Any]:
    """{ pct_known, iplus_density, unknown_words[], verdict }
    verdict ∈ { too_easy, iplus_sweet_spot, too_hard }"""
    config = config or cfgmod.Config.load()
    sentences = parser_core.segment_sentences(text)
    known = set(kb_core.get_known_set())

    content_total = 0
    content_known = 0
    unknown_counter: dict[str, int] = {}
    for s in sentences:
        toks = parser_core.tokenize(s, config)
        content = [t for t in toks if parser_core.is_content_word(t)]
        content_total += len(content)
        for t in content:
            if t["lemma"] in known:
                content_known += 1
            else:
                unknown_counter[t["lemma"]] = unknown_counter.get(t["lemma"], 0) + 1

    pct_known = (
        content_known / content_total if content_total else 1.0
    )
    iplus_candidates = [
        c for s in sentences if (c := is_iplus(s, config, known=known)) is not None
    ]
    iplus_density = (
        len(iplus_candidates) / len(sentences) if sentences else 0.0
    )
    unknown_words = sorted(
        unknown_counter.items(), key=lambda kv: -kv[1]
    )

    if pct_known >= 0.99 or (pct_known >= 0.98 and iplus_density < 0.05):
        verdict = "too_easy"
    elif pct_known < 0.90:
        verdict = "too_hard"
    else:
        verdict = "iplus_sweet_spot"

    result = {
        "pct_known": round(pct_known, 4),
        "iplus_density": round(iplus_density, 4),
        "unknown_words": [w for w, _ in unknown_words],
        "verdict": verdict,
        "sentence_count": len(sentences),
        "content_word_count": content_total,
    }

    # §11: comprehensibility blends shadow estimates so "at my level" means
    # actually comprehended, not just carded (best-effort).
    try:
        from moguru.mcp.shadow_mcp import core as shadow_core

        keys = sorted(unknown_counter.keys() | known.intersection(
            unknown_counter.keys()
        )) or sorted(known)
        keys = keys[:400]
        if keys:
            estimates = shadow_core.comprehension_batch(keys, "reading", "vocab", config)
            est_by_key = {e["key"]: e for e in estimates}
            num = den = 0.0
            for lemma, count in list(unknown_counter.items()) + [
                (k, 1) for k in (known - set(unknown_counter)) if k in est_by_key
            ]:
                e = est_by_key.get(lemma)
                if not e or e["confidence"] == "low":
                    continue
                weight = count
                num += e["p_understood"] * weight
                den += weight
            if den:
                result["pct_understood"] = round(num / den, 4)
    except Exception:
        pass
    return result
