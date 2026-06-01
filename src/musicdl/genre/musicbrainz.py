from __future__ import annotations

import time

import musicbrainzngs  # type: ignore[import-untyped]
import structlog

from musicdl.errors import GenreError

logger = structlog.get_logger()

_RATE_LIMIT_SLEEP = 1.05  # MusicBrainz hard limit: 1 req/s


class MusicBrainzClient:
    def __init__(self, user_agent: str) -> None:
        try:
            app, version, contact = _parse_user_agent(user_agent)
            musicbrainzngs.set_useragent(app, version, contact)
        except Exception as exc:
            raise GenreError(f"Failed to initialise MusicBrainz client: {exc}") from exc

    def get_artist_genres(self, artist_name: str) -> list[str]:
        """
        Returns genre/tag names for the best-matching artist in MusicBrainz.
        Raises GenreError on network or parse failure.
        """
        try:
            result = musicbrainzngs.search_artists(artist=artist_name, limit=5)
            time.sleep(_RATE_LIMIT_SLEEP)
        except musicbrainzngs.WebServiceError as exc:
            raise GenreError(f"MusicBrainz search failed for '{artist_name}': {exc}") from exc
        except Exception as exc:
            raise GenreError(f"MusicBrainz request failed for '{artist_name}': {exc}") from exc

        artists = result.get("artist-list", [])
        if not artists:
            return []

        # Pick highest-score result
        best = max(artists, key=lambda a: int(a.get("ext:score", 0)))
        mb_id = best.get("id", "")
        if not mb_id:
            return []

        try:
            detail = musicbrainzngs.get_artist_by_id(mb_id, includes=["tags"])
            time.sleep(_RATE_LIMIT_SLEEP)
        except Exception as exc:
            raise GenreError(f"MusicBrainz detail fetch failed for '{mb_id}': {exc}") from exc

        tags = detail.get("artist", {}).get("tag-list", [])
        # Sort by count descending, return names only
        sorted_tags = sorted(tags, key=lambda t: int(t.get("count", 0)), reverse=True)
        return [t["name"] for t in sorted_tags if t.get("name")]


def _parse_user_agent(ua: str) -> tuple[str, str, str]:
    parts = ua.strip().split()
    if parts:
        app_ver = parts[0].split("/", 1)
        app = app_ver[0]
        version = app_ver[1] if len(app_ver) > 1 else "0.1"
        contact = parts[1] if len(parts) > 1 else ""
        return app, version, contact
    return "musicdl", "0.1", ""
