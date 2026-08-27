"""Gate tests for parser-mcp (spec §9 step 1: parser must return correct
data before anything downstream is built)."""

from __future__ import annotations

import pytest

from moguru.mcp.parser_mcp import core


def test_tokenize_basic():
    toks = core.tokenize("魚を食べた。")
    assert toks, "no tokens"
    surfaces = [t["surface"] for t in toks]
    assert surfaces[0] == "魚"
    # 見た -> 食べた must resolve its verb to dictionary form 食べる
    lemmas = [t["lemma"] for t in toks]
    assert "食べる" in lemmas
    # readings are kana
    tabe = next(t for t in toks if t["lemma"] == "食べる")
    assert tabe["reading_kana"] and tabe["reading_kana"] not in tabe["surface"]
    # char offsets reconstruct the text
    assert toks[0]["char_start"] == 0
    assert "".join(
        core.tokenize("魚を食べた。")[i]["surface"] for i in range(len(toks))
    ) == "魚を食べた。"


def test_tokenize_pos_and_content_filter():
    toks = core.tokenize("魚を食べた。")
    by_surface = {t["surface"]: t for t in toks}
    assert by_surface["を"]["pos"] == "助詞"
    assert core.is_content_word(by_surface["魚"]) is True
    assert core.is_content_word(by_surface["を"]) is False


def test_deinflect_ta_form():
    cands = core.deinflect("食べた")
    assert any(c["lemma"] == "食べる" for c in cands)


def test_deinflect_nai_form():
    cands = core.deinflect("食べない")
    assert any(c["lemma"] == "食べる" for c in cands)


def test_segment_sentences():
    parts = core.segment_sentences("今日は晴れですね。雨が降りそう！行こうか？")
    assert len(parts) == 3
    assert parts[0].endswith("。")


def test_to_reading():
    hira = core.to_reading("魚を食べた。", "hiragana")
    assert hira == "さかなをたべた。"
    kata = core.to_reading("魚を食べた。", "katakana")
    assert kata == "サカナヲタベタ。"
    romaji = core.to_reading("魚を食べた。", "romaji")
    assert romaji.startswith("sakana")


def test_pitch_accent_field_present():
    toks = core.tokenize("魚を食べた。")
    # at least some tokens carry UniDic accent info (aType)
    assert any("pitch_accent" in t for t in toks)


def test_sudachi_engine_selectable():
    pytest.importorskip("sudachipy")
    eng = core.SudachiEngine()
    morphs = eng.analyze("魚を食べた。")
    assert morphs and morphs[0]["surface"] == "魚"
