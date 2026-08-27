"""parser-mcp core — morphological analysis (spec §3.1).

The parser is the foundation: Japanese has no word boundaries, and nothing
downstream (lookup, i+1, mining) works until text is tokenized and
deinflected.

Engines:
  - mecab_unidic (default): fugashi + UniDic. UniDic is preferred because it
    carries lemma, reading, and pitch accent (aType).
  - sudachi: SudachiPy.
  - ichiran: not available locally — explicit error (interface preserved).
"""

from __future__ import annotations

import os
import re
from abc import ABC, abstractmethod
from typing import Any

import jaconv

from moguru.config import Config, REPO_ROOT


# ---------------------------------------------------------------------------
# Engines
# ---------------------------------------------------------------------------

class ParserEngine(ABC):
    """Minimal engine interface: raw morphs with normalized fields."""

    @abstractmethod
    def analyze(self, text: str) -> list[dict[str, Any]]:
        """Return raw morphs:
        [{surface, lemma, reading_kana, pos, pos_detail, inflection_type,
          base_form, pitch_accent}]
        """
        raise NotImplementedError


def _clean_lemma(lemma: str | None) -> str:
    """UniDic lemmas may carry alternatives ('有る/在る') or empty values."""
    if not lemma or lemma == "*":
        return ""
    return lemma.split("/")[0]


class MeCabUniDicEngine(ParserEngine):
    def __init__(self) -> None:
        import fugashi

        # fugashi.Tagger (not GenericTagger) gives named UniDic features
        # (pos1, cType, lemma, kana, aType). Systems without a global mecabrc
        # need -r /dev/null; the dicdir comes from the unidic package.
        import unidic

        self.tagger = fugashi.Tagger(f"-r /dev/null -d {unidic.DICDIR}")

    def analyze(self, text: str) -> list[dict[str, Any]]:
        out = []
        for node in self.tagger(text):
            f = node.feature
            surface = node.surface
            lemma = _clean_lemma(getattr(f, "lemma", ""))
            pos1 = getattr(f, "pos1", "") or ""
            pos2 = getattr(f, "pos2", "") or ""
            c_type = getattr(f, "cType", "") or ""
            c_form = getattr(f, "cForm", "") or ""
            kana = getattr(f, "kana", "") or ""
            if kana == "*":
                kana = ""
            try:
                a_type_raw = getattr(f, "aType", "")
                a_type = int(a_type_raw) if str(a_type_raw).isdigit() else None
            except (TypeError, ValueError):
                a_type = None
            out.append(
                {
                    "surface": surface,
                    "lemma": lemma or surface,
                    "reading_kana": kana,
                    "pos": pos1,
                    "pos_detail": pos2,
                    "inflection_type": f"{c_form}" if c_form else "",
                    "inflection_cType": c_type,
                    "base_form": lemma or surface,
                    "pitch_accent": a_type,
                }
            )
        # Drop the EOS artifact if present
        return [m for m in out if m["surface"]]


class SudachiEngine(ParserEngine):
    def __init__(self) -> None:
        from sudachipy import dictionary

        self.tokenizer = dictionary.Dictionary().create()

    def analyze(self, text: str) -> list[dict[str, Any]]:
        out = []
        for m in self.tokenizer.tokenize(text):
            parts = m.part_of_speech()  # 6-tuple
            surface = m.surface()
            out.append(
                {
                    "surface": surface,
                    "lemma": m.dictionary_form() or surface,
                    "reading_kana": (m.reading_form() or "").replace("*", ""),
                    "pos": parts[0],
                    "pos_detail": "/".join(p for p in parts[1:4] if p),
                    "inflection_type": parts[4] or "",
                    "inflection_cType": parts[4] or "",
                    "base_form": m.dictionary_form() or surface,
                    "pitch_accent": None,  # Sudachi carries no accent
                }
            )
        return out


class IchiranEngine(ParserEngine):
    def __init__(self) -> None:
        raise RuntimeError(
            "parser.engine 'ichiran' requires the ichiran C library/server, "
            "which is not installed. Use 'mecab_unidic' (default) or 'sudachi'."
        )


