"""srs-mcp core — spaced repetition (spec §3.5).

Two interchangeable backends selected by `srs.backend`:
  - builtin : FSRS scheduler over data/user/srs.sqlite (always available)
  - anki    : AnkiConnect HTTP bridge (localhost:8765)
  - none    : no-op
"""

from __future__ import annotations

import json
import os
import sqlite3
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Any

import requests

from moguru.config import Config, REPO_ROOT

SCHEMA = """
CREATE TABLE IF NOT EXISTS notes (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  guid TEXT UNIQUE,
  deck TEXT, note_type TEXT,
  fields TEXT,           -- JSON object
  tags TEXT,             -- JSON array
  created TEXT
);
CREATE TABLE IF NOT EXISTS cards (
  note_id INTEGER PRIMARY KEY,
  deck TEXT,
  card_json TEXT,        -- fsrs Card.to_dict()
  due TEXT,              -- ISO datetime (denormalized for fast queries)
  created TEXT
);
CREATE TABLE IF NOT EXISTS review_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  note_id INTEGER, rating INTEGER, ts TEXT, card_json TEXT
);
CREATE INDEX IF NOT EXISTS idx_notes_deck ON notes(deck);
"""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


class SRSBackend(ABC):
    @abstractmethod
    def add_card(self, deck: str, note_type: str, fields: dict[str, str],
                 tags: list[str]) -> int: ...
    @abstractmethod
    def find_notes(self, query: str) -> list[int]: ...
    @abstractmethod
    def update_note(self, note_id: int, fields: dict[str, str]) -> None: ...
    @abstractmethod
    def due_cards(self, deck: str) -> list[dict[str, Any]]: ...
    @abstractmethod
    def review_note(self, note_id: int, rating: str) -> dict[str, Any]: ...
    @abstractmethod
    def import_known(self) -> list[str]: ...


class NullBackend(SRSBackend):
    def add_card(self, deck, note_type, fields, tags) -> int:
        return -1

    def find_notes(self, query) -> list[int]:
        return []

    def update_note(self, note_id, fields) -> None:
        pass

    def due_cards(self, deck) -> list[dict[str, Any]]:
        return []

    def review_note(self, note_id, rating) -> dict[str, Any]:
        return {"reviewed": False, "reason": "srs.backend is 'none'"}

    def import_known(self) -> list[str]:
        return []


# ---------------------------------------------------------------------------
# Builtin FSRS backend
# ---------------------------------------------------------------------------

