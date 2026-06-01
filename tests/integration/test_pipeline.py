from __future__ import annotations

import dataclasses
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from musicdl.config import Settings
from musicdl.database import Database
from musicdl.downloader.sldl import DownloadResult, SldlDownloader, SldlResult
from musicdl.errors import NotFoundError
from musicdl.genre.cache import GenreCache
from musicdl.genre.resolver import GenreResolver, ResolvedGenre
from musicdl.spotify.client import SpotifyClient
from musicdl.spotify.models import ArtistStub, TrackMetadata
import musicdl.pipeline as pipeline_mod

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_urls(path: Path, urls: list[str]) -> None:
    path.write_text("\n".join(urls) + "\n", encoding="utf-8")


def _make_settings(tmp_path: Path) -> Settings:
    return Settings(
        lastfm_api_key="fake",
        lastfm_api_secret="fake",
        mb_user_agent="musicdl/0.1 test@test.com",
        output_base=tmp_path / "music",
        staging_dir=tmp_path / "staging",
        db_path=tmp_path / "test.db",
        max_retries=3,
        cache_ttl_days=30,
        min_lastfm_tag_weight=30,
    )


def _stub_spotify(tracks: list[TrackMetadata]) -> MagicMock:
    client = MagicMock(spec=SpotifyClient)
    client.expand_url.return_value = tracks
    return client


def _stub_resolver(primary: str = "electronic", subgenre: str = "techno") -> MagicMock:
    resolver = MagicMock(spec=GenreResolver)
    resolver.resolve.return_value = ResolvedGenre(primary=primary, subgenre=subgenre, source="lastfm")
    return resolver


def _stub_downloader_success(staging_dir: Path, track: TrackMetadata) -> MagicMock:
    """Simulates sldl writing a file to staging_dir."""
    fake_file = staging_dir / f"{track.primary_artist.name} - {track.title}.mp3"
    staging_dir.mkdir(parents=True, exist_ok=True)
    fake_file.write_bytes(b"\xff\xfb" + b"\x00" * 1000)  # minimal valid-ish MP3 header

    dl = MagicMock(spec=SldlDownloader)
    dl.download.return_value = SldlResult(
        outcome=DownloadResult.SUCCESS,
        downloaded_files=[fake_file],
        stdout="",
        stderr="",
        return_code=0,
        duration_seconds=1.0,
    )
    return dl


def _stub_downloader_fail(error: Exception) -> MagicMock:
    dl = MagicMock(spec=SldlDownloader)
    dl.download.side_effect = error
    return dl


