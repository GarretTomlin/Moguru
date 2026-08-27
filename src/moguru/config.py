"""Configuration loading (spec §1).

All §1 parameters live in config.yaml at the repo root. Everything else reads
them through this module so no tool or skill hard-codes a model name, backend,
or threshold.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

# Repo root = .../moguru (three levels up from this file: src/moguru/config.py)
REPO_ROOT = Path(__file__).resolve().parents[2]

DEFAULT_CONFIG_PATH = REPO_ROOT / "config.yaml"


def load_dotenv(path: Path | str | None = None) -> None:
    """Optional `data/user/.env` — KEY=VALUE lines for provider API keys.

    The file lives under gitignored user state, so keys stay out of the repo
    without shell-profile juggling (`moguru serve` from any terminal sees
    them). Values only fill UNSET variables: a real export always wins.
    """
    path = Path(path) if path else REPO_ROOT / "data" / "user" / ".env"
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip("'\"")
        if key:
            os.environ.setdefault(key, value)


@dataclass
class ModelConfig:
    endpoint: str
    name: str


@dataclass
class Config:
    raw: dict[str, Any]

    # model.*
    local: ModelConfig
    strong: ModelConfig | None
    shadow: ModelConfig
    routing: str  # local_only | local_first | strong_only

    # parser.*
    parser_engine: str  # mecab_unidic | sudachi | ichiran

    # srs.*
    srs_backend: str  # anki | builtin | none

    # mining.*
    iplus_threshold: int
    sentence_len: tuple[int, int]

    # defs.*
    defs_mode: str  # bilingual | mixed | monolingual

    # shadow.*
    shadow_min_samples: int
    shadow_decay_half_life_days: float
    shadow_calibration_window: int
    shadow_weights: dict[str, float]

    # paths
    dictionaries_dir: Path
    user_dir: Path

    # anki
    anki_connect_url: str
    anki_deck: str
    anki_mature_interval_days: int

    # Phase A adaptivity overlay (data/user/adaptive.yaml), applied on load.
    adaptive_overrides: dict[str, Any] = field(default_factory=dict)

    # ------------------------------------------------------------------
    @classmethod
    def load(cls, path: Path | str | None = None) -> "Config":
        load_dotenv()  # data/user/.env provider keys, if present
        path = Path(path) if path else DEFAULT_CONFIG_PATH
        with open(path, encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}

        model = raw.get("model", {})
        local = model.get("local", {})
        strong = model.get("strong") or {}
        shadow = model.get("shadow") or {}

        paths = raw.get("paths", {})
        dictionaries_dir = Path(paths.get("dictionaries", "data/dictionaries"))
        user_dir = Path(paths.get("user", "data/user"))
        if not dictionaries_dir.is_absolute():
            dictionaries_dir = REPO_ROOT / dictionaries_dir
        if not user_dir.is_absolute():
            user_dir = REPO_ROOT / user_dir

        anki = raw.get("anki", {})

        cfg = cls(
            raw=raw,
            local=ModelConfig(
                endpoint=local.get("endpoint", "http://localhost:11434/v1"),
                name=local.get("name", ""),
            ),
            strong=(
                ModelConfig(
                    endpoint=strong.get("endpoint", ""),
                    name=strong.get("name", ""),
                )
                if strong.get("endpoint")
                else None
            ),
            shadow=ModelConfig(
                endpoint=shadow.get("endpoint", "http://localhost:11435/v1"),
                name=shadow.get("name", "qwen3-8b"),
            ),
            routing=model.get("routing", "local_first"),
            parser_engine=raw.get("parser", {}).get("engine", "mecab_unidic"),
            srs_backend=raw.get("srs", {}).get("backend", "anki"),
            iplus_threshold=raw.get("mining", {}).get("iplus_threshold", 1),
            sentence_len=tuple(
                raw.get("mining", {}).get("sentence_len", [4, 25])
            ),
            defs_mode=raw.get("defs", {}).get("mode", "bilingual"),
            shadow_min_samples=raw.get("shadow", {}).get("min_samples", 4),
            shadow_decay_half_life_days=raw.get("shadow", {}).get(
                "decay_half_life_days", 120
            ),
            shadow_calibration_window=raw.get("shadow", {}).get(
                "calibration_window", 500
            ),
            shadow_weights=raw.get("shadow", {}).get("weights", {}) or {},
            dictionaries_dir=dictionaries_dir,
            user_dir=user_dir,
            anki_connect_url=anki.get("connect_url", "http://localhost:8765"),
            anki_deck=anki.get("deck", "Moguru 日本語"),
            anki_mature_interval_days=anki.get("mature_interval_days", 21),
        )

        # Phase A overlay: adaptivity proposals live outside config.yaml so
        # human-authored config is never silently mutated (spec §5.2 rails).
        adaptive_file = cfg.user_dir / "adaptive.yaml"
        if adaptive_file.exists():
            with open(adaptive_file, encoding="utf-8") as f:
                cfg.adaptive_overrides = yaml.safe_load(f) or {}
            cfg._apply_adaptive_overrides()
        return cfg

    def _apply_adaptive_overrides(self) -> None:
        ov = self.adaptive_overrides
        if "iplus_threshold" in ov.get("mining", {}):
            self.iplus_threshold = int(ov["mining"]["iplus_threshold"])
        if "mode" in ov.get("defs", {}):
            self.defs_mode = ov["defs"]["mode"]

    # Convenience paths -------------------------------------------------
    @property
    def dict_db(self) -> Path:
        return self.dictionaries_dir / "dict.sqlite"

    @property
    def freq_db(self) -> Path:
        return self.dictionaries_dir / "freq.sqlite"

    @property
    def kb_db(self) -> Path:
        return self.user_dir / "kb.sqlite"

    @property
    def srs_db(self) -> Path:
        return self.user_dir / "srs.sqlite"