_ENGINES = {
    "mecab_unidic": MeCabUniDicEngine,
    "sudachi": SudachiEngine,
    "ichiran": IchiranEngine,
}

_engine_instance: ParserEngine | None = None
_engine_name: str | None = None


def get_engine(config: Config | None = None) -> ParserEngine:
    global _engine_instance, _engine_name
    if config is None:
        from moguru.config import Config as _C

        config = _C.load(os.environ.get("MOGURU_CONFIG") or REPO_ROOT / "config.yaml")
    if _engine_instance is not None and _engine_name == config.parser_engine:
        return _engine_instance
    cls = _ENGINES.get(config.parser_engine)
    if cls is None:
        raise ValueError(f"unknown parser.engine: {config.parser_engine!r}")
    _engine_instance = cls()
    _engine_name = config.parser_engine
    return _engine_instance


# ---------------------------------------------------------------------------
# Spec §3.1 tools
# ---------------------------------------------------------------------------

def tokenize(text: str, config: Config | None = None) -> list[dict[str, Any]]:
    """Token = { surface, lemma, reading_kana, pos, pos_detail,
                inflection_type, base_form, pitch_accent?, char_start, char_end }
    """
    engine = get_engine(config)
    tokens: list[dict[str, Any]] = []
    char_pos = 0
    for m in engine.analyze(text):
        surface = m["surface"]
        # Locate the surface in the remaining text (MeCab may normalize
        # whitespace); fall back to advancing by its length.
        idx = text.find(surface, char_pos)
        start = idx if idx >= 0 else char_pos
        end = start + len(surface)
        tok = {
            "surface": surface,
            "lemma": m["lemma"],
            "reading_kana": m["reading_kana"],
            "pos": m["pos"],
            "pos_detail": m["pos_detail"],
            "inflection_type": m["inflection_type"],
            "base_form": m["base_form"],
            "char_start": start,
            "char_end": end,
        }
        if m.get("pitch_accent") is not None:
            tok["pitch_accent"] = m["pitch_accent"]
        tokens.append(tok)
        char_pos = end
    return tokens


_SENTENCE_ENDERS = "。！？!?…‼⁉｡"


def segment_sentences(text: str) -> list[str]:
    """Split text into sentences on terminal punctuation / newlines."""
    sentences: list[str] = []
    buf: list[str] = []
    for ch in text:
        buf.append(ch)
        if ch in _SENTENCE_ENDERS:
            sentences.append("".join(buf))
            buf = []
    tail = "".join(buf).strip()
    if tail:
        sentences.append(tail)
    return [s.strip() for s in sentences if s.strip()]


def to_reading(
    text: str,
    mode: str = "hiragana",
    config: Config | None = None,
) -> str:
    """Whole-text reading ('hiragana' | 'katakana' | 'romaji') — furigana base."""
    engine = get_engine(config)
    parts: list[str] = []
    for m in engine.analyze(text):
        if m["reading_kana"]:
            kana = m["reading_kana"]
        elif any("ァ" <= c <= "ン" or "ぁ" <= c <= "ん" for c in m["surface"]):
            kana = jaconv.kata2hira(m["surface"])
        else:
            kana = m["surface"]
        parts.append(kana)
    joined = "".join(parts)
    if mode == "katakana":
        return jaconv.hira2kata(joined)
    if mode == "romaji":
        return jaconv.kata2alphabet(joined)
    return jaconv.kata2hira(joined)


# ---------------------------------------------------------------------------
# deinflect
# ---------------------------------------------------------------------------

