from __future__ import annotations

import pytest

from musicdl.organizer.filesystem import build_target_path, move_to_library, sanitize
from musicdl.spotify.models import ArtistStub, TrackMetadata
from pathlib import Path


class TestSanitize:
    @pytest.mark.parametrize("raw,expected", [
        ("House",               "house"),
        ("Deep House",          "deep house"),
        ("",                    "unknown"),
        ("   ",                 "unknown"),
        ('file/with\\bad:chars', "file_with_bad_chars"),
        ("A" * 300,             "a" * 200),
        ("...leading dots",     "leading dots"),
        ("trailingdots...",     "trailingdots"),
    ])
    def test_sanitize(self, raw: str, expected: str) -> None:
        assert sanitize(raw) == expected

    def test_unicode_preserved(self) -> None:
        result = sanitize("Björk")
        assert "bj" in result


class TestBuildTargetPath:
    def _make_track(self, **kwargs: object) -> TrackMetadata:
        defaults = dict(
            track_id="id1",
            spotify_url="https://open.spotify.com/track/id1",
            title="Glue",
            artists=(ArtistStub(spotify_id="a1", name="Bicep"),),
            album_name="Bicep",
            album_spotify_id="alb1",
            release_year=2017,
            duration_ms=375000,
            track_number=1,
            disc_number=1,
            isrc=None,
            primary_genre="electronic",
            subgenre="house",
        )
        defaults.update(kwargs)
        return TrackMetadata(**defaults)  # type: ignore[arg-type]

    def test_standard_path(self, tmp_path: Path) -> None:
        track = self._make_track()
        path = build_target_path(tmp_path, track)
        assert path == tmp_path / "electronic" / "house" / "bicep" / "bicep" / "01 - glue.mp3"

    def test_unknown_genre_falls_back(self, tmp_path: Path) -> None:
        track = self._make_track(primary_genre=None, subgenre=None)
        path = build_target_path(tmp_path, track)
        assert "unknown" in str(path)

    def test_track_number_zero_padded(self, tmp_path: Path) -> None:
        track = self._make_track(track_number=3)
        path = build_target_path(tmp_path, track)
        assert "03 - glue" in path.name


class TestMoveToLibrary:
    def test_moves_file(self, tmp_path: Path) -> None:
        source = tmp_path / "staging" / "track.mp3"
        source.parent.mkdir()
        source.write_bytes(b"audio")
        target = tmp_path / "music" / "track.mp3"
        result = move_to_library(source, target)
        assert result == target
        assert target.exists()
        assert not source.exists()

    def test_does_not_overwrite_existing(self, tmp_path: Path) -> None:
        existing = tmp_path / "track.mp3"
        existing.write_bytes(b"original")
        source = tmp_path / "new.mp3"
        source.write_bytes(b"new content")
        result = move_to_library(source, existing)
        assert result.name == "track_2.mp3"
        assert existing.read_bytes() == b"original"
        assert result.read_bytes() == b"new content"

    def test_multiple_collisions(self, tmp_path: Path) -> None:
        (tmp_path / "track.mp3").write_bytes(b"1")
        (tmp_path / "track_2.mp3").write_bytes(b"2")
        source = tmp_path / "src.mp3"
        source.write_bytes(b"3")
        result = move_to_library(source, tmp_path / "track.mp3")
        assert result.name == "track_3.mp3"

    def test_creates_parent_directories(self, tmp_path: Path) -> None:
        source = tmp_path / "src.mp3"
        source.write_bytes(b"audio")
        deep_target = tmp_path / "a" / "b" / "c" / "track.mp3"
        move_to_library(source, deep_target)
        assert deep_target.exists()
