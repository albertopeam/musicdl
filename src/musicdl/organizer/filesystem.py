from __future__ import annotations

import re
from pathlib import Path

_UNSAFE_RE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_WHITESPACE_RE = re.compile(r"\s+")
_MAX_SEGMENT = 200


def sanitize(name: str) -> str:
    name = name.strip()
    if not name:
        return "unknown"
    name = _UNSAFE_RE.sub("_", name)
    name = _WHITESPACE_RE.sub(" ", name).strip(". ")
    name = name.lower()
    return name[:_MAX_SEGMENT] or "unknown"


def build_target_path(
    base: Path,
    primary_genre: str | None,
    subgenre: str | None,
    track_number: int | None,
    title: str,
) -> Path:
    genre_dir    = sanitize(primary_genre or "unknown")
    subgenre_dir = sanitize(subgenre or "unknown")
    number       = f"{track_number or 0:02d}"
    filename     = f"{number} - {sanitize(title)}.mp3"
    if genre_dir == "unknown" and subgenre_dir == "unknown":
        return base / genre_dir / filename
    return base / genre_dir / subgenre_dir / filename


def move_to_library(source: Path, target: Path) -> Path:
    """
    Move source to target. Creates parent directories.
    If target already exists, appends _2, _3, … before the extension.
    Never overwrites an existing file.
    """
    target.parent.mkdir(parents=True, exist_ok=True)

    if target.exists():
        stem = target.stem
        suffix = target.suffix
        n = 2
        while target.exists():
            target = target.parent / f"{stem}_{n}{suffix}"
            n += 1

    source.rename(target)
    return target
