"""Shared fixtures: an isolated config (temp user dir) over real dictionary
data, so kb/srs/mining tests never pollute data/user/."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[1]


@pytest.fixture()
def temp_config(tmp_path, monkeypatch):
    cfg = {
        "model": {"local": {"endpoint": "http://localhost:11434/v1", "name": "test"},
                  "routing": "local_only",
                  "shadow": {"endpoint": "http://localhost:11435/v1", "name": "qwen3-8b"}},
        "parser": {"engine": "mecab_unidic"},
        "srs": {"backend": "builtin"},
        "mining": {"iplus_threshold": 1, "sentence_len": [4, 25]},
        "defs": {"mode": "bilingual"},
        "shadow": {"min_samples": 4},
        "paths": {
            "dictionaries": str(REPO / "data" / "dictionaries"),
            "user": str(tmp_path / "user"),
        },
        "anki": {"connect_url": "http://localhost:8765", "deck": "Test deck",
                 "mature_interval_days": 21},
    }
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(yaml.safe_dump(cfg, allow_unicode=True), encoding="utf-8")
    monkeypatch.setenv("MOGURU_CONFIG", str(cfg_path))

    # Reset cached singletons that key off the config env
    import moguru.mcp.kb_mcp.core as kb_core

    kb_core._BLOOM_CACHE = None
    import moguru.mcp.srs_mcp.core as srs_core

    srs_core._BACKEND = None
    srs_core._BACKEND_KEY = None

    from moguru.config import Config

    yield Config.load(cfg_path)

    kb_core._BLOOM_CACHE = None
    srs_core._BACKEND = None
    srs_core._BACKEND_KEY = None
