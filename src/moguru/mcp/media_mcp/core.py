"""media-mcp core — content pipeline (spec §3.6).

parse_subtitles / extract_audio / ocr_image / capture_context.
ffmpeg must be on PATH (checked lazily). manga-ocr is an optional extra:
`uv sync --extra ocr`.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Any

from moguru.config import Config


def parse_subtitles(file: str) -> list[dict[str, Any]]:
    """[{ start, end, text }] from .srt / .ass (millisecond timestamps)."""
    import pysubs2

    subs = pysubs2.load(file)
    return [
        {"start": ev.start, "end": ev.end, "text": ev.plaintext.replace("\n", " ")}
        for ev in subs.events
        if ev.plaintext.strip()
    ]


def _require_ffmpeg() -> None:
    if shutil.which("ffmpeg") is None:
        raise RuntimeError("ffmpeg not found on PATH — install it (brew install ffmpeg)")


def extract_audio(media: str, start: float, end: float,
                  out_dir: str | None = None, config: Config | None = None) -> str:
    """Slice sentence audio (m4a) for cards. Times in seconds."""
    _require_ffmpeg()
    config = config or Config.load()
    out_dir = Path(out_dir) if out_dir else config.user_dir / "media"
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = Path(media).stem
    out = out_dir / f"{stem}_{int(start * 1000)}-{int(end * 1000)}.m4a"
    subprocess.run(
        [
            "ffmpeg", "-y", "-loglevel", "error",
            "-ss", str(start), "-to", str(end), "-i", media,
            "-vn", "-c:a", "aac", "-b:a", "96k", str(out),
        ],
        check=True,
    )
    return str(out)


def capture_context(media: str, timestamp: float,
                    out_dir: str | None = None, config: Config | None = None) -> str:
    """Grab the frame at `timestamp` (seconds) as the card's context image."""
    _require_ffmpeg()
    config = config or Config.load()
    out_dir = Path(out_dir) if out_dir else config.user_dir / "media"
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = Path(media).stem
    out = out_dir / f"{stem}_{int(timestamp * 1000)}.png"
    subprocess.run(
        [
            "ffmpeg", "-y", "-loglevel", "error",
            "-ss", str(timestamp), "-i", media,
            "-frames:v", "1", str(out),
        ],
        check=True,
    )
    return str(out)


def ocr_image(image: str) -> str:
    """Japanese OCR (manga / screenshots) via manga-ocr. Optional extra:
    `uv sync --extra ocr`."""
    try:
        from manga_ocr import MangaOcr

    except ImportError as e:
        raise RuntimeError(
            "manga-ocr is not installed — run `uv sync --extra ocr` "
            "(pulls torch; the base install stays light without it)"
        ) from e
    global _OCR
    if _OCR is None:
        _OCR = MangaOcr()
    return _OCR(image)


_OCR: Any | None = None
