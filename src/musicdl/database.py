from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from collections.abc import Generator

from musicdl.errors import DatabaseError


class TrackStatus:
    PENDING      = "pending"
    DOWNLOADING  = "downloading"
    DOWNLOADED   = "downloaded"
    NOT_FOUND    = "not_found"
    FAILED       = "failed"
    SKIPPED      = "skipped"
    MISSING      = "missing"


# ---------------------------------------------------------------------------
# Typed row dataclasses — nothing outside this module sees sqlite3.Row or dict
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TrackRow:
    id: int
    track_id: str
    spotify_url: str
    isrc: str | None
    title: str
    primary_artist: str
    all_artists: list[str]
    album_name: str
    album_spotify_id: str
    release_year: int | None
    duration_ms: int | None
    track_number: int | None
    disc_number: int | None
    primary_genre: str | None
    subgenre: str | None
    genre_source: str | None
    status: str
    local_path: Path | None
    file_size_bytes: int | None
    downloaded_at: str | None
    last_error: str | None
    retry_count: int
    source: str
    spotify_id: str | None
    original_path: Path | None
    tags: list[str]


@dataclass(frozen=True)
class SessionRow:
    id: int
    input_file: str
    started_at: str
    finished_at: str | None
    total_tracks: int | None
    downloaded: int
    skipped: int
    failed: int


@dataclass(frozen=True)
class GenreCacheRow:
    artist_name: str
    primary_genre: str | None
    subgenre: str | None
    genre_source: str | None
    raw_tags: list[str]
    fetched_at: str
    ttl_days: int


# ---------------------------------------------------------------------------
# Schema migrations — each entry is SQL to run once, in order
# ---------------------------------------------------------------------------

_MIGRATIONS: list[str] = [
    # Version 1 — initial schema
    """
    CREATE TABLE tracks (
        id                  INTEGER PRIMARY KEY AUTOINCREMENT,
        spotify_track_id    TEXT NOT NULL UNIQUE,
        spotify_url         TEXT NOT NULL,
        isrc                TEXT,
        title               TEXT NOT NULL,
        primary_artist      TEXT NOT NULL,
        all_artists         TEXT NOT NULL,
        album_name          TEXT NOT NULL,
        album_spotify_id    TEXT NOT NULL,
        release_year        INTEGER,
        duration_ms         INTEGER,
        track_number        INTEGER,
        disc_number         INTEGER,
        primary_genre       TEXT,
        subgenre            TEXT,
        genre_source        TEXT,
        status              TEXT NOT NULL DEFAULT 'pending',
        local_path          TEXT,
        file_size_bytes     INTEGER,
        downloaded_at       TEXT,
        last_error          TEXT,
        retry_count         INTEGER NOT NULL DEFAULT 0,
        created_at          TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
        updated_at          TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
    );
    CREATE INDEX idx_tracks_status ON tracks(status);
    CREATE INDEX idx_tracks_album  ON tracks(album_spotify_id);
    """,
    # Version 2 — download sessions
    """
    CREATE TABLE sessions (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        input_file      TEXT NOT NULL,
        started_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
        finished_at     TEXT,
        total_tracks    INTEGER,
        downloaded      INTEGER NOT NULL DEFAULT 0,
        skipped         INTEGER NOT NULL DEFAULT 0,
        failed          INTEGER NOT NULL DEFAULT 0
    );
    """,
    # Version 3 — genre cache
    """
    CREATE TABLE genre_cache (
        artist_name     TEXT NOT NULL UNIQUE,
        primary_genre   TEXT,
        subgenre        TEXT,
        genre_source    TEXT,
        raw_tags        TEXT NOT NULL DEFAULT '[]',
        fetched_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
        ttl_days        INTEGER NOT NULL DEFAULT 30
    );
    CREATE INDEX idx_genre_cache_artist ON genre_cache(artist_name);
    """,
    # Version 4 — import support: rename primary key column, add source tracking,
    # add tags column for Last.fm track-level tags (used by DJ session micro-subgenre)
    """
    ALTER TABLE tracks RENAME COLUMN spotify_track_id TO track_id;
    ALTER TABLE tracks ADD COLUMN source         TEXT NOT NULL DEFAULT 'soulseek';
    ALTER TABLE tracks ADD COLUMN spotify_id     TEXT;
    ALTER TABLE tracks ADD COLUMN original_path  TEXT;
    ALTER TABLE tracks ADD COLUMN tags           TEXT NOT NULL DEFAULT '[]';
    UPDATE tracks SET spotify_id = track_id WHERE source = 'soulseek';
    """,
]


