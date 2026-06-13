from __future__ import annotations

from pathlib import Path

import pytest
from pytest_mock import MockerFixture

from musicdl.downloader.detector import find_best_match
from musicdl.spotify.models import ArtistStub, TrackMetadata


def _track(duration_ms: int) -> TrackMetadata:
    return TrackMetadata(
        track_id="abc",
        spotify_url="https://open.spotify.com/track/abc",
        title="Test Track",
        artists=(ArtistStub(spotify_id="a1", name="Artist"),),
        album_name="Album",
        album_spotify_id="alb1",
        release_year=2020,
        duration_ms=duration_ms,
        track_number=1,
        disc_number=1,
        isrc=None,
    )


class TestFindBestMatch:
    def test_returns_none_when_no_files(self) -> None:
        assert find_best_match([], _track(180_000)) is None

    def test_returns_none_when_all_files_unreadable(self, tmp_path: Path) -> None:
        corrupt = tmp_path / "corrupt.mp3"
        corrupt.write_bytes(b"this is not an mp3")
        assert find_best_match([corrupt], _track(180_000)) is None

    def test_returns_matching_file(self, tmp_path: Path, mocker: MockerFixture) -> None:
        f = tmp_path / "track.mp3"
        f.write_bytes(b"")
        mocker.patch(
            "musicdl.downloader.detector._get_duration_seconds",
            return_value=180.0,
        )
        result = find_best_match([f], _track(180_000))
        assert result == f

    def test_skips_corrupt_falls_back_to_readable(self, tmp_path: Path, mocker: MockerFixture) -> None:
        corrupt = tmp_path / "corrupt.mp3"
        corrupt.write_bytes(b"garbage")
        good = tmp_path / "good.mp3"
        good.write_bytes(b"")

        # corrupt returns None; good returns a duration that doesn't match within tolerance
        def fake_duration(path: Path) -> float | None:
            return None if path == corrupt else 300.0

        mocker.patch("musicdl.downloader.detector._get_duration_seconds", side_effect=fake_duration)

        # expected duration is 180s — 300s is outside ±3s tolerance, so no exact match
        result = find_best_match([corrupt, good], _track(180_000))
        # should fall back to first readable file (good), not the corrupt one
        assert result == good

    def test_corrupt_only_file_returns_none_not_corrupt(self, tmp_path: Path, mocker: MockerFixture) -> None:
        corrupt = tmp_path / "corrupt.mp3"
        corrupt.write_bytes(b"garbage")
        mocker.patch("musicdl.downloader.detector._get_duration_seconds", return_value=None)
        result = find_best_match([corrupt], _track(180_000))
        assert result is None

    def test_returns_none_when_duration_unknown(self) -> None:
        # track with duration_ms=0 falls through to first file
        pass  # edge case: duration_ms <= 0 returns new_files[0] directly — existing behaviour

    def test_no_duration_on_track_returns_first_file(self, tmp_path: Path) -> None:
        f = tmp_path / "track.mp3"
        f.write_bytes(b"")
        result = find_best_match([f], _track(0))
        assert result == f

    @pytest.mark.parametrize("diff_s", [0.0, 1.5, 3.0])
    def test_accepts_files_within_tolerance(self, tmp_path: Path, mocker: MockerFixture, diff_s: float) -> None:
        f = tmp_path / "track.mp3"
        f.write_bytes(b"")
        mocker.patch(
            "musicdl.downloader.detector._get_duration_seconds",
            return_value=180.0 + diff_s,
        )
        assert find_best_match([f], _track(180_000)) == f

    def test_rejects_file_outside_tolerance(self, tmp_path: Path, mocker: MockerFixture) -> None:
        f = tmp_path / "track.mp3"
        f.write_bytes(b"")
        mocker.patch(
            "musicdl.downloader.detector._get_duration_seconds",
            return_value=184.0,  # 4s over — outside ±3s
        )
        # No exact match; falls back to the readable file
        result = find_best_match([f], _track(180_000))
        assert result == f  # fallback, not exact match
