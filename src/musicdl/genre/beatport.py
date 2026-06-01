from __future__ import annotations

import re
import time

import httpx
import structlog

from musicdl.errors import GenreError

logger = structlog.get_logger()

_SEARCH_URL = "https://www.beatport.com/search"
_GENRE_RE = re.compile(r'"genre"\s*:\s*\{[^}]*"name"\s*:\s*"([^"]+)"', re.IGNORECASE)
_RATE_LIMIT_SLEEP = 2.0


class BeatportScraper:
    """
    Best-effort electronic genre enrichment via Beatport search.
    Only meaningful for electronic music artists.
    """

    def __init__(self) -> None:
        self._client = httpx.Client(
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                )
            },
            timeout=15.0,
            follow_redirects=True,
        )

    def get_genre(self, artist_name: str) -> str | None:
        """
        Returns a genre string from Beatport for the given artist, or None if
        no result is found or the scrape fails. Never raises — this is best-effort.
        """
        try:
            response = self._client.get(_SEARCH_URL, params={"q": artist_name})
            time.sleep(_RATE_LIMIT_SLEEP)
            response.raise_for_status()
            match = _GENRE_RE.search(response.text)
            if match:
                return match.group(1).lower().strip()
            return None
        except httpx.HTTPError as exc:
            logger.debug("beatport_scrape_failed", artist=artist_name, error=str(exc))
            return None
        except Exception as exc:
            logger.debug("beatport_scrape_error", artist=artist_name, error=str(exc))
            return None

    def close(self) -> None:
        self._client.close()
