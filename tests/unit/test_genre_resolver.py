from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from musicdl.errors import GenreError
from musicdl.genre.cache import GenreCache
from musicdl.genre.resolver import GenreResolver, ResolvedGenre, _UNKNOWN


def _make_resolver(
    lastfm_tags: list[str] | Exception | None = None,
    mb_tags: list[str] | Exception | None = None,
    beatport_genre: str | None = None,
    cache: GenreCache | None = None,
) -> GenreResolver:
    lastfm = MagicMock()
    if isinstance(lastfm_tags, Exception):
        lastfm.get_top_tags.side_effect = lastfm_tags
    else:
        lastfm.get_top_tags.return_value = lastfm_tags or []

    mb = MagicMock()
    if isinstance(mb_tags, Exception):
        mb.get_artist_genres.side_effect = mb_tags
    else:
        mb.get_artist_genres.return_value = mb_tags or []

    beatport = MagicMock()
    beatport.get_genre.return_value = beatport_genre

    if cache is None:
        cache = MagicMock()
        cache.get.return_value = None

    return GenreResolver(lastfm=lastfm, musicbrainz=mb, beatport=beatport, cache=cache)


class TestGenreResolverWaterfall:
    def test_returns_cached_result(self) -> None:
        cache = MagicMock()
        cache.get.return_value = MagicMock(primary="electronic", subgenre="techno", source="lastfm")
        resolver = _make_resolver(cache=cache)
        result = resolver.resolve("Charlotte de Witte")
        assert result.primary == "electronic"
        assert result.subgenre == "techno"

    def test_lastfm_hit_skips_mb_and_beatport(self) -> None:
        resolver = _make_resolver(lastfm_tags=["techno"])
        result = resolver.resolve("Bicep")
        assert result.primary == "electronic"
        assert result.subgenre == "techno"
        assert result.source == "lastfm"

    def test_falls_back_to_musicbrainz(self) -> None:
        resolver = _make_resolver(lastfm_tags=[], mb_tags=["house"])
        result = resolver.resolve("Bicep")
        assert result.source == "musicbrainz"
        assert result.primary == "electronic"

    def test_falls_back_to_beatport(self) -> None:
        resolver = _make_resolver(lastfm_tags=[], mb_tags=[], beatport_genre="techno")
        result = resolver.resolve("Charlotte de Witte")
        assert result.source == "beatport"

    def test_falls_back_to_spotify_genres(self) -> None:
        resolver = _make_resolver(lastfm_tags=[], mb_tags=[], beatport_genre=None)
        result = resolver.resolve("SomeArtist", spotify_genres=["drum and bass"])
        assert result.primary == "electronic"
        assert result.source == "spotify"

    def test_returns_unknown_when_all_fail(self) -> None:
        resolver = _make_resolver(lastfm_tags=[], mb_tags=[], beatport_genre=None)
        result = resolver.resolve("SomeArtist", spotify_genres=[])
        assert result.primary == "unknown"
        assert result.source == "unknown"

    def test_lastfm_exception_falls_through(self) -> None:
        resolver = _make_resolver(lastfm_tags=GenreError("api down"), mb_tags=["techno"])
        result = resolver.resolve("Bicep")
        assert result.source == "musicbrainz"