class BuiltinFSRSBackend(SRSBackend):
    def __init__(self, config: Config):
        from fsrs import Scheduler

        self.config = config
        config.user_dir.mkdir(parents=True, exist_ok=True)
        self.scheduler = Scheduler()
        self.conn = sqlite3.connect(config.srs_db)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)

    # -- helpers ---------------------------------------------------------
    def _note_row(self, note_id: int) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT * FROM notes WHERE id = ?", (note_id,)
        ).fetchone()

    def _card(self, note_id: int):
        from fsrs import Card

        row = self.conn.execute(
            "SELECT card_json FROM cards WHERE note_id = ?", (note_id,)
        ).fetchone()
        if row is None:
            return None
        return Card.from_dict(json.loads(row["card_json"]))

    def _save_card(self, note_id: int, deck: str, card) -> None:
        self.conn.execute(
            """INSERT INTO cards (note_id, deck, card_json, due, created)
               VALUES (?,?,?,?,?)
               ON CONFLICT(note_id) DO UPDATE SET card_json = excluded.card_json,
                 due = excluded.due""",
            (note_id, deck, json.dumps(card.to_dict()), _iso(card.due), _now().isoformat()),
        )
        self.conn.commit()

    # -- spec §3.5 ---------------------------------------------------------
    def add_card(self, deck: str, note_type: str, fields: dict[str, str],
                 tags: list[str]) -> int:
        import uuid

        cur = self.conn.execute(
            """INSERT INTO notes (guid, deck, note_type, fields, tags, created)
               VALUES (?,?,?,?,?,?)""",
            (uuid.uuid4().hex, deck, note_type,
             json.dumps(fields, ensure_ascii=False),
             json.dumps(tags, ensure_ascii=False),
             _now().isoformat()),
        )
        note_id = int(cur.lastrowid)
        from fsrs import Card

        card = Card()
        self._save_card(note_id, deck, card)
        return note_id

    def find_notes(self, query: str) -> list[int]:
        """Minimal query syntax: `deck:NAME`, `tag:X`, `field:term`,
        or a bare term matched against all field values."""
        rows = self.conn.execute("SELECT * FROM notes").fetchall()
        out = []
        for r in rows:
            fields = json.loads(r["fields"])
            tags = json.loads(r["tags"])
            q = query.strip()
            if not q:
                out.append(r["id"])
                continue
            if q.startswith("deck:"):
                if r["deck"] == q[5:]:
                    out.append(r["id"])
            elif q.startswith("tag:"):
                if q[4:] in tags:
                    out.append(r["id"])
            elif ":" in q:
                field, term = q.split(":", 1)
                if fields.get(field, "") == term:
                    out.append(r["id"])
            else:
                if any(q in v for v in fields.values()):
                    out.append(r["id"])
        return out

    def update_note(self, note_id: int, fields: dict[str, str]) -> None:
        row = self._note_row(note_id)
        if row is None:
            raise KeyError(f"no note {note_id}")
        merged = json.loads(row["fields"])
        merged.update(fields)
        self.conn.execute(
            "UPDATE notes SET fields = ? WHERE id = ?",
            (json.dumps(merged, ensure_ascii=False), note_id),
        )
        self.conn.commit()

    def due_cards(self, deck: str) -> list[dict[str, Any]]:
        now_s = _iso(_now())
        rows = self.conn.execute(
            """SELECT n.id, n.deck, n.note_type, n.fields, n.tags, c.due, c.card_json
               FROM notes n JOIN cards c ON c.note_id = n.id
               WHERE (n.deck = ? OR ? = '') AND c.due <= ?
               ORDER BY c.due ASC""",
            (deck, deck, now_s),
        ).fetchall()
        return [self._row_to_card(r) for r in rows]

    def _row_to_card(self, r: sqlite3.Row) -> dict[str, Any]:
        return {
            "note_id": r["id"],
            "deck": r["deck"],
            "note_type": r["note_type"],
            "fields": json.loads(r["fields"]),
            "tags": json.loads(r["tags"]),
            "due": r["due"],
        }

    def all_cards(self, deck: str = "") -> list[dict[str, Any]]:
        rows = self.conn.execute(
            """SELECT n.id, n.deck, n.note_type, n.fields, n.tags, c.due, c.card_json
               FROM notes n JOIN cards c ON c.note_id = n.id
               WHERE (n.deck = ? OR ? = '')
               ORDER BY c.due ASC""",
            (deck, deck),
        ).fetchall()
        return [self._row_to_card(r) for r in rows]

    def review_note(self, note_id: int, rating: str) -> dict[str, Any]:
        from fsrs import Rating

        ratings = {
            "again": Rating.Again,
            "hard": Rating.Hard,
            "good": Rating.Good,
            "easy": Rating.Easy,
        }
        if rating not in ratings:
            raise ValueError(f"rating must be one of {list(ratings)}, got {rating!r}")
        card = self._card(note_id)
        if card is None:
            raise KeyError(f"no card for note {note_id}")
        new_card, review_log = self.scheduler.review_card(card, ratings[rating])
        deck = self._note_row(note_id)["deck"]
        self._save_card(note_id, deck, new_card)
        self.conn.execute(
            "INSERT INTO review_log (note_id, rating, ts, card_json) VALUES (?,?,?,?)",
            (note_id, int(ratings[rating].value), _now().isoformat(),
             json.dumps(new_card.to_dict())),
        )
        self.conn.commit()
        return {
            "note_id": note_id,
            "rating": rating,
            "next_due": _iso(new_card.due),
            "stability": new_card.stability,
            "state": new_card.state.name if hasattr(new_card.state, "name") else str(new_card.state),
        }

    def import_known(self) -> list[str]:
        """Mature cards (>= mature_interval_days stability-scheduled interval)
        contribute their target-word lemmas."""
        threshold_days = self.config.anki_mature_interval_days
        now = _now()
        lemmas: list[str] = []
        for r in self.conn.execute(
            "SELECT n.fields, c.due FROM notes n JOIN cards c ON c.note_id = n.id"
        ).fetchall():
            fields = json.loads(r["fields"])
            lemma = fields.get("TargetWord") or fields.get("target") or ""
            if not lemma:
                continue
            try:
                due = datetime.fromisoformat(r["due"])
                interval_days = (due - now).total_seconds() / 86400
                # A card scheduled far into the future implies a mature interval
                if interval_days >= threshold_days:
                    lemmas.append(lemma)
            except ValueError:
                continue
        return lemmas


# ---------------------------------------------------------------------------
# AnkiConnect backend
# ---------------------------------------------------------------------------

