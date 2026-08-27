"""dict-mcp importers — build dict.sqlite from EDRDG / kanjium / Yomitan data.

Sources (spec §3.2, §6):
  - JMdict_e.gz      (EDRDG, J-E primary dictionary)
  - JMnedict.xml.gz  (EDRDG, names)
  - kanjidic2.xml.gz (EDRDG, kanji)
  - kradzip.zip      (EDRDG, kradfile/radkfile radical decomposition)
  - accents.txt      (kanjium, pitch accent)
  - optional Yomitan-format J-J package dir  (monolingual slot)

All land in data/dictionaries/dict.sqlite (+ FTS5).
"""

from __future__ import annotations

import gzip
import io
import json
import re
import sqlite3
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

_ENT_RE = re.compile(r"&(\w+);")


def _sanitize_entities(xml_text: str) -> str:
    """Replace undefined DTD entities (&n; &uk; ...) with their names."""
    return _ENT_RE.sub(r"\1", xml_text)


def _iter_xml(path: Path, root_tag: str, tag: str, gzipped: bool = True):
    """Stream-parse a (gzipped) XML file, yielding elements of `tag`."""
    opener = gzip.open if gzipped else open
    with opener(path, "rb") as f:  # type: ignore[operator]
        text = f.read().decode("utf-8")
    text = _sanitize_entities(text)
    # iterparse on a string
    context = ET.iterparse(io.StringIO(text), events=("end",))
    for _event, elem in context:
        if elem.tag == tag:
            yield elem
            elem.clear()
        elif elem.tag == root_tag and _event == "end":
            break


def _txt(elem: ET.Element | None) -> str:
    return elem.text.strip() if elem is not None and elem.text else ""


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

SCHEMA = """
CREATE TABLE IF NOT EXISTS jmdict (
  id INTEGER PRIMARY KEY,
  kanji TEXT NOT NULL,   -- JSON [keb...]
  kana TEXT NOT NULL,    -- JSON [reb...]
  senses TEXT NOT NULL   -- JSON [{gloss[], pos[], misc[], field[]}]
);
CREATE TABLE IF NOT EXISTS jmdict_keys (
  key TEXT, reading TEXT, ent_seq INTEGER
);
CREATE INDEX IF NOT EXISTS idx_jmdict_keys ON jmdict_keys(key);
CREATE VIRTUAL TABLE IF NOT EXISTS jmdict_fts USING fts5(text, ent_seq UNINDEXED);

CREATE TABLE IF NOT EXISTS jmnedict (
  id INTEGER PRIMARY KEY,
  kanji TEXT NOT NULL,
  kana TEXT NOT NULL,
  gloss TEXT NOT NULL    -- JSON [translation strings]
);
CREATE TABLE IF NOT EXISTS jmnedict_keys (key TEXT, ent_seq INTEGER);
CREATE INDEX IF NOT EXISTS idx_jmnedict_keys ON jmnedict_keys(key);

CREATE TABLE IF NOT EXISTS kanji (
  char TEXT PRIMARY KEY,
  on_readings TEXT, kun_readings TEXT, meanings TEXT, nanori TEXT,
  stroke_count INTEGER, grade INTEGER, jlpt INTEGER, freq_rank INTEGER
);
CREATE TABLE IF NOT EXISTS kanji_radicals (char TEXT, radical TEXT);
CREATE INDEX IF NOT EXISTS idx_kanji_radicals ON kanji_radicals(char);
CREATE TABLE IF NOT EXISTS krad (char TEXT PRIMARY KEY, components TEXT);
CREATE TABLE IF NOT EXISTS radk (radical TEXT PRIMARY KEY, kanji TEXT);

CREATE TABLE IF NOT EXISTS pitch (
  headword TEXT, reading TEXT, accents TEXT,
  PRIMARY KEY (headword, reading)
);

-- Monolingual (J-J) slot: populated only when the user drops a Yomitan-format
-- J-J package in data/dictionaries/jj/ and runs the importer.
CREATE TABLE IF NOT EXISTS jj_entries (
  id INTEGER PRIMARY KEY,
  headword TEXT, reading TEXT, definition TEXT
);
CREATE INDEX IF NOT EXISTS idx_jj_headword ON jj_entries(headword);

CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
"""


def connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.executescript(SCHEMA)
    return conn


def reset(conn: sqlite3.Connection) -> None:
    """Drop and recreate the EDRDG-derived tables so builds are idempotent.
    (jj_entries — user-imported J-J — is intentionally preserved.)"""
    conn.executescript(
        """
        DROP TABLE IF EXISTS jmdict;
        DROP TABLE IF EXISTS jmdict_keys;
        DROP TABLE IF EXISTS jmdict_fts;
        DROP TABLE IF EXISTS jmnedict;
        DROP TABLE IF EXISTS jmnedict_keys;
        DROP TABLE IF EXISTS kanji;
        DROP TABLE IF EXISTS kanji_radicals;
        DROP TABLE IF EXISTS krad;
        DROP TABLE IF EXISTS radk;
        DROP TABLE IF EXISTS pitch;
        """
    )
    conn.executescript(SCHEMA)
    conn.commit()


