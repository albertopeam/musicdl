from __future__ import annotations

from pathlib import Path

import structlog

from musicdl.spotify.models import TrackMetadata

logger = structlog.get_logger()


def tag_file(path: Path, track: TrackMetadata) -> None:
    """Write ID3v2.4 tags to an MP3 file. Raises on file read failure."""
    from mutagen.id3 import (  # type: ignore[import-untyped]
        TALB,
        TDRC,
        TCON,
        TIT2,
        TPOS,
        TPE1,
        TRCK,
        TSRC,
    )
    from mutagen.mp3 import MP3  # type: ignore[import-untyped]

    try:
        audio = MP3(str(path))
    except Exception as exc:
        raise OSError(f"Cannot read MP3 file {path}: {exc}") from exc

    if audio.tags is None:
        audio.add_tags()

    tags = audio.tags
    tags["TIT2"] = TIT2(encoding=3, text=track.title)
    tags["TPE1"] = TPE1(encoding=3, text=", ".join(a.name for a in track.artists))
    tags["TALB"] = TALB(encoding=3, text=track.album_name)
    tags["TDRC"] = TDRC(encoding=3, text=str(track.release_year))
    tags["TRCK"] = TRCK(encoding=3, text=str(track.track_number))
    tags["TPOS"] = TPOS(encoding=3, text=str(track.disc_number))
    tags["TCON"] = TCON(encoding=3, text=track.primary_genre or "")
    if track.isrc:
        tags["TSRC"] = TSRC(encoding=3, text=track.isrc)

    audio.save()
    logger.debug("tagged_file", file=path.name, genre=track.primary_genre)


def write_genre_tag(path: Path, genre: str) -> None:
    """Write only the TCON (genre) tag to an existing MP3. Used by the classify command."""
    from mutagen.id3 import TCON  # type: ignore[import-untyped]
    from mutagen.mp3 import MP3  # type: ignore[import-untyped]

    try:
        audio = MP3(str(path))
    except Exception as exc:
        raise OSError(f"Cannot read MP3 file {path}: {exc}") from exc

    if audio.tags is None:
        audio.add_tags()
    audio.tags["TCON"] = TCON(encoding=3, text=genre)
    audio.save()
    logger.debug("wrote_genre_tag", file=path.name, genre=genre)
