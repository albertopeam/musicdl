from __future__ import annotations

from pathlib import Path

import pytest

from musicdl.database import Database, TrackRow
from musicdl.spotify.models import ArtistStub, TrackMetadata


def _insert(db: Database, track: TrackMetadata, status: str = "pending") -> None:
    db.upsert_track(
        spotify_track_id=track.spotify_track_id,
        spotify_url=track.spotify_url,
        title=track.title,
        primary_artist=track.primary_artist.name,
        all_artists=[a.name for a in track.artists],
        album_name=track.album_name,
        album_spotify_id=track.album_spotify_id,
        isrc=track.isrc,
        release_year=track.release_year,
        duration_ms=track.duration_ms,
        track_number=track.track_number,
        disc_number=track.disc_number,
        status=status,
    )


class TestShouldDownload:
    def test_returns_true_when_track_not_in_db(self, tmp_db: Database) -> None:
        assert tmp_db.should_download("nonexistent_id", max_retries=3) is True

    def test_returns_false_when_downloaded_and_file_exists(
        self, tmp_db: Database, sample_track: TrackMetadata, tmp_path: Path
    ) -> None:
        _insert(tmp_db, sample_track, status="pending")
        fake_file = tmp_path / "track.mp3"
        fake_file.write_bytes(b"audio")
        tmp_db.mark_downloaded(sample_track.spotify_track_id, fake_file, 5)
        assert tmp_db.should_download(sample_track.spotify_track_id, max_retries=3) is False

    def test_returns_true_when_downloaded_but_file_missing(
        self, tmp_db: Database, sample_track: TrackMetadata, tmp_path: Path
    ) -> None:
        _insert(tmp_db, sample_track, status="pending")
        missing_file = tmp_path / "gone.mp3"
        tmp_db.mark_downloaded(sample_track.spotify_track_id, missing_file, 5)
        assert tmp_db.should_download(sample_track.spotify_track_id, max_retries=3) is True

    def test_returns_false_when_failed_and_retries_exhausted(
        self, tmp_db: Database, sample_track: TrackMetadata
    ) -> None:
        _insert(tmp_db, sample_track)
        for _ in range(3):
            tmp_db.mark_failed(sample_track.spotify_track_id, "err")
        assert tmp_db.should_download(sample_track.spotify_track_id, max_retries=3) is False

    def test_returns_true_when_failed_with_retries_remaining(
        self, tmp_db: Database, sample_track: TrackMetadata
    ) -> None:
        _insert(tmp_db, sample_track)
        tmp_db.mark_failed(sample_track.spotify_track_id, "err")
        assert tmp_db.should_download(sample_track.spotify_track_id, max_retries=3) is True


class TestMarkDownloaded:
    def test_sets_status_and_path(
        self, tmp_db: Database, sample_track: TrackMetadata, tmp_path: Path
    ) -> None:
        _insert(tmp_db, sample_track)
        target = tmp_path / "track.mp3"
        target.write_bytes(b"x")
        tmp_db.mark_downloaded(sample_track.spotify_track_id, target, 1)
        row: TrackRow | None = tmp_db.get_track(sample_track.spotify_track_id)
        assert row is not None
        assert row.status == "downloaded"
        assert row.local_path == target
        assert row.file_size_bytes == 1

    def test_typed_row_not_dict(self, tmp_db: Database, sample_track: TrackMetadata) -> None:
        _insert(tmp_db, sample_track)
        row = tmp_db.get_track(sample_track.spotify_track_id)
        assert isinstance(row, TrackRow)
        assert isinstance(row.all_artists, list)


class TestMarkFailed:
    def test_increments_retry_count(
        self, tmp_db: Database, sample_track: TrackMetadata
    ) -> None:
        _insert(tmp_db, sample_track)
        tmp_db.mark_failed(sample_track.spotify_track_id, "err1")
        row = tmp_db.get_track(sample_track.spotify_track_id)
        assert row is not None
        assert row.retry_count == 1
        assert row.last_error == "err1"

    def test_truncates_long_error(
        self, tmp_db: Database, sample_track: TrackMetadata
    ) -> None:
        _insert(tmp_db, sample_track)
        tmp_db.mark_failed(sample_track.spotify_track_id, "x" * 2000)
        row = tmp_db.get_track(sample_track.spotify_track_id)
        assert row is not None
        assert len(row.last_error or "") <= 1000


class TestGenreCache:
    def test_cache_miss_returns_none(self, tmp_db: Database) -> None:
        assert tmp_db.get_genre_cache("Unknown Artist", ttl_days=30) is None

    def test_cache_hit_returns_row(self, tmp_db: Database) -> None:
        tmp_db.set_genre_cache("Bicep", "electronic", "house", "lastfm", ["house", "deep house"], 30)
        row = tmp_db.get_genre_cache("Bicep", ttl_days=30)
        assert row is not None
        assert row.primary_genre == "electronic"
        assert row.subgenre == "house"
        assert isinstance(row.raw_tags, list)

    def test_upsert_updates_existing(self, tmp_db: Database) -> None:
        tmp_db.set_genre_cache("Bicep", "electronic", "house", "lastfm", [], 30)
        tmp_db.set_genre_cache("Bicep", "electronic", "techno", "musicbrainz", [], 30)
        row = tmp_db.get_genre_cache("Bicep", ttl_days=30)
        assert row is not None
        assert row.subgenre == "techno"


class TestResetFailed:
    def test_resets_all_failed_to_pending(
        self, tmp_db: Database, sample_track: TrackMetadata
    ) -> None:
        _insert(tmp_db, sample_track)
        tmp_db.mark_failed(sample_track.spotify_track_id, "err")
        count = tmp_db.reset_failed_tracks()
        assert count == 1
        row = tmp_db.get_track(sample_track.spotify_track_id)
        assert row is not None
        assert row.status == "pending"
        assert row.retry_count == 0
