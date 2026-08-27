"""dict-mcp core — reference lookups (ground truth, spec §3.2).

Principle 0.1: dictionary entries are ALWAYS resolved from real data. The
model must never recite dictionary content from its own weights — a tool call
returning ground truth is required before any reading/definition/frequency is
shown to the learner.
"""

from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path
from typing import Any

from moguru.config import Config, REPO_ROOT


def _conn(config: Config | None = None) -> sqlite3.Connection:
    if config is None:
        config = Config.load(os.environ.get("MOGURU_CONFIG") or REPO_ROOT / "config.yaml")
    if not config.dict_db.exists():
        raise FileNotFoundError(
            f"dictionary database not found at {config.dict_db} — "
            "run `moguru data build` first"
        )
    conn = sqlite3.connect(f"file:{config.dict_db}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def lookup_word(query: str, reading: str | None = None) -> list[dict[str, Any]]:
    """JMdict (J-E, primary). Entry = { headwords[], readings[],
    senses[{gloss[], pos[], misc[], field[]}], id }"""
    conn = _conn()
    try:
        if reading:
            rows = conn.execute(
                """SELECT DISTINCT j.* FROM jmdict j
                   JOIN jmdict_keys k ON k.ent_seq = j.id
                   WHERE k.key = ? AND EXISTS (
                     SELECT 1 FROM jmdict_keys k2
                     WHERE k2.ent_seq = j.id AND k2.key = ?)""",
                (query, reading),
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT DISTINCT j.* FROM jmdict j
                   JOIN jmdict_keys k ON k.ent_seq = j.id
                   WHERE k.key = ?""",
                (query,),
            ).fetchall()
        if not rows and not reading:
            # FTS fallback: substring/contains search (only for unfiltered
            # lookups — an explicit reading filter that matched nothing is
            # a definitive "no such entry")
            try:
                fts = conn.execute(
                    "SELECT ent_seq FROM jmdict_fts WHERE jmdict_fts MATCH ? LIMIT 20",
                    (f'"{query}"*',),
                ).fetchall()
                seqs = [r["ent_seq"] for r in fts]
                if seqs:
                    qmarks = ",".join("?" * len(seqs))
                    rows = conn.execute(
                        f"SELECT * FROM jmdict WHERE id IN ({qmarks})", seqs
                    ).fetchall()
            except sqlite3.OperationalError:
                rows = []
        out = []
        for r in rows:
            out.append(
                {
                    "id": r["id"],
                    "headwords": json.loads(r["kanji"]),
                    "readings": json.loads(r["kana"]),
                    "senses": json.loads(r["senses"]),
                }
            )
        return out
    finally:
        conn.close()


def lookup_name(query: str) -> list[dict[str, Any]]:
    """JMnedict (people/places)."""
    conn = _conn()
    try:
        rows = conn.execute(
            """SELECT DISTINCT n.* FROM jmnedict n
               JOIN jmnedict_keys k ON k.ent_seq = n.id
               WHERE k.key = ?""",
            (query,),
        ).fetchall()
        return [
            {
                "id": r["id"],
                "headwords": json.loads(r["kanji"]),
                "readings": json.loads(r["kana"]),
                "gloss": json.loads(r["gloss"]),
            }
            for r in rows
        ]
    finally:
        conn.close()


def lookup_kanji(char: str) -> dict[str, Any] | None:
    """KANJIDIC2. KanjiEntry = { char, on_readings[], kun_readings[],
    meanings[], nanori[], stroke_count, grade, jlpt, freq_rank,
    radicals[], components[] }"""
    if len(char) != 1:
        raise ValueError("lookup_kanji expects exactly one character")
    conn = _conn()
    try:
        r = conn.execute("SELECT * FROM kanji WHERE char = ?", (char,)).fetchone()
        if r is None:
            return None
        radicals = [
            row["radical"]
            for row in conn.execute(
                "SELECT radical FROM kanji_radicals WHERE char = ?", (char,)
            )
        ]
        krad_row = conn.execute(
            "SELECT components FROM krad WHERE char = ?", (char,)
        ).fetchone()
        components = json.loads(krad_row["components"]) if krad_row else []
        return {
            "char": r["char"],
            "on_readings": json.loads(r["on_readings"]),
            "kun_readings": json.loads(r["kun_readings"]),
            "meanings": json.loads(r["meanings"]),
            "nanori": json.loads(r["nanori"]),
            "stroke_count": r["stroke_count"],
            "grade": r["grade"],
            "jlpt": r["jlpt"],
            "freq_rank": r["freq_rank"],
            "radicals": radicals,
            "components": components,
        }
    finally:
        conn.close()


def lookup_monolingual(query: str) -> list[dict[str, Any]]:
    """J-J definitions (monolingual mode). Requires a Yomitan-format J-J
    package imported into the jj_entries table (see importers)."""
    conn = _conn()
    try:
        try:
            rows = conn.execute(
                "SELECT * FROM jj_entries WHERE headword = ? ORDER BY id", (query,)
            ).fetchall()
        except sqlite3.OperationalError:
            rows = []
        if not rows:
            raise FileNotFoundError(
                "monolingual source not configured: drop a Yomitan-format J-J "
                "dictionary package into data/dictionaries/jj/ and run "
                "`moguru data build --monolingual`"
            )
        return [
            {
                "id": r["id"],
                "headword": r["headword"],
                "reading": r["reading"],
                "definition": r["definition"],
            }
            for r in rows
        ]
    finally:
        conn.close()


def decompose_kanji(char: str) -> list[dict[str, Any]]:
    """Radicals/primitives composing a kanji — feeds RTK stories."""
    if len(char) != 1:
        raise ValueError("decompose_kanji expects exactly one character")
    conn = _conn()
    try:
        row = conn.execute("SELECT components FROM krad WHERE char = ?", (char,)).fetchone()
        if row is None:
            return []
        comps = json.loads(row["components"])
        out: list[dict[str, Any]] = []
        for c in comps:
            entry = {
                "component": c,
                "is_radical": bool(
                    conn.execute(
                        "SELECT 1 FROM radk WHERE radical = ?", (c,)
                    ).fetchone()
                ),
            }
            kan = conn.execute(
                "SELECT meanings FROM kanji WHERE char = ?", (c,)
            ).fetchone()
            if kan:
                meanings = json.loads(kan["meanings"])
                if meanings:
                    entry["meaning"] = meanings[0]
            out.append(entry)
        return out
    finally:
        conn.close()


def lookup_pitch(headword: str, reading: str | None = None) -> list[dict[str, Any]]:
    """kanjium pitch accent: [{headword, reading, accents: [int...]}]"""
    conn = _conn()
    try:
        if reading:
            rows = conn.execute(
                "SELECT * FROM pitch WHERE headword = ? AND reading = ?",
                (headword, reading),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM pitch WHERE headword = ?", (headword,)
            ).fetchall()
        return [
            {
                "headword": r["headword"],
                "reading": r["reading"],
                "accents": [int(a) for a in r["accents"].split(",") if a.strip().isdigit()],
            }
            for r in rows
        ]
    finally:
        conn.close()
