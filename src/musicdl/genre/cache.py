from __future__ import annotations

from dataclasses import dataclass

from musicdl.database import Database, GenreCacheRow


@dataclass(frozen=True)
class CachedGenre:
    primary: str
    subgenre: str
    source: str


class GenreCache:
    def __init__(self, db: Database, ttl_days: int = 30) -> None:
        self._db = db
        self._ttl = ttl_days
        # In-process cache — avoids repeated DB reads within one session
        self._mem: dict[str, CachedGenre] = {}

    def get(self, artist_name: str) -> CachedGenre | None:
        key = artist_name.lower()
        if key in self._mem:
            return self._mem[key]
        row: GenreCacheRow | None = self._db.get_genre_cache(artist_name, self._ttl)
        if row is None:
            return None
        cached = CachedGenre(
            primary=row.primary_genre or "unknown",
            subgenre=row.subgenre or "unknown",
            source=row.genre_source or "unknown",
        )
        self._mem[key] = cached
        return cached

    def set(
        self,
        artist_name: str,
        primary: str,
        subgenre: str,
        source: str,
        raw_tags: list[str],
    ) -> None:
        self._db.set_genre_cache(
            artist_name=artist_name,
            primary_genre=primary,
            subgenre=subgenre,
            genre_source=source,
            raw_tags=raw_tags,
            ttl_days=self._ttl,
        )
        self._mem[artist_name.lower()] = CachedGenre(primary=primary, subgenre=subgenre, source=source)
