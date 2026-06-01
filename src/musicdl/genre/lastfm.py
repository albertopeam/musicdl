from __future__ import annotations

import time

import pylast  # type: ignore[import-untyped]
import structlog

from musicdl.errors import GenreError

logger = structlog.get_logger()

_RATE_LIMIT_SLEEP = 0.2  # 5 req/s


class LastFmClient:
    def __init__(self, api_key: str, api_secret: str, min_weight: int = 30) -> None:
        try:
            self._network = pylast.LastFMNetwork(api_key=api_key, api_secret=api_secret)
        except Exception as exc:
            raise GenreError(f"Failed to initialise Last.fm client: {exc}") from exc
        self._min_weight = min_weight

    def get_top_tags(self, artist_name: str) -> list[str]:
        """
        Returns tags (in descending weight order) for artist_name,
        filtered to weight >= min_weight.
        Raises GenreError on network failure.
        """
        try:
            artist = self._network.get_artist(artist_name)
            raw_tags = artist.get_top_tags(limit=15)
            time.sleep(_RATE_LIMIT_SLEEP)
        except pylast.WSError as exc:
            raise GenreError(f"Last.fm API error for '{artist_name}': {exc}") from exc
        except Exception as exc:
            raise GenreError(f"Last.fm request failed for '{artist_name}': {exc}") from exc

        return [
            str(item.item.get_name())
            for item in raw_tags
            if int(item.weight) >= self._min_weight
        ]
