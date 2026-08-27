"""kb-mcp core — the learner's knowledge state (spec §3.4).

SQLite. This is the store the whole system revolves around: adaptivity comes
from the knowledge store — "i+1" is defined relative to what this user knows.
"""

from __future__ import annotations

import hashlib
import os
import sqlite3
from datetime import datetime, timezone
from typing import Any

from moguru.config import Config, REPO_ROOT

# Spec §3.4 schema — verbatim.
SCHEMA = """
CREATE TABLE IF NOT EXISTS known_words (
  lemma TEXT PRIMARY KEY, reading TEXT, strength REAL DEFAULT 0,
  source TEXT, first_seen TEXT, last_seen TEXT, encounter_count INTEGER DEFAULT 0,
  srs_note_id INTEGER
);
CREATE TABLE IF NOT EXISTS known_kanji (
  char TEXT PRIMARY KEY, source TEXT, first_seen TEXT
);
CREATE TABLE IF NOT EXISTS encounters (
  id INTEGER PRIMARY KEY, lemma TEXT, sentence TEXT, media_ref TEXT, ts TEXT
);
CREATE INDEX IF NOT EXISTS idx_encounters_lemma ON encounters(lemma);
"""

# sources: "anki" | "manual" | "mined" (spec §3.4) | "reader" (Reader surface)
VALID_SOURCES = {"anki", "manual", "mined", "reader"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def connect(config: Config | None = None) -> sqlite3.Connection:
    if config is None:
        config = Config.load(os.environ.get("MOGURU_CONFIG") or REPO_ROOT / "config.yaml")
    config.user_dir.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(config.kb_db)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


# ---------------------------------------------------------------------------
# Bloom filter for fast membership (spec: "fast membership (bloom + table)")
# ---------------------------------------------------------------------------

class Bloom:
    """Tiny bloom filter over the known-lemma set; false positives are fine
    because they are always confirmed against the table."""

    def __init__(self, capacity: int = 200_000, bits: int = 1 << 21,
                 hashes: int = 3):
        self.capacity = capacity
        self.bits = bits
        self.hashes = hashes
        self._filter = bytearray(bits // 8)
        self._count = 0

    def _positions(self, item: str):
        for i in range(self.hashes):
            h = hashlib.sha256(f"{i}:{item}".encode()).digest()
            yield int.from_bytes(h[:8], "big") % self.bits

    def add(self, item: str) -> None:
        if item in self:
            return
        for pos in self._positions(item):
            self._filter[pos // 8] |= 1 << (pos % 8)
        self._count += 1

    def __contains__(self, item: str) -> bool:
        return all(
            self._filter[pos // 8] & (1 << (pos % 8))
            for pos in self._positions(item)
        )


_BLOOM_CACHE: tuple[str, int, Bloom] | None = None


def _get_bloom(conn: sqlite3.Connection) -> Bloom:
    global _BLOOM_CACHE
    row = conn.execute("SELECT COUNT(*) c, MAX(last_seen) m FROM known_words").fetchone()
    key = f"{row['c']}:{row['m']}"
    if _BLOOM_CACHE and _BLOOM_CACHE[0] == key:
        return _BLOOM_CACHE[2]
    bloom = Bloom()
    for r in conn.execute("SELECT lemma FROM known_words"):
        bloom.add(r["lemma"])
    _BLOOM_CACHE = (key, row["c"], bloom)
    return bloom


# ---------------------------------------------------------------------------
# Spec §3.4 tools
# ---------------------------------------------------------------------------

def is_known(lemma: str) -> dict[str, Any]:
    """{ known, strength, source, first_seen, srs_state? }"""
    conn = connect()
    try:
        row = conn.execute(
            "SELECT * FROM known_words WHERE lemma = ?", (lemma,)
        ).fetchone()
        if row is None:
            return {"known": False, "lemma": lemma}
        out: dict[str, Any] = {
            "known": True,
            "lemma": lemma,
            "strength": row["strength"],
            "source": row["source"],
            "first_seen": row["first_seen"],
            "encounter_count": row["encounter_count"],
        }
        if row["srs_note_id"] is not None:
            out["srs_state"] = _srs_state(row["srs_note_id"])
        return out
    finally:
        conn.close()


def _srs_state(note_id: int) -> dict[str, Any] | None:
    """Best-effort view of the note's SRS state (builtin backend only)."""
    from moguru.config import Config as _C

    config = _C.load(os.environ.get("MOGURU_CONFIG") or REPO_ROOT / "config.yaml")
    if not config.srs_db.exists() or config.srs_backend != "builtin":
        return None
    try:
        conn = sqlite3.connect(config.srs_db)
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT due, stability, reps, state, last_review FROM cards WHERE note_id = ?",
            (note_id,),
        ).fetchone()
        conn.close()
        if row is None:
            return None
        return {
            "due": row["due"],
            "stability": row["stability"],
            "reps": row["reps"],
            "state": row["state"],
            "last_review": row["last_review"],
        }
    except sqlite3.Error:
        return None


def get_known_set(filter_: dict[str, Any] | None = None) -> list[str]:
    conn = connect()
    try:
        sql = "SELECT lemma FROM known_words"
        params: list[Any] = []
        if filter_:
            clauses = []
            if "source" in filter_:
                clauses.append("source = ?")
                params.append(filter_["source"])
            if "min_strength" in filter_:
                clauses.append("strength >= ?")
                params.append(filter_["min_strength"])
            if clauses:
                sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY lemma"
        return [r["lemma"] for r in conn.execute(sql, params)]
    finally:
        conn.close()


def known_kanji() -> list[str]:
    conn = connect()
    try:
        return [r["char"] for r in conn.execute("SELECT char FROM known_kanji ORDER BY char")]
    finally:
        conn.close()


def mark_known(lemma: str, source: str, reading: str | None = None,
               srs_note_id: int | None = None) -> None:
    if source not in VALID_SOURCES:
        raise ValueError(f"source must be one of {sorted(VALID_SOURCES)}, got {source!r}")
    conn = connect()
    try:
        now = _now()
        conn.execute(
            """INSERT INTO known_words (lemma, reading, source, first_seen, last_seen, encounter_count, srs_note_id)
               VALUES (?,?,?,?,?,0,?)
               ON CONFLICT(lemma) DO UPDATE SET
                 last_seen = excluded.last_seen,
                 source = excluded.source,
                 reading = COALESCE(excluded.reading, reading),
                 srs_note_id = COALESCE(excluded.srs_note_id, srs_note_id)""",
            (lemma, reading, source, now, now, srs_note_id),
        )
        conn.commit()
    finally:
        conn.close()


def mark_kanji_known(char: str, source: str = "manual") -> None:
    conn = connect()
    try:
        conn.execute(
            """INSERT INTO known_kanji (char, source, first_seen)
               VALUES (?,?,?)
               ON CONFLICT(char) DO NOTHING""",
            (char, source, _now()),
        )
        conn.commit()
    finally:
        conn.close()


def record_encounter(lemma: str, context_sentence: str,
                     media_ref: str | None = None) -> None:
    """For +freq / maturity tracking."""
    conn = connect()
    try:
        now = _now()
        conn.execute(
            "INSERT INTO encounters (lemma, sentence, media_ref, ts) VALUES (?,?,?,?)",
            (lemma, context_sentence, media_ref, now),
        )
        conn.execute(
            """INSERT INTO known_words (lemma, source, first_seen, last_seen, encounter_count)
               VALUES (?, 'mined', ?, ?, 1)
               ON CONFLICT(lemma) DO UPDATE SET
                 encounter_count = encounter_count + 1,
                 last_seen = excluded.last_seen""",
            (lemma, now, now),
        )
        conn.commit()
    finally:
        conn.close()


def set_srs_note(lemma: str, note_id: int) -> None:
    conn = connect()
    try:
        conn.execute(
            "UPDATE known_words SET srs_note_id = ? WHERE lemma = ?", (note_id, lemma)
        )
        conn.commit()
    finally:
        conn.close()


def stats() -> dict[str, Any]:
    conn = connect()
    try:
        words = conn.execute("SELECT COUNT(*) c FROM known_words").fetchone()["c"]
        kanji = conn.execute("SELECT COUNT(*) c FROM known_kanji").fetchone()["c"]
        encounters = conn.execute("SELECT COUNT(*) c FROM encounters").fetchone()["c"]
        by_source = {
            r["source"]: r["c"]
            for r in conn.execute(
                "SELECT source, COUNT(*) c FROM known_words GROUP BY source"
            )
        }
        avg_strength = conn.execute(
            "SELECT AVG(strength) s FROM known_words"
        ).fetchone()["s"]
        return {
            "known_words": words,
            "known_kanji": kanji,
            "encounters": encounters,
            "by_source": by_source,
            "avg_strength": round(avg_strength, 3) if avg_strength is not None else 0.0,
        }
    finally:
        conn.close()