# ---------------------------------------------------------------------------
# Database class
# ---------------------------------------------------------------------------


class Database:
    def __init__(self, path: Path) -> None:
        self._path = path

    @contextmanager
    def _connect(self) -> Generator[sqlite3.Connection, None, None]:
        try:
            conn = sqlite3.connect(str(self._path), timeout=5.0)
        except sqlite3.Error as exc:
            raise DatabaseError(f"Cannot open database at {self._path}: {exc}") from exc
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            yield conn
        finally:
            conn.close()

    def migrate(self) -> None:
        with self._connect() as conn:
            version: int = conn.execute("PRAGMA user_version").fetchone()[0]
            for i, sql in enumerate(_MIGRATIONS[version:], start=version + 1):
                try:
                    conn.executescript(sql)
                    conn.execute(f"PRAGMA user_version = {i}")
                    conn.commit()
                except sqlite3.Error as exc:
                    raise DatabaseError(f"Migration {i} failed: {exc}") from exc

    # ------------------------------------------------------------------
    # Track CRUD
    # ------------------------------------------------------------------

    def get_track(self, track_id: str) -> TrackRow | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM tracks WHERE track_id = ?",
                (track_id,),
            ).fetchone()
        return _row_to_track(row) if row else None

    def get_track_by_local_path(self, local_path: Path) -> TrackRow | None:
        """Look up an imported track by its file path (dedup on re-import)."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM tracks WHERE local_path = ?",
                (str(local_path),),
            ).fetchone()
        return _row_to_track(row) if row else None

    def get_track_by_spotify_id(self, spotify_id: str) -> TrackRow | None:
        """Look up a track by its Spotify ID when track_id may be a synthetic local ID."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM tracks WHERE spotify_id = ?",
                (spotify_id,),
            ).fetchone()
        return _row_to_track(row) if row else None

    def upsert_track(
        self,
        track_id: str,
        spotify_url: str,
        title: str,
        primary_artist: str,
        all_artists: list[str],
        album_name: str,
        album_spotify_id: str,
        isrc: str | None = None,
        release_year: int | None = None,
        duration_ms: int | None = None,
        track_number: int | None = None,
        disc_number: int | None = None,
        status: str = TrackStatus.PENDING,
        source: str = "soulseek",
        spotify_id: str | None = None,
    ) -> None:
        resolved_spotify_id = spotify_id if spotify_id is not None else track_id
        with self._connect() as conn:
            with conn:
                conn.execute(
                    """
                    INSERT INTO tracks (
                        track_id, spotify_url, isrc, title, primary_artist,
                        all_artists, album_name, album_spotify_id, release_year,
                        duration_ms, track_number, disc_number, status,
                        source, spotify_id
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(track_id) DO UPDATE SET
                        status = excluded.status,
                        updated_at = strftime('%Y-%m-%dT%H:%M:%SZ','now')
                    WHERE tracks.status NOT IN ('downloaded')
                    """,
                    (
                        track_id, spotify_url, isrc, title, primary_artist,
                        json.dumps(all_artists), album_name, album_spotify_id,
                        release_year, duration_ms, track_number, disc_number, status,
                        source, resolved_spotify_id,
                    ),
                )

    def upsert_imported_track(
        self,
        track_id: str,
        title: str,
        primary_artist: str,
        all_artists: list[str],
        album_name: str,
        local_path: Path,
        duration_ms: int | None = None,
        release_year: int | None = None,
        track_number: int | None = None,
        isrc: str | None = None,
        primary_genre: str | None = None,
        subgenre: str | None = None,
        spotify_id: str | None = None,
        spotify_url: str = "",
        album_spotify_id: str = "",
    ) -> None:
        """Insert or update a locally imported track. Always sets status=downloaded."""
        with self._connect() as conn:
            with conn:
                conn.execute(
                    """
                    INSERT INTO tracks (
                        track_id, spotify_url, isrc, title, primary_artist,
                        all_artists, album_name, album_spotify_id, release_year,
                        duration_ms, track_number, disc_number, status,
                        source, spotify_id, local_path, original_path,
                        primary_genre, subgenre,
                        downloaded_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'downloaded',
                              'imported', ?, ?, ?, ?, ?, strftime('%Y-%m-%dT%H:%M:%SZ','now'))
                    ON CONFLICT(track_id) DO UPDATE SET
                        local_path   = excluded.local_path,
                        primary_genre = COALESCE(excluded.primary_genre, tracks.primary_genre),
                        subgenre     = COALESCE(excluded.subgenre, tracks.subgenre),
                        updated_at   = strftime('%Y-%m-%dT%H:%M:%SZ','now')
                    """,
                    (
                        track_id, spotify_url, isrc, title, primary_artist,
                        json.dumps(all_artists), album_name, album_spotify_id,
                        release_year, duration_ms, track_number, None,
                        spotify_id, str(local_path), str(local_path),
                        primary_genre, subgenre,
                    ),
                )

    def set_local_path(self, track_id: str, path: Path) -> None:
        with self._connect() as conn:
            with conn:
                conn.execute(
                    "UPDATE tracks SET local_path = ?, updated_at = strftime('%Y-%m-%dT%H:%M:%SZ','now') WHERE track_id = ?",
                    (str(path), track_id),
                )

    def set_genre(
        self,
        track_id: str,
        primary_genre: str,
        subgenre: str,
        genre_source: str,
    ) -> None:
        with self._connect() as conn:
            with conn:
                conn.execute(
                    """
                    UPDATE tracks
                    SET primary_genre = ?, subgenre = ?, genre_source = ?,
                        updated_at = strftime('%Y-%m-%dT%H:%M:%SZ','now')
                    WHERE track_id = ?
                    """,
                    (primary_genre, subgenre, genre_source, track_id),
                )

    def mark_downloading(self, track_id: str) -> None:
        with self._connect() as conn:
            with conn:
                conn.execute(
                    "UPDATE tracks SET status = 'downloading', updated_at = strftime('%Y-%m-%dT%H:%M:%SZ','now') WHERE track_id = ?",
                    (track_id,),
                )

    def mark_downloaded(
        self,
        track_id: str,
        local_path: Path,
        file_size_bytes: int,
    ) -> None:
        with self._connect() as conn:
            with conn:
                conn.execute(
                    """
                    UPDATE tracks
                    SET status = 'downloaded', local_path = ?, file_size_bytes = ?,
                        downloaded_at = strftime('%Y-%m-%dT%H:%M:%SZ','now'),
                        last_error = NULL,
                        updated_at = strftime('%Y-%m-%dT%H:%M:%SZ','now')
                    WHERE track_id = ?
                    """,
                    (str(local_path), file_size_bytes, track_id),
                )

    def mark_failed(self, track_id: str, error: str) -> None:
        with self._connect() as conn:
            with conn:
                conn.execute(
                    """
                    UPDATE tracks
                    SET status = 'failed', last_error = ?,
                        retry_count = retry_count + 1,
                        updated_at = strftime('%Y-%m-%dT%H:%M:%SZ','now')
                    WHERE track_id = ?
                    """,
                    (error[:1000], track_id),
                )

    def mark_skipped(self, track_id: str) -> None:
        with self._connect() as conn:
            with conn:
                conn.execute(
                    "UPDATE tracks SET status = 'skipped', updated_at = strftime('%Y-%m-%dT%H:%M:%SZ','now') WHERE track_id = ?",
                    (track_id,),
                )

    def mark_not_found(self, track_id: str) -> None:
        with self._connect() as conn:
            with conn:
                conn.execute(
                    """
                    UPDATE tracks
                    SET status = 'not_found', retry_count = 0, last_error = NULL,
                        updated_at = strftime('%Y-%m-%dT%H:%M:%SZ','now')
                    WHERE track_id = ?
                    """,
                    (track_id,),
                )

    def mark_missing(self, track_id: str) -> None:
        """Mark an imported track whose file can no longer be found on disk."""
        with self._connect() as conn:
            with conn:
                conn.execute(
                    "UPDATE tracks SET status = 'missing', updated_at = strftime('%Y-%m-%dT%H:%M:%SZ','now') WHERE track_id = ?",
                    (track_id,),
                )

    def should_download(
        self,
        track_id: str,
        max_retries: int,
        not_found_retry_days: int = 3,
    ) -> bool:
        row = self.get_track(track_id)
        if row is None:
            return True
        if row.status == TrackStatus.DOWNLOADED and row.local_path is not None:
            if row.local_path.exists():
                return False
            # File is gone. Imported tracks are not re-downloaded via Soulseek.
            if row.source == "imported":
                self.mark_missing(track_id)
                return False
        if row.status == TrackStatus.MISSING:
            return False
        if row.status == TrackStatus.FAILED and row.retry_count >= max_retries:
            return False
        if row.status == TrackStatus.NOT_FOUND:
            with self._connect() as conn:
                result = conn.execute(
                    "SELECT julianday('now') - julianday(updated_at) FROM tracks WHERE track_id = ?",
                    (track_id,),
                ).fetchone()
            days_since = result[0] if result else 0
            return days_since >= not_found_retry_days
        return True

    def reset_failed_tracks(self) -> int:
        with self._connect() as conn:
            with conn:
                cursor = conn.execute(
                    "UPDATE tracks SET status = 'pending', retry_count = 0, last_error = NULL WHERE status = 'failed'"
                )
                return cursor.rowcount

    def reset_not_found_tracks(self) -> int:
        with self._connect() as conn:
            with conn:
                cursor = conn.execute(
                    "UPDATE tracks SET status = 'pending' WHERE status = 'not_found'"
                )
                return cursor.rowcount

    def list_tracks_by_status(self, status: str) -> list[TrackRow]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM tracks WHERE status = ? ORDER BY primary_artist, album_name, track_number",
                (status,),
            ).fetchall()
        return [_row_to_track(r) for r in rows]

    def list_unclassified_tracks(self, mode: str = "unclassified") -> list[TrackRow]:
        """Return downloaded tracks that need genre classification.

        mode='unclassified': primary_genre is NULL or 'unknown'
        mode='reclassify':   same plus genre_source='fallback'
        mode='all':          every downloaded track
        """
        if mode == "unclassified":
            where = "(primary_genre IS NULL OR primary_genre = 'unknown')"
        elif mode == "reclassify":
            where = "(primary_genre IS NULL OR primary_genre = 'unknown' OR genre_source = 'fallback')"
        elif mode == "all":
            where = "1=1"
        else:
            raise DatabaseError(f"Unknown classify mode: {mode!r}")
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT * FROM tracks WHERE status = 'downloaded' AND {where} ORDER BY primary_artist",  # noqa: S608
            ).fetchall()
        return [_row_to_track(r) for r in rows]

    # ------------------------------------------------------------------
    # Session CRUD
    # ------------------------------------------------------------------

    def create_session(self, input_file: str) -> int:
        with self._connect() as conn:
            with conn:
                cursor = conn.execute(
                    "INSERT INTO sessions (input_file) VALUES (?)",
                    (input_file,),
                )
                return cursor.lastrowid  # type: ignore[return-value]

    def finish_session(
        self,
        session_id: int,
        total_tracks: int,
        downloaded: int,
        skipped: int,
        failed: int,
    ) -> None:
        with self._connect() as conn:
            with conn:
                conn.execute(
                    """
                    UPDATE sessions
                    SET finished_at = strftime('%Y-%m-%dT%H:%M:%SZ','now'),
                        total_tracks = ?, downloaded = ?, skipped = ?, failed = ?
                    WHERE id = ?
                    """,
                    (total_tracks, downloaded, skipped, failed, session_id),
                )

    def list_sessions(self, limit: int = 20) -> list[SessionRow]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM sessions ORDER BY started_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [_row_to_session(r) for r in rows]

    # ------------------------------------------------------------------
    # Genre cache
    # ------------------------------------------------------------------

    def get_genre_cache(self, artist_name: str, ttl_days: int) -> GenreCacheRow | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM genre_cache
                WHERE artist_name = ?
                  AND julianday('now') - julianday(fetched_at) < ?
                """,
                (artist_name, ttl_days),
            ).fetchone()
        return _row_to_genre_cache(row) if row else None

    def set_genre_cache(
        self,
        artist_name: str,
        primary_genre: str | None,
        subgenre: str | None,
        genre_source: str | None,
        raw_tags: list[str],
        ttl_days: int,
    ) -> None:
        with self._connect() as conn:
            with conn:
                conn.execute(
                    """
                    INSERT INTO genre_cache (artist_name, primary_genre, subgenre, genre_source, raw_tags, ttl_days)
                    VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(artist_name) DO UPDATE SET
                        primary_genre = excluded.primary_genre,
                        subgenre      = excluded.subgenre,
                        genre_source  = excluded.genre_source,
                        raw_tags      = excluded.raw_tags,
                        fetched_at    = strftime('%Y-%m-%dT%H:%M:%SZ','now'),
                        ttl_days      = excluded.ttl_days
                    """,
                    (
                        artist_name,
                        primary_genre,
                        subgenre,
                        genre_source,
                        json.dumps(raw_tags),
                        ttl_days,
                    ),
                )

    def clear_genre_cache_for_artist(self, artist_name: str) -> None:
        """Delete the genre cache entry for an artist (forces re-resolution on next classify)."""
        with self._connect() as conn:
            with conn:
                conn.execute(
                    "DELETE FROM genre_cache WHERE artist_name = ?",
                    (artist_name,),
                )


# ---------------------------------------------------------------------------
# Private deserialisation helpers
# ---------------------------------------------------------------------------


def _row_to_track(row: sqlite3.Row) -> TrackRow:
    keys = row.keys()
    return TrackRow(
        id=row["id"],
        track_id=row["track_id"],
        spotify_url=row["spotify_url"],
        isrc=row["isrc"],
        title=row["title"],
        primary_artist=row["primary_artist"],
        all_artists=json.loads(row["all_artists"]),
        album_name=row["album_name"],
        album_spotify_id=row["album_spotify_id"],
        release_year=row["release_year"],
        duration_ms=row["duration_ms"],
        track_number=row["track_number"],
        disc_number=row["disc_number"],
        primary_genre=row["primary_genre"],
        subgenre=row["subgenre"],
        genre_source=row["genre_source"],
        status=row["status"],
        local_path=Path(row["local_path"]) if row["local_path"] else None,
        file_size_bytes=row["file_size_bytes"],
        downloaded_at=row["downloaded_at"],
        last_error=row["last_error"],
        retry_count=row["retry_count"],
        source=row["source"] if "source" in keys else "soulseek",
        spotify_id=row["spotify_id"] if "spotify_id" in keys else None,
        original_path=Path(row["original_path"]) if ("original_path" in keys and row["original_path"]) else None,
        tags=json.loads(row["tags"]) if ("tags" in keys and row["tags"]) else [],
    )


def _row_to_session(row: sqlite3.Row) -> SessionRow:
    return SessionRow(
        id=row["id"],
        input_file=row["input_file"],
        started_at=row["started_at"],
        finished_at=row["finished_at"],
        total_tracks=row["total_tracks"],
        downloaded=row["downloaded"],
        skipped=row["skipped"],
        failed=row["failed"],
    )


def _row_to_genre_cache(row: sqlite3.Row) -> GenreCacheRow:
    return GenreCacheRow(
        artist_name=row["artist_name"],
        primary_genre=row["primary_genre"],
        subgenre=row["subgenre"],
        genre_source=row["genre_source"],
        raw_tags=json.loads(row["raw_tags"]),
        fetched_at=row["fetched_at"],
        ttl_days=row["ttl_days"],
    )