class AnkiBackend(SRSBackend):
    # Canonical note type per the card-format skill (spec §4.5).
    TARGET_WORD_FIELDS = [
        "Sentence", "TargetWord", "Reading", "Definition", "Audio", "Image",
        "PitchAccent", "Source",
    ]
    MODEL_CSS = (
        ".card { font-family: 'Hiragino Sans', sans-serif; font-size: 22px; "
        "text-align: center; color: #222; background: white; } "
        ".target { font-size: 34px; } .reading { color: #666; } "
        ".def { font-size: 18px; margin-top: 8px; } "
        ".meta { font-size: 13px; color: #999; margin-top: 12px; }"
    )
    MODEL_TEMPLATES = [{
        "Name": "Card 1",
        "Front": "<div class='target'>{{TargetWord}}</div>",
        "Back": (
            "{{FrontSide}}<hr id=answer><div class='reading'>{{Reading}} "
            "{{PitchAccent}}</div><div class='def'>{{Definition}}</div>"
            "<div class='meta'>{{Sentence}} — {{Source}}</div>"
        ),
    }]

    def __init__(self, config: Config):
        self.config = config
        self.url = config.anki_connect_url
        self._model_ready = False

    def _invoke(self, action: str, **params) -> Any:
        resp = requests.post(
            self.url,
            json={"action": action, "version": 6, "params": params},
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        if data.get("error"):
            raise RuntimeError(f"AnkiConnect error: {data['error']}")
        return data.get("result")

    def _ensure_model(self) -> None:
        """Create the canonical target-word note type if Anki doesn't have it."""
        if self._model_ready:
            return
        names = self._invoke("modelNames")
        if "target-word" not in names:
            self._invoke(
                "createModel",
                modelName="target-word",
                inOrderFields=self.TARGET_WORD_FIELDS,
                css=self.MODEL_CSS,
                isCloze=False,
                cardTemplates=self.MODEL_TEMPLATES,
            )
        self._model_ready = True

    def add_card(self, deck: str, note_type: str, fields: dict[str, str],
                 tags: list[str]) -> int:
        self._ensure_model()
        self._invoke("createDeck", deck=deck)
        return int(
            self._invoke(
                "addNote",
                note={
                    "deckName": deck,
                    "modelName": note_type,
                    "fields": fields,
                    "tags": tags,
                    "options": {"allowDuplicate": False},
                },
            )
        )

    def find_notes(self, query: str) -> list[int]:
        return [int(n) for n in self._invoke("findNotes", query=query)]

    def update_note(self, note_id: int, fields: dict[str, str]) -> None:
        self._invoke("updateNoteFields", note={"id": note_id, "fields": fields})

    def due_cards(self, deck: str) -> list[dict[str, Any]]:
        query = f'"deck:{deck}" is:due'
        card_ids = self._invoke("findCards", query=query)
        if not card_ids:
            return []
        infos = self._invoke("cardsInfo", cards=[int(c) for c in card_ids])
        out = []
        for ci in infos:
            out.append(
                {
                    "note_id": ci["note"],
                    "deck": ci.get("deckName", deck),
                    "note_type": ci.get("modelName", ""),
                    "fields": {
                        k: v.get("value", "")
                        for k, v in (ci.get("fields") or {}).items()
                    },
                    "tags": ci.get("tags", []),
                    "due": str(ci.get("due", "")),
                }
            )
        return out

    def review_note(self, note_id: int, rating: str) -> dict[str, Any]:
        raise RuntimeError(
            "reviewing Anki cards programmatically is not supported — review "
            "inside Anki (its scheduler owns the timing)"
        )

    def import_known(self) -> list[str]:
        """Read mature cards' target-word fields (spec §3.5)."""
        interval = self.config.anki_mature_interval_days
        note_ids = self._invoke("findNotes", query=f'"prop:ivl>={interval}"')
        if not note_ids:
            return []
        infos = self._invoke("notesInfo", notes=[int(n) for n in note_ids])
        lemmas = []
        for ni in infos:
            fields = {k: v.get("value", "") for k, v in (ni.get("fields") or {}).items()}
            lemma = fields.get("TargetWord") or ni.get("sortField") or ""
            if lemma:
                lemmas.append(lemma)
        return lemmas


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

_BACKEND: SRSBackend | None = None
_BACKEND_KEY: tuple[str, str] | None = None


def get_backend(config: Config | None = None) -> SRSBackend:
    global _BACKEND, _BACKEND_KEY
    if config is None:
        config = Config.load(os.environ.get("MOGURU_CONFIG") or REPO_ROOT / "config.yaml")
    key = (config.srs_backend, str(config.srs_db))
    if _BACKEND is not None and _BACKEND_KEY == key:
        return _BACKEND
    if config.srs_backend == "builtin":
        _BACKEND = BuiltinFSRSBackend(config)
    elif config.srs_backend == "anki":
        _BACKEND = AnkiBackend(config)
    elif config.srs_backend == "none":
        _BACKEND = NullBackend()
    else:
        raise ValueError(f"unknown srs.backend: {config.srs_backend!r}")
    _BACKEND_KEY = key
    return _BACKEND
