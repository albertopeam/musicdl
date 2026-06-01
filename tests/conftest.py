from __future__ import annotations

from pathlib import Path

import pytest

from musicdl.database import Database
from musicdl.spotify.models import ArtistStub, TrackMetadata


@pytest.fixture
def tmp_db(tmp_path: Path) -> Database:
    db = Database(tmp_path / "test.db")
    db.migrate()
    return db


@pytest.fixture
def sample_track() -> TrackMetadata:
    return TrackMetadata(
        spotify_track_id="4uLU6hMCjMI75M1A2tKUQC",
        spotify_url="https://open.spotify.com/track/4uLU6hMCjMI75M1A2tKUQC",
        title="Glue",
        artists=(ArtistStub(spotify_id="art1", name="Bicep"),),
        album_name="Bicep",
        album_spotify_id="alb1",
        release_year=2017,
        duration_ms=375_000,
        track_number=1,
        disc_number=1,
        isrc="GBAHT1700555",
    )


@pytest.fixture
def staging_dir(tmp_path: Path) -> Path:
    d = tmp_path / "staging"
    d.mkdir()
    return d
