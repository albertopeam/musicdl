from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

import structlog

logger = structlog.get_logger()

SUPPORTED_EXTENSIONS = frozenset({".mp3", ".flac", ".m4a", ".wav"})


@dataclass(frozen=True)
class ScannedFile:
    path: Path
    title: str | None
    artist: str | None
    album: str | None
    year: int | None
    track_number: int | None
    isrc: str | None
    genre: str | None
    duration_ms: int | None
    file_format: str  # "mp3" | "flac" | "m4a" | "wav"


def scan_directory(path: Path) -> list[Path]:
    """Return all supported audio files under path (recursive)."""
    if path.is_file():
        return [path] if path.suffix.lower() in SUPPORTED_EXTENSIONS else []
    return sorted(
        p for p in path.rglob("*")
        if p.is_file() and p.suffix.lower() in SUPPORTED_EXTENSIONS
    )


def read_file_tags(path: Path) -> ScannedFile | None:
    """Read ID3/Vorbis/iTunes tags from an audio file. Returns None on failure."""
    try:
        suffix = path.suffix.lower()
        if suffix == ".mp3":
            return _read_mp3_tags(path)
        elif suffix == ".flac":
            return _read_flac_tags(path)
        elif suffix in (".m4a", ".mp4"):
            return _read_m4a_tags(path)
        else:
            return _read_generic_tags(path)
    except Exception as exc:
        logger.warning("tag_read_failed", path=str(path), error=str(exc))
        return None


def make_local_track_id(path: Path, title: str | None, artist: str | None) -> str:
    """Generate a stable synthetic track ID for files with no Spotify match.

    Uses a hash of normalized artist + title + filename. Content-based hashing
    is intentionally avoided because cloud-synced files (Google Drive, iCloud)
    can return different bytes on successive reads when not fully cached locally.
    """
    key = f"{(artist or '').strip().lower()}|{(title or '').strip().lower()}|{path.name.lower()}"
    h = hashlib.sha256(key.encode()).hexdigest()[:16]
    return f"local:{h}"


# ---------------------------------------------------------------------------
# Format-specific helpers
# ---------------------------------------------------------------------------

def _read_mp3_tags(path: Path) -> ScannedFile:
    from mutagen.mp3 import MP3  # type: ignore[import-untyped]

    audio = MP3(str(path))
    tags = audio.tags or {}

    def _text(frame_id: str) -> str | None:
        frame = tags.get(frame_id)
        return str(frame).strip() or None if frame else None

    year: int | None = None
    tdrc = tags.get("TDRC")
    if tdrc:
        try:
            year = int(str(tdrc)[:4])
        except (ValueError, TypeError):
            pass

    track_number: int | None = None
    trck = _text("TRCK")
    if trck:
        try:
            track_number = int(trck.split("/")[0])
        except (ValueError, TypeError):
            pass

    duration_ms = int(audio.info.length * 1000) if audio.info else None

    return ScannedFile(
        path=path,
        title=_text("TIT2"),
        artist=_text("TPE1"),
        album=_text("TALB"),
        year=year,
        track_number=track_number,
        isrc=_text("TSRC"),
        genre=_text("TCON"),
        duration_ms=duration_ms,
        file_format="mp3",
    )


def _read_flac_tags(path: Path) -> ScannedFile:
    from mutagen.flac import FLAC  # type: ignore[import-untyped]

    audio = FLAC(str(path))
    tags = audio.tags or {}

    def _first(key: str) -> str | None:
        vals = tags.get(key.upper()) or tags.get(key.lower())
        return (vals[0].strip() or None) if vals else None

    year: int | None = None
    raw_year = _first("DATE") or _first("YEAR")
    if raw_year:
        try:
            year = int(raw_year[:4])
        except (ValueError, TypeError):
            pass

    track_number: int | None = None
    raw_trk = _first("TRACKNUMBER")
    if raw_trk:
        try:
            track_number = int(raw_trk.split("/")[0])
        except (ValueError, TypeError):
            pass

    duration_ms = int(audio.info.length * 1000) if audio.info else None

    return ScannedFile(
        path=path,
        title=_first("TITLE"),
        artist=_first("ARTIST"),
        album=_first("ALBUM"),
        year=year,
        track_number=track_number,
        isrc=_first("ISRC"),
        genre=_first("GENRE"),
        duration_ms=duration_ms,
        file_format="flac",
    )


def _read_m4a_tags(path: Path) -> ScannedFile:
    from mutagen.mp4 import MP4  # type: ignore[import-untyped]

    audio = MP4(str(path))
    tags = audio.tags or {}

    def _first(key: str) -> str | None:
        vals = tags.get(key)
        return (str(vals[0]).strip() or None) if vals else None

    year: int | None = None
    raw_year = _first("\xa9day")
    if raw_year:
        try:
            year = int(raw_year[:4])
        except (ValueError, TypeError):
            pass

    track_number: int | None = None
    trkn = tags.get("trkn")
    if trkn:
        try:
            track_number = int(trkn[0][0])
        except (TypeError, IndexError, ValueError):
            pass

    duration_ms = int(audio.info.length * 1000) if audio.info else None

    return ScannedFile(
        path=path,
        title=_first("\xa9nam"),
        artist=_first("\xa9ART"),
        album=_first("\xa9alb"),
        year=year,
        track_number=track_number,
        isrc=None,  # M4A rarely carries ISRC in standard atoms
        genre=_first("\xa9gen"),
        duration_ms=duration_ms,
        file_format="m4a",
    )


def _read_generic_tags(path: Path) -> ScannedFile:
    import mutagen  # type: ignore[import-untyped]

    audio = mutagen.File(str(path))
    if audio is None:
        raise ValueError(f"mutagen could not read {path}")

    duration_ms = int(audio.info.length * 1000) if hasattr(audio, "info") and audio.info else None

    return ScannedFile(
        path=path,
        title=None,
        artist=None,
        album=None,
        year=None,
        track_number=None,
        isrc=None,
        genre=None,
        duration_ms=duration_ms,
        file_format=path.suffix.lstrip(".").lower(),
    )
