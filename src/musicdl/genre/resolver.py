from __future__ import annotations

from dataclasses import dataclass

import structlog

from musicdl.genre.beatport import BeatportScraper
from musicdl.genre.cache import CachedGenre, GenreCache
from musicdl.genre.lastfm import LastFmClient
from musicdl.genre.musicbrainz import MusicBrainzClient
from musicdl.genre.normalizer import classify
from musicdl.genre.taxonomy import GENRE_MAP

logger = structlog.get_logger()


@dataclass(frozen=True)
class ResolvedGenre:
    primary: str
    subgenre: str
    source: str


_UNKNOWN = ResolvedGenre(primary="unknown", subgenre="unknown", source="unknown")


class GenreResolver:
    def __init__(
        self,
        lastfm: LastFmClient,
        musicbrainz: MusicBrainzClient,
        beatport: BeatportScraper,
        cache: GenreCache,
    ) -> None:
        self._lastfm = lastfm
        self._mb = musicbrainz
        self._beatport = beatport
        self._cache = cache

    def resolve(
        self,
        artist_name: str,
        spotify_genres: list[str] | None = None,
    ) -> ResolvedGenre:
        cached: CachedGenre | None = self._cache.get(artist_name)
        if cached is not None:
            logger.debug("genre_cache_hit", artist=artist_name, genre=cached.primary)
            return ResolvedGenre(primary=cached.primary, subgenre=cached.subgenre, source=cached.source)

        result = (
            self._try_lastfm(artist_name)
            or self._try_musicbrainz(artist_name)
            or self._try_beatport(artist_name)
            or self._try_spotify(spotify_genres or [])
            or _UNKNOWN
        )

        self._cache.set(
            artist_name=artist_name,
            primary=result.primary,
            subgenre=result.subgenre,
            source=result.source,
            raw_tags=[],
        )
        logger.info(
            "genre_resolved",
            artist=artist_name,
            genre=result.primary,
            subgenre=result.subgenre,
            source=result.source,
        )
        return result

    def _try_lastfm(self, artist_name: str) -> ResolvedGenre | None:
        try:
            tags = self._lastfm.get_top_tags(artist_name)
        except Exception as exc:
            logger.warning("lastfm_genre_failed", artist=artist_name, error=str(exc))
            return None

        match = classify(tags)
        if match:
            return ResolvedGenre(primary=match[0], subgenre=match[1], source="lastfm")
        return None

    def _try_musicbrainz(self, artist_name: str) -> ResolvedGenre | None:
        try:
            tags = self._mb.get_artist_genres(artist_name)
        except Exception as exc:
            logger.warning("musicbrainz_genre_failed", artist=artist_name, error=str(exc))
            return None

        match = classify(tags)
        if match:
            return ResolvedGenre(primary=match[0], subgenre=match[1], source="musicbrainz")
        return None

    def _try_beatport(self, artist_name: str) -> ResolvedGenre | None:
        raw = self._beatport.get_genre(artist_name)
        if raw is None:
            return None
        # Beatport genres are already clean strings; try normaliser
        match = classify([raw])
        if match:
            return ResolvedGenre(primary=match[0], subgenre=match[1], source="beatport")
        # Accept the raw Beatport genre directly as electronic subgenre
        if raw in GENRE_MAP.values() or any(raw == v[1] for v in GENRE_MAP.values()):
            return ResolvedGenre(primary="electronic", subgenre=raw, source="beatport")
        return None

    def _try_spotify(self, genres: list[str]) -> ResolvedGenre | None:
        if not genres:
            return None
        match = classify(genres)
        if match:
            return ResolvedGenre(primary=match[0], subgenre=match[1], source="spotify")
        return None