# Rule-based cascade (Yomitan-style) over kana tails. MeCab already resolves
# most inflections via the lemma; these rules catch standalone surfaces that
# MeCab mis-analyzes and produce extra candidates that get verified.
_DEINFLECT_RULES: list[tuple[str, str, str]] = [
    # (ending, replacement, part_of_speech_hint)
    ("ませんでした", "る", "v1"),
    ("なかった", "る", "v1"),
    ("なかった", "う", "v5"),
    ("くれました", "くれる", "v1"),
    ("ました", "る", "v1"),
    ("ました", "う", "v5"),
    ("ませず", "る", "v1"),
    ("たい", "たい", "adj-i"),
    ("たくない", "たい", "adj-i"),
    ("ません", "る", "v1"),
    ("ましょう", "る", "v1"),
    ("えば", "う", "v5"),
    ("った", "う", "v5"),
    ("った", "る", "v5"),
    ("って", "う", "v5"),
    ("って", "る", "v5"),
    ("んだ", "む", "v5"),
    ("んだ", "ぶ", "v5"),
    ("んで", "む", "v5"),
    ("んで", "ぶ", "v5"),
    ("いた", "く", "v5"),
    ("いて", "く", "v5"),
    ("いだ", "ぐ", "v5"),
    ("いで", "ぐ", "v5"),
    ("した", "する", "vs"),
    ("して", "する", "vs"),
    ("れない", "る", "v1"),
    ("られる", "る", "v1"),
    ("させる", "る", "v1"),
    ("ない", "ない", "neg"),
    ("た", "る", "v1"),
    ("て", "る", "v1"),
    ("だ", "だ", "copula"),
    ("です", "だ", "copula"),
    ("ます", "る", "v1"),
    ("る", "る", "v1"),
]


def _validate_candidate(engine: ParserEngine, candidate: str) -> bool:
    """A candidate lemma is valid if the engine analyzes it back to itself."""
    if not candidate:
        return False
    morphs = engine.analyze(candidate)
    if not morphs:
        return False
    first = morphs[0]
    if len(morphs) == 1 and first["surface"] == candidate:
        # dictionary-form words analyze as themselves
        return first["lemma"] == candidate or first["base_form"] == candidate
    return False


def deinflect(surface: str, config: Config | None = None) -> list[dict[str, Any]]:
    """見た -> 見る, etc. Returns candidate lemmas, best first.

    candidate_lemma = { lemma, via, inflection }
    """
    engine = get_engine(config)
    candidates: list[dict[str, Any]] = []
    seen: set[str] = set()

    def add(lemma: str, via: str) -> None:
        if lemma and lemma not in seen and lemma != surface:
            seen.add(lemma)
            candidates.append({"lemma": lemma, "via": via})

    # 1) The analyzer's own lemma for this surface.
    morphs = engine.analyze(surface)
    for m in morphs:
        if m["inflection_type"] or m["inflection_cType"]:
            add(m["lemma"], "analyzer-lemma")
            break

    # 2) Rule cascade over the surface (works on kana and kanji tails alike
    #    for the common godan/ichidan patterns).
    for ending, repl, _hint in _DEINFLECT_RULES:
        if surface.endswith(ending) and len(surface) > len(ending):
            stem = surface[: -len(ending)]
            for cand in (stem + repl, stem):
                if _validate_candidate(engine, cand):
                    add(cand, f"rule(-{ending})")

    # Deduplicate while keeping order
    return candidates


# ---------------------------------------------------------------------------
# POS utilities shared by the mining/comprehensibility skills (spec §4.1 step 3)
# ---------------------------------------------------------------------------

CONTENT_POS = {"名詞", "動詞", "形容詞", "形状詞", "副詞", "連体詞"}
_NON_CONTENT_NOUNL_DETAIL = {"代名詞", "数詞"}


def is_content_word(token: dict[str, Any]) -> bool:
    """Content words = nouns/verbs/adjectives/adverbs; drops particles,
    auxiliaries, symbols (spec §4.1 step 3)."""
    pos = token.get("pos", "")
    if pos not in CONTENT_POS:
        return False
    if pos in {"名詞", "代名詞"} and token.get("pos_detail") in _NON_CONTENT_NOUNL_DETAIL:
        return False
    if pos == "名詞" and token.get("pos_detail") in {"代名詞", "数詞"}:
        return False
    if pos == "名詞" and token.get("pos_detail") == "助動詞語幹":
        return False
    # Skip bare symbols/punctuation that slip through as nouns
    if re.fullmatch(r"[^\w々〆ヶ]+", token.get("surface", "")):
        return False
    return True
