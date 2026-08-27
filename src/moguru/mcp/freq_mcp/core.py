"""freq-mcp — frequency data (spec §3.3).

Sources: JPDB v2 (Yomitan term-meta banks) and optional BCCWJ-derived lists,
all landing in data/dictionaries/freq.sqlite. Learn-frequent-first ordering is
driven here.
"""

from __future__ import annotations

import json
import math
import os
import sqlite3
import zipfile
from pathlib import Path
from typing import Any

from moguru.config import Config, REPO_ROOT

SCHEMA = """
CREATE TABLE IF NOT EXISTS freq (
  headword TEXT, source TEXT, rank INTEGER, freq_class INTEGER,
  PRIMARY KEY (headword, source)
);
CREATE INDEX IF NOT EXISTS idx_freq_headword ON freq(headword);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
"""


def connect_ro(config: Config | None = None) -> sqlite3.Connection:
    if config is None:
        config = Config.load(os.environ.get("MOGURU_CONFIG") or REPO_ROOT / "config.yaml")
    if not config.freq_db.exists():
        raise FileNotFoundError(
            f"frequency database not found at {config.freq_db} — "
            "run `moguru data build` first"
        )
    conn = sqlite3.connect(f"file:{config.freq_db}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _freq_class(rank: int) -> int:
    """JPDB-style frequency class derived from rank: class N spans ranks
    [2^(N-2), 2^(N-1)) — each class is ~half as common as the previous."""
    return max(1, int(math.floor(math.log2(max(rank, 1)))) + 1)


# ---------------------------------------------------------------------------
# Importer (Yomitan term_meta_bank format: [term, "freq", {value: rank}])
# ---------------------------------------------------------------------------

def _iter_freq_rows(obj):
    """Yield flat [term, "freq", payload] rows, tolerating banks that wrap
    rows in an extra array level (BCCWJ combined format)."""
    for row in obj:
        if row and isinstance(row[0], list):
            yield from _iter_freq_rows(row)
        else:
            yield row


def build_from_yomitan_zip(db_path: Path, zip_path: Path, source: str) -> int:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.executescript(SCHEMA)
    count = 0
    batch: list[tuple[str, str, int, int]] = []
    with zipfile.ZipFile(zip_path) as zf:
        banks = sorted(n for n in zf.namelist()
                       if Path(n).name.startswith("term_meta_bank")
                       and n.endswith(".json"))
        for bank in banks:
            with zf.open(bank) as f:
                rows = json.load(f)
            for row in _iter_freq_rows(rows):
                if len(row) < 3 or row[1] != "freq":
                    continue
                term = row[0]
                payload = row[2] if isinstance(row[2], dict) else {}
                value = payload.get("value", payload.get("frequency"))
                if not isinstance(value, (int, float)) or isinstance(value, bool):
                    continue
                rank = int(value)
                batch.append((term, source, rank, _freq_class(rank)))
                count += 1
                if len(batch) >= 50_000:
                    conn.executemany(
                        "INSERT INTO freq VALUES (?,?,?,?) "
                        "ON CONFLICT(headword, source) DO UPDATE SET "
                        "rank = MIN(rank, excluded.rank), "
                        "freq_class = MIN(freq_class, excluded.freq_class)",
                        batch,
                    )
                    batch = []
    if batch:
        conn.executemany(
            "INSERT INTO freq VALUES (?,?,?,?) "
            "ON CONFLICT(headword, source) DO UPDATE SET "
            "rank = MIN(rank, excluded.rank), "
            "freq_class = MIN(freq_class, excluded.freq_class)",
            batch,
        )
    conn.execute(
        "INSERT OR REPLACE INTO meta VALUES (?,?)",
        (f"{source}_built", str(db_path.stat().st_mtime)),
    )
    conn.commit()
    conn.close()
    return count


# ---------------------------------------------------------------------------
# Spec §3.3 tools
# ---------------------------------------------------------------------------

def _kana_keys(lemma: str, reading: str | None = None) -> list[str]:
    """JPDB's 'Kana' list is keyed by readings; resolve a lemma's kana form
    via the parser so kanji lemmas match (食べる -> たべる)."""
    keys = [lemma]
    if reading:
        keys.append(reading)
    try:
        from moguru.mcp.parser_mcp import core as parser_core

        kana = parser_core.to_reading(lemma, "hiragana")
        if kana and kana not in keys:
            keys.append(kana)
    except Exception:
        pass
    return keys


def frequency(lemma: str, reading: str | None = None) -> dict[str, Any]:
    """{ bccwj_rank?, jpdb_rank?, jpdb_freq_class? } — optional when absent."""
    conn = connect_ro()
    try:
        out: dict[str, Any] = {}

        def best(source: str, keys: list[str]) -> int | None:
            for k in keys:
                if not k:
                    continue
                row = conn.execute(
                    "SELECT MIN(rank) r FROM freq WHERE headword = ? AND source = ?",
                    (k, source),
                ).fetchone()
                if row and row["r"] is not None:
                    return int(row["r"])
            return None

        keys = _kana_keys(lemma, reading)
        jpdb = best("jpdb", keys)
        if jpdb is not None:
            out["jpdb_rank"] = jpdb
            out["jpdb_freq_class"] = _freq_class(jpdb)
        bccwj = best("bccwj", keys)
        if bccwj is not None:
            out["bccwj_rank"] = bccwj
        return out
    finally:
        conn.close()


def rank_by_frequency(lemmas: list[str]) -> list[dict[str, Any]]:
    """[{lemma, jpdb_rank}] sorted ascending (frequent first)."""
    conn = connect_ro()
    try:
        out = []
        for lemma in lemmas:
            rank: int | None = None
            for k in _kana_keys(lemma):
                row = conn.execute(
                    "SELECT MIN(rank) r FROM freq WHERE headword = ? AND source = 'jpdb'",
                    (k,),
                ).fetchone()
                if row and row["r"] is not None:
                    rank = int(row["r"])
                    break
            out.append({"lemma": lemma, "jpdb_rank": rank})
        out.sort(key=lambda e: (e["jpdb_rank"] is None,
                                e["jpdb_rank"] if e["jpdb_rank"] is not None else 0))
        return out
    finally:
        conn.close()
