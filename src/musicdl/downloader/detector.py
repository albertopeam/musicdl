from __future__ import annotations

from pathlib import Path

import structlog

from musicdl.spotify.models import TrackMetadata

logger = structlog.get_logger()

_DURATION_TOLERANCE_S = 3


def find_best_match(new_files: list[Path], track: TrackMetadata) -> Path | None:
    """
    Among files that appeared in staging after sldl ran, pick the one
    whose audio duration is within ±3s of the expected track duration.
    Returns None if no confident match is found.
    """
    if not new_files:
        return None

    if track.duration_ms <= 0:
        # No duration to compare — take the first file
        return new_files[0]

    expected_s = track.duration_ms / 1000.0

    readable: list[Path] = []
    for path in new_files:
        actual_s = _get_duration_seconds(path)
        if actual_s is None:
            continue
        readable.append(path)
        diff = abs(actual_s - expected_s)
        if diff <= _DURATION_TOLERANCE_S:
            logger.debug(
                "detector_match",
                file=path.name,
                expected_s=round(expected_s, 1),
                actual_s=round(actual_s, 1),
                diff=round(diff, 1),
            )
            return path

    # No duration match — fall back to first readable file (excludes corrupt files)
    if readable:
        logger.warning(
            "detector_no_match",
            title=track.title,
            expected_s=round(expected_s, 1),
            candidates=[p.name for p in readable],
        )
        return readable[0]

    return None


def _get_duration_seconds(path: Path) -> float | None:
    try:
        from mutagen.mp3 import MP3  # type: ignore[import-untyped]

        audio = MP3(str(path))
        return float(audio.info.length)
    except Exception as exc:
        logger.debug("detector_duration_read_failed", file=path.name, error=str(exc))
        return None
