from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ArtistStub:
    spotify_id: str
    name: str


@dataclass(frozen=True)
class TrackMetadata:
    spotify_track_id: str
    spotify_url: str
    title: str
    artists: tuple[ArtistStub, ...]
    album_name: str
    album_spotify_id: str
    release_year: int
    duration_ms: int
    track_number: int
    disc_number: int
    isrc: str | None
    # Populated after genre resolution
    primary_genre: str | None = None
    subgenre: str | None = None

    @property
    def primary_artist(self) -> ArtistStub:
        return self.artists[0]

    @property
    def search_query(self) -> str:
        return f"{self.primary_artist.name} - {self.title}"