# ---------------------------------------------------------------------------
# JMdict
# ---------------------------------------------------------------------------

def build_jmdict(conn: sqlite3.Connection, gz_path: Path) -> int:
    entries: list[tuple[int, str, str, str]] = []
    keys: list[tuple[str, str, int]] = []
    fts_rows: list[tuple[str, int]] = []

    for entry in _iter_xml(gz_path, "JMdict", "entry"):
        ent_seq = int(_txt(entry.find("ent_seq")))
        keb_list = [(_txt(k.find("keb")) or "") for k in entry.findall("k_ele")]
        kanji = [k for k in keb_list if k]
        kana: list[str] = []
        for r in entry.findall("r_ele"):
            reb = _txt(r.find("reb"))
            if reb:
                kana.append(reb)
        senses = []
        for s in entry.findall("sense"):
            glosses = [g.text.strip() for g in s.findall("gloss") if g.text]
            if not glosses:
                continue
            senses.append(
                {
                    "gloss": glosses,
                    "pos": [p.text.strip() for p in s.findall("pos") if p.text],
                    "misc": [m.text.strip() for m in s.findall("misc") if m.text],
                    "field": [fl.text.strip() for fl in s.findall("field") if fl.text],
                }
            )
        entries.append((ent_seq, json.dumps(kanji, ensure_ascii=False),
                        json.dumps(kana, ensure_ascii=False),
                        json.dumps(senses, ensure_ascii=False)))
        for k in kanji:
            keys.append((k, kana[0] if kana else None, ent_seq))
        for k in kana:
            keys.append((k, k, ent_seq))
        flat = " ".join(g for s in senses for g in s["gloss"])
        fts_rows.append(
            (" ".join(kanji + kana) + " " + flat, ent_seq)
        )

    conn.executemany("INSERT OR REPLACE INTO jmdict VALUES (?,?,?,?)", entries)
    conn.executemany("INSERT INTO jmdict_keys VALUES (?,?,?)", keys)
    conn.executemany("INSERT INTO jmdict_fts(rowid, text, ent_seq) VALUES (?,?,?)",
                     [(i + 1, t, e) for i, (t, e) in enumerate(fts_rows)])
    conn.commit()
    return len(entries)


# ---------------------------------------------------------------------------
# JMnedict
# ---------------------------------------------------------------------------

def build_jmnedict(conn: sqlite3.Connection, gz_path: Path) -> int:
    entries: list[tuple[int, str, str, str]] = []
    keys: list[tuple[str, int]] = []
    for entry in _iter_xml(gz_path, "JMnedict", "entry"):
        ent_seq = int(_txt(entry.find("ent_seq")))
        kanji = [_txt(k.find("keb")) for k in entry.findall("k_ele")]
        kanji = [k for k in kanji if k]
        kana = [_txt(r.find("reb")) for r in entry.findall("r_ele")]
        kana = [k for k in kana if k]
        gloss: list[str] = []
        for trans in entry.findall("trans"):
            for tm in trans.findall("trans_det"):
                if tm.text:
                    gloss.append(tm.text.strip())
        entries.append((ent_seq,
                        json.dumps(kanji, ensure_ascii=False),
                        json.dumps(kana, ensure_ascii=False),
                        json.dumps(gloss, ensure_ascii=False)))
        for k in kanji + kana:
            keys.append((k, ent_seq))
    conn.executemany("INSERT OR REPLACE INTO jmnedict VALUES (?,?,?,?)", entries)
    conn.executemany("INSERT INTO jmnedict_keys VALUES (?,?)", keys)
    conn.commit()
    return len(entries)


# ---------------------------------------------------------------------------
# KANJIDIC2
# ---------------------------------------------------------------------------

def build_kanjidic2(conn: sqlite3.Connection, gz_path: Path) -> int:
    rows: list[tuple] = []
    rad_rows: list[tuple[str, str]] = []
    for ch in _iter_xml(gz_path, "kanjidic2", "character"):
        char = _txt(ch.find("literal"))
        misc = ch.find("misc")
        grade = _txt(misc.find("grade")) if misc is not None else ""
        strokes = _txt(misc.find("stroke_count")) if misc is not None else ""
        freq = _txt(misc.find("freq")) if misc is not None else ""
        jlpt = _txt(misc.find("jlpt")) if misc is not None else ""
        on_read: list[str] = []
        kun_read: list[str] = []
        nanori: list[str] = []
        meanings: list[str] = []
        rm = ch.find("reading_meaning")
        if rm is not None:
            # NOTE: kanjidic2's element is <rmgroup> — no underscore.
            for group in rm.findall("rmgroup"):
                for r in group.findall("reading"):
                    rt = r.get("r_type", "")
                    val = _txt(r)
                    if not val:
                        continue
                    if rt == "ja_on":
                        on_read.append(val)
                    elif rt == "ja_kun":
                        kun_read.append(val)
                for m in group.findall("meaning"):
                    # English meanings only (m_language absent/="en")
                    if m.get("m_language", "en") in ("en", None, ""):
                        t = _txt(m)
                        if t:
                            meanings.append(t)
            for n in rm.findall("nanori"):
                val = _txt(n)
                if val:
                    nanori.append(val)
        radical_el = ch.find("radical")
        if radical_el is not None:
            for rv in radical_el.findall("rad_value"):
                if rv.get("rad_class") == "classical":
                    val = _txt(rv)
                    if val:
                        rad_rows.append((char, val))
        rows.append((
            char,
            json.dumps(on_read, ensure_ascii=False),
            json.dumps(kun_read, ensure_ascii=False),
            json.dumps(meanings, ensure_ascii=False),
            json.dumps(nanori, ensure_ascii=False),
            int(strokes) if strokes.isdigit() else None,
            int(grade) if grade.isdigit() else None,
            int(jlpt) if jlpt.isdigit() else None,
            int(freq) if freq.isdigit() else None,
        ))
    conn.executemany("INSERT OR REPLACE INTO kanji VALUES (?,?,?,?,?,?,?,?,?)", rows)
    conn.executemany("INSERT OR REPLACE INTO kanji_radicals VALUES (?,?)", rad_rows)
    conn.commit()
    return len(rows)


