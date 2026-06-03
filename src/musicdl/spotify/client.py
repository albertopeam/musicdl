from __future__ import annotations

import re

from musicdl.errors import SpotifyError
from musicdl.spotify.models import ArtistStub, TrackMetadata

_URL_PATTERNS: dict[str, re.Pattern[str]] = {
    "track":    re.compile(r"spotify\.com/track/([A-Za-z0-9]+)"),
    "album":    re.compile(r"spotify\.com/album/([A-Za-z0-9]+)"),
    "playlist": re.compile(r"spotify\.com/playlist/([A-Za-z0-9]+)"),
}

# Public client credentials used by spotdl's free (unofficial) flow.
_CLIENT_ID     = "5f573c9620494bae87890c0f08a60293"
_CLIENT_SECRET = "212476d9b0f3472eaa762d90b19b0ba8"


def parse_spotify_url(url: str) -> tuple[str, str]:
    url = url.strip()
    for url_type, pattern in _URL_PATTERNS.items():
        m = pattern.search(url)
        if m:
            return url_type, m.group(1)
    raise SpotifyError(f"Unrecognised Spotify URL: {url!r}")


def _init_spotdl_client() -> None:
    """Initialise the spotdl SpotifyClient singleton (safe to call multiple times)."""
    try:
        from spotdl.utils.spotify import SpotifyClient  # type: ignore[import-untyped]
    except ImportError as exc:
        raise SpotifyError("spotdl is not installed. Run: uv add spotdl") from exc

    # SpotifyClient is a singleton — skip if already initialised.
    if SpotifyClient._instance is not None:
        return

    try:
        SpotifyClient.init(
            client_id=_CLIENT_ID,
            client_secret=_CLIENT_SECRET,
            no_cache=True,
            headless=True,
        )
    except Exception as exc:
        raise SpotifyError(f"Failed to initialise Spotify client: {exc}") from exc


class SpotifyClient:
    """
    Wraps spotdl's SpotifyClient to fetch track metadata without a Premium account.
    Uses Spotify's unofficial public endpoints via SpotipyFree internally.
    """

    def __init__(self) -> None:
        _init_spotdl_client()

    def expand_url(self, url: str) -> list[TrackMetadata]:
        url_type, spotify_id = parse_spotify_url(url)
        try:
            if url_type == "track":
                return [self._fetch_track(spotify_id, url)]
            elif url_type == "album":
                return self._fetch_album(spotify_id)
            elif url_type == "playlist":
                return self._fetch_playlist(spotify_id)
            else:
                raise SpotifyError(f"Unknown URL type: {url_type}")
        except SpotifyError:
            raise
        except Exception as exc:
            raise SpotifyError(
                f"Failed to fetch Spotify metadata for {url!r}: {exc}"
            ) from exc

    def _fetch_track(self, track_id: str, original_url: str) -> TrackMetadata:
        from spotdl.types.song import Song  # type: ignore[import-untyped]

        song = Song.from_url(f"https://open.spotify.com/track/{track_id}")
        return _song_to_track(song, original_url)

    def _fetch_album(self, album_id: str) -> list[TrackMetadata]:
        from spotdl.types.album import Album  # type: ignore[import-untyped]

        album = Album.from_url(f"https://open.spotify.com/album/{album_id}")
        return [_song_to_track(s, s.url) for s in album.songs if s is not None]

    def lookup_by_isrc(self, isrc: str) -> TrackMetadata | None:
        """Return track metadata for a known ISRC code, or None if not found."""
        _init_spotdl_client()
        try:
            from spotdl.utils.spotify import SpotifyClient as SpotdlClient  # type: ignore[import-untyped]
            spotdl = SpotdlClient.get_instance()
            results = spotdl._spotify.search(q=f"isrc:{isrc}", type="track", limit=1)  # type: ignore[union-attr]
            items = results.get("tracks", {}).get("items", [])
            if not items:
                return None
            track_id = items[0]["id"]
            url = f"https://open.spotify.com/track/{track_id}"
            return self._fetch_track(track_id, url)
        except Exception:
            return None

    def _fetch_playlist(self, playlist_id: str) -> list[TrackMetadata]:
        from spotdl.types.playlist import Playlist  # type: ignore[import-untyped]

        playlist = Playlist.from_url(f"https://open.spotify.com/playlist/{playlist_id}")
        return [_song_to_track(s, s.url) for s in playlist.songs if s is not None]


def _song_to_track(song: object, original_url: str) -> TrackMetadata:
    raw_artists: list[str] = getattr(song, "artists", None) or []
    primary_artist: str = getattr(song, "artist", None) or (raw_artists[0] if raw_artists else "Unknown")

    if not raw_artists:
        raw_artists = [primary_artist]

    artists = tuple(ArtistStub(spotify_id="", name=name) for name in raw_artists)

    duration_s: float = float(getattr(song, "duration", 0) or 0)

    return TrackMetadata(
        track_id=getattr(song, "song_id", "") or "",
        spotify_url=original_url,
        title=getattr(song, "name", "Unknown") or "Unknown",
        artists=artists,
        album_name=getattr(song, "album_name", "") or "",
        album_spotify_id=getattr(song, "album_id", "") or "",
        release_year=int(getattr(song, "year", 0) or 0),
        duration_ms=int(duration_s * 1000),
        track_number=int(getattr(song, "track_number", 1) or 1),
        disc_number=int(getattr(song, "disc_number", 1) or 1),
        isrc=getattr(song, "isrc", None),
    )
