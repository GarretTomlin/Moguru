"""Data pipeline — download + build all dictionaries (spec §6).

`moguru data download` fetches archives into data/dictionaries/.
`moguru data build` parses them into dict.sqlite / freq.sqlite.
"""

from __future__ import annotations

from pathlib import Path

import requests

from moguru.config import Config

EDRDG = "https://www.edrdg.org/pub/Nihongo"

EDRDG_FILES = {
    "JMdict_e.gz": f"{EDRDG}/JMdict_e.gz",
    "JMnedict.xml.gz": f"{EDRDG}/JMnedict.xml.gz",
    "kanjidic2.xml.gz": f"{EDRDG}/kanjidic2.xml.gz",
    "kradzip.zip": f"{EDRDG}/kradzip.zip",
}

KANJIUM_URL = (
    "https://raw.githubusercontent.com/mifunetoshiro/kanjium/master/"
    "data/source_files/raw/accents.txt"
)

YOMITAN_PERMALINK = (
    "https://github.com/Kuuuube/yomitan-dictionaries/releases/download/yomitan-permalink"
)
FREQ_ZIPS = {
    "JPDB_v2.2_Frequency_Kana.zip": f"{YOMITAN_PERMALINK}/JPDB_v2.2_Frequency_Kana.zip",
    "BCCWJ_SUW_LUW_combined.zip": f"{YOMITAN_PERMALINK}/BCCWJ_SUW_LUW_combined.zip",
}


def download(config: Config | None = None) -> None:
    config = config or Config.load()
    target = config.dictionaries_dir
    target.mkdir(parents=True, exist_ok=True)
    jobs = {**EDRDG_FILES, "accents.txt": KANJIUM_URL, **FREQ_ZIPS}
    for name, url in jobs.items():
        dest = target / name
        if dest.exists() and dest.stat().st_size > 0:
            print(f"  [skip] {name} (exists)")
            continue
        print(f"  [get ] {name} <- {url}")
        with requests.get(url, stream=True, timeout=120) as r:
            r.raise_for_status()
            with open(dest, "wb") as f:
                for chunk in r.iter_content(chunk_size=1 << 20):
                    f.write(chunk)
    print("downloads complete.")


def build(config: Config | None = None) -> None:
    from moguru.mcp.dict_mcp import importers as dimp
    from moguru.mcp.freq_mcp import core as fcore

    config = config or Config.load()
    d = config.dictionaries_dir
    conn = dimp.connect(config.dict_db)
    dimp.reset(conn)

    print("building JMdict (J-E)…")
    n = dimp.build_jmdict(conn, d / "JMdict_e.gz")
    print(f"  {n} entries")
    print("building JMnedict (names)…")
    n = dimp.build_jmnedict(conn, d / "JMnedict.xml.gz")
    print(f"  {n} entries")
    print("building KANJIDIC2…")
    n = dimp.build_kanjidic2(conn, d / "kanjidic2.xml.gz")
    print(f"  {n} kanji")
    print("building krad/radk (decomposition)…")
    n = dimp.build_krad(conn, d / "kradzip.zip")
    print(f"  {n} kanji decompositions")
    print("building kanjium pitch accents…")
    n = dimp.build_pitch(conn, d / "accents.txt")
    print(f"  {n} accent entries")
    conn.execute(
        "INSERT OR REPLACE INTO meta VALUES ('built_at', datetime('now'))"
    )
    conn.commit()
    conn.close()

    print("building JPDB v2.2 frequency…")
    n = fcore.build_from_yomitan_zip(
        config.freq_db, d / "JPDB_v2.2_Frequency_Kana.zip", "jpdb"
    )
    print(f"  {n} headwords")
    bccwj_zip = d / "BCCWJ_SUW_LUW_combined.zip"
    if bccwj_zip.exists():
        print("building BCCWJ frequency…")
        n = fcore.build_from_yomitan_zip(config.freq_db, bccwj_zip, "bccwj")
        print(f"  {n} headwords")

    # Optional J-J package
    jj_dir = d / "jj"
    if jj_dir.exists() and any(jj_dir.glob("term_bank_*.json")):
        print("building monolingual (J-J) package…")
        conn = dimp.connect(config.dict_db)
        n = dimp.build_monolingual(conn, jj_dir)
        print(f"  {n} entries")
        conn.close()
    print("build complete.")


def build_monolingual(config: Config | None = None) -> None:
    """Import a Yomitan-format J-J package from data/dictionaries/jj/."""
    from moguru.mcp.dict_mcp import importers as dimp

    config = config or Config.load()
    jj_dir = config.dictionaries_dir / "jj"
    conn = dimp.connect(config.dict_db)
    try:
        n = dimp.build_monolingual(conn, jj_dir)
        print(f"imported {n} J-J entries from {jj_dir}")
    finally:
        conn.close()