# ---------------------------------------------------------------------------
# KRADFILE / RADKFILE (radical decomposition)
# ---------------------------------------------------------------------------

def build_krad(conn: sqlite3.Connection, zip_path: Path) -> int:
    krad: dict[str, list[str]] = {}
    radk: dict[str, list[str]] = {}
    current_rad: str | None = None
    with zipfile.ZipFile(zip_path) as zf:
        for name in zf.namelist():
            base = Path(name).name
            if base not in ("kradfile", "kradfile2", "radkfile", "radkfile2"):
                continue
            with zf.open(name) as f:
                for raw_line in io.TextIOWrapper(f, encoding="euc-jp"):
                    line = raw_line.strip()
                    if not line or line.startswith("#"):
                        continue
                    if line.startswith("$"):  # radkfile radical header
                        current_rad = line.split()[1]
                        radk.setdefault(current_rad, [])
                        continue
                    if ":" in line:
                        char, rest = line.split(":", 1)
                        comps = rest.split()
                        krad.setdefault(char.strip(), []).extend(comps)
                    elif current_rad is not None:
                        for c in line:
                            radk[current_rad].append(c)
    conn.executemany(
        "INSERT OR REPLACE INTO krad VALUES (?,?)",
        [(c, json.dumps(sorted(set(v)), ensure_ascii=False)) for c, v in krad.items()],
    )
    conn.executemany(
        "INSERT OR REPLACE INTO radk VALUES (?,?)",
        [(r, json.dumps(sorted(set(v)), ensure_ascii=False)) for r, v in radk.items()],
    )
    conn.commit()
    return len(krad)


# ---------------------------------------------------------------------------
# kanjium pitch accents
# ---------------------------------------------------------------------------

def build_pitch(conn: sqlite3.Connection, txt_path: Path) -> int:
    rows: list[tuple[str, str, str]] = []
    with open(txt_path, encoding="utf-8") as f:
        for line in f:
            parts = line.rstrip("\n").split("\t")
            if len(parts) >= 3:
                headword, reading, accents = parts[0], parts[1], parts[2]
                rows.append((headword, reading, accents))
    conn.executemany("INSERT OR REPLACE INTO pitch VALUES (?,?,?)", rows)
    conn.commit()
    return len(rows)


# ---------------------------------------------------------------------------
# Optional monolingual (J-J) Yomitan package
# ---------------------------------------------------------------------------

def build_monolingual(conn: sqlite3.Connection, package_dir: Path) -> int:
    """Import a Yomitan-format J-J dictionary package (term_bank_*.json).

    Rows: [expression, reading, defTags, ruleDeinflectTags, popularity,
           sequence, glossary[], ...]. We keep expression/reading and the
    glossary rendered as plain text (Yomitan structured content is flattened).
    """
    count = 0
    rows: list[tuple[int, str, str, str]] = []
    banks = sorted(package_dir.glob("term_bank_*.json"))
    if not banks:
        raise FileNotFoundError(
            f"no term_bank_*.json found in {package_dir} — drop a Yomitan-format "
            "J-J dictionary package there and re-run"
        )
    for bank in banks:
        for row in json.loads(bank.read_text(encoding="utf-8")):
            expression, reading = row[0], row[1]
            gloss = row[5] if len(row) > 5 else []
            text_parts: list[str] = []
            if isinstance(gloss, list):
                for g in gloss:
                    if isinstance(g, str):
                        text_parts.append(g)
                    elif isinstance(g, dict):
                        text_parts.append(g.get("text") or g.get("content") or "")
            elif isinstance(gloss, str):
                text_parts.append(gloss)
            definition = "\n".join(p for p in text_parts if p)
            if expression and definition:
                rows.append((count, expression, reading or "", definition))
                count += 1
    conn.executemany(
        "INSERT OR REPLACE INTO jj_entries VALUES (?,?,?,?)", rows
    )
    conn.commit()
    return count