def _make_track(
    track_id: str = "track1",
    title: str = "Glue",
    artist: str = "Bicep",
    duration_ms: int = 10,
) -> TrackMetadata:
    return TrackMetadata(
        spotify_track_id=track_id,
        spotify_url=f"https://open.spotify.com/track/{track_id}",
        title=title,
        artists=(ArtistStub(spotify_id="a1", name=artist),),
        album_name="Album",
        album_spotify_id="alb1",
        release_year=2020,
        duration_ms=duration_ms,
        track_number=1,
        disc_number=1,
        isrc=None,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestFullPipelineDownloadsAndOrganisesTrack:
    """Full happy path: one track, resolved genre, file in correct directory, DB updated."""

    def test_run(self, tmp_path: Path) -> None:
        settings = _make_settings(tmp_path)
        db = Database(settings.db_path)
        db.migrate()

        track = _make_track()
        spotify = _stub_spotify([track])
        resolver = _stub_resolver("electronic", "techno")
        downloader = _stub_downloader_success(settings.staging_dir, track)

        input_file = tmp_path / "urls.txt"
        _write_urls(input_file, ["https://open.spotify.com/track/track1"])

        with patch("musicdl.tagger.id3.tag_file"):  # skip actual mutagen tagging
            counts = pipeline_mod.run(
                input_file=input_file,
                settings=settings,
                db=db,
                spotify=spotify,
                resolver=resolver,
                downloader=downloader,
            )

        assert counts["downloaded"] == 1
        assert counts["failed"] == 0
        assert counts["skipped"] == 0

        row = db.get_track(track.spotify_track_id)
        assert row is not None
        assert row.status == "downloaded"
        assert row.primary_genre == "electronic"
        assert row.subgenre == "techno"
        assert row.local_path is not None
        assert "electronic" in str(row.local_path)
        assert "techno" in str(row.local_path)
        assert row.local_path.exists()


class TestPipelineSkipsAlreadyDownloadedTrack:
    """Re-running with the same URL skips the track and never calls sldl."""

    def test_run(self, tmp_path: Path) -> None:
        settings = _make_settings(tmp_path)
        db = Database(settings.db_path)
        db.migrate()

        track = _make_track()
        # Pre-populate as already downloaded with an existing file
        db.upsert_track(
            spotify_track_id=track.spotify_track_id,
            spotify_url=track.spotify_url,
            title=track.title,
            primary_artist=track.primary_artist.name,
            all_artists=[track.primary_artist.name],
            album_name=track.album_name,
            album_spotify_id=track.album_spotify_id,
        )
        existing_file = tmp_path / "music" / "track.mp3"
        existing_file.parent.mkdir(parents=True)
        existing_file.write_bytes(b"audio")
        db.mark_downloaded(track.spotify_track_id, existing_file, 5)

        downloader = MagicMock(spec=SldlDownloader)
        input_file = tmp_path / "urls.txt"
        _write_urls(input_file, ["https://open.spotify.com/track/track1"])

        counts = pipeline_mod.run(
            input_file=input_file,
            settings=settings,
            db=db,
            spotify=_stub_spotify([track]),
            resolver=_stub_resolver(),
            downloader=downloader,
        )

        assert counts["skipped"] == 1
        assert counts["downloaded"] == 0
        downloader.download.assert_not_called()


class TestPipelineContinuesAfterOneFailedDownload:
    """When sldl fails for track 1, track 2 still downloads successfully."""

    def test_run(self, tmp_path: Path) -> None:
        settings = _make_settings(tmp_path)
        db = Database(settings.db_path)
        db.migrate()

        track1 = _make_track("t1", "Track One", "ArtistA")
        track2 = _make_track("t2", "Track Two", "ArtistB")

        fake_file = settings.staging_dir / "ArtistB - Track Two.mp3"
        settings.staging_dir.mkdir(parents=True, exist_ok=True)
        fake_file.write_bytes(b"\xff\xfb" + b"\x00" * 500)

        downloader = MagicMock(spec=SldlDownloader)
        downloader.download.side_effect = [
            NotFoundError("not found"),
            SldlResult(
                outcome=DownloadResult.SUCCESS,
                downloaded_files=[fake_file],
                stdout="", stderr="", return_code=0, duration_seconds=1.0,
            ),
        ]

        input_file = tmp_path / "urls.txt"
        _write_urls(input_file, [
            "https://open.spotify.com/track/t1",
            "https://open.spotify.com/track/t2",
        ])

        with patch("musicdl.tagger.id3.tag_file"):
            counts = pipeline_mod.run(
                input_file=input_file,
                settings=settings,
                db=db,
                spotify=_stub_spotify([track1, track2]),
                resolver=_stub_resolver(),
                downloader=downloader,
            )

        assert counts["failed"] == 1
        assert counts["downloaded"] == 1

        row1 = db.get_track("t1")
        row2 = db.get_track("t2")
        assert row1 is not None and row1.status == "failed"
        assert row2 is not None and row2.status == "downloaded"


class TestPipelineHandlesPlaylistExpansion:
    """A playlist URL that expands to N tracks processes each independently."""

    def test_run(self, tmp_path: Path) -> None:
        settings = _make_settings(tmp_path)
        db = Database(settings.db_path)
        db.migrate()

        tracks = [_make_track(f"t{i}", f"Track {i}", "Artist") for i in range(3)]

        staging = settings.staging_dir
        staging.mkdir(parents=True)
        fake_files = []
        for t in tracks:
            f = staging / f"{t.title}.mp3"
            f.write_bytes(b"\xff\xfb" + b"\x00" * 100)
            fake_files.append(f)

        downloader = MagicMock(spec=SldlDownloader)
        downloader.download.side_effect = [
            SldlResult(
                outcome=DownloadResult.SUCCESS,
                downloaded_files=[fake_files[i]],
                stdout="", stderr="", return_code=0, duration_seconds=1.0,
            )
            for i in range(3)
        ]

        input_file = tmp_path / "urls.txt"
        _write_urls(input_file, ["https://open.spotify.com/playlist/pl1"])

        with patch("musicdl.tagger.id3.tag_file"):
            counts = pipeline_mod.run(
                input_file=input_file,
                settings=settings,
                db=db,
                spotify=_stub_spotify(tracks),
                resolver=_stub_resolver(),
                downloader=downloader,
            )

        assert counts["downloaded"] == 3
        assert downloader.download.call_count == 3


class TestPipelineRecordsGenreInDbAndDirectory:
    """Genre from resolver ends up in DB fields and output directory path."""

    def test_run(self, tmp_path: Path) -> None:
        settings = _make_settings(tmp_path)
        db = Database(settings.db_path)
        db.migrate()

        track = _make_track()
        settings.staging_dir.mkdir(parents=True, exist_ok=True)
        fake_file = settings.staging_dir / "Bicep - Glue.mp3"
        fake_file.write_bytes(b"\xff\xfb" + b"\x00" * 100)

        downloader = MagicMock(spec=SldlDownloader)
        downloader.download.return_value = SldlResult(
            outcome=DownloadResult.SUCCESS,
            downloaded_files=[fake_file],
            stdout="", stderr="", return_code=0, duration_seconds=1.0,
        )

        input_file = tmp_path / "urls.txt"
        _write_urls(input_file, ["https://open.spotify.com/track/track1"])

        with patch("musicdl.tagger.id3.tag_file"):
            pipeline_mod.run(
                input_file=input_file,
                settings=settings,
                db=db,
                spotify=_stub_spotify([track]),
                resolver=_stub_resolver("electronic", "deep house"),
                downloader=downloader,
            )

        row = db.get_track(track.spotify_track_id)
        assert row is not None
        assert row.primary_genre == "electronic"
        assert row.subgenre == "deep house"
        assert row.local_path is not None
        assert "electronic" in str(row.local_path)
        assert "deep house" in str(row.local_path)
