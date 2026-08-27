"""Gate tests for srs-mcp builtin FSRS backend (spec §9 step 2)."""

from __future__ import annotations

from moguru.mcp.srs_mcp import core


def test_add_and_due(temp_config):
    backend = core.get_backend(temp_config)
    note_id = backend.add_card(
        deck="Test deck",
        note_type="target-word",
        fields={"Sentence": "魚を食べた。", "TargetWord": "食べる",
                "Reading": "たべる", "Definition": "to eat"},
        tags=["mined", "test"],
    )
    assert note_id > 0
    # A new FSRS card is due immediately
    due = backend.due_cards("Test deck")
    assert any(c["note_id"] == note_id for c in due)


def test_review_schedules_future(temp_config):
    backend = core.get_backend(temp_config)
    note_id = backend.add_card("Test deck", "target-word",
                               {"Sentence": "水を飲む。", "TargetWord": "飲む"},
                               ["test"])
    outcome = backend.review_note(note_id, "good")
    assert outcome["rating"] == "good"
    from datetime import datetime, timezone

    next_due = datetime.fromisoformat(outcome["next_due"])
    assert next_due > datetime.now(timezone.utc)
    # not due anymore right after a good review
    due_ids = [c["note_id"] for c in backend.due_cards("Test deck")]
    assert note_id not in due_ids


def test_review_bad_rating(temp_config):
    import pytest

    backend = core.get_backend(temp_config)
    note_id = backend.add_card("Test deck", "target-word",
                               {"Sentence": "x", "TargetWord": "y"}, [])
    with pytest.raises(ValueError):
        backend.review_note(note_id, "meh")


def test_find_and_update(temp_config):
    backend = core.get_backend(temp_config)
    note_id = backend.add_card("Test deck", "target-word",
                               {"Sentence": "空が青い。", "TargetWord": "青い"},
                               ["tagX"])
    ids = backend.find_notes("TargetWord:青い")
    assert note_id in ids
    backend.update_note(note_id, {"Definition": "blue"})
    found = backend.find_notes("青い")
    assert note_id in found


def test_import_known_empty_when_young(temp_config):
    backend = core.get_backend(temp_config)
    note_id = backend.add_card("Test deck", "target-word",
                               {"Sentence": "x", "TargetWord": "若い"}, [])
    # young card -> not imported
    assert backend.import_known() == []
    _ = note_id


def test_null_backend(tmp_path):
    from moguru.config import Config

    config = Config.__new__(Config)
    config.srs_backend = "none"
    config.anki_connect_url = ""
    config.anki_deck = ""
    config.anki_mature_interval_days = 21
    config.user_dir = tmp_path
    backend = core.get_backend(config)
    assert backend.add_card("d", "t", {}, []) == -1
    assert backend.import_known() == []
