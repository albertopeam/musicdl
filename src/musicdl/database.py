from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from collections.abc import Generator

from musicdl.errors import DatabaseError

# ---------------------------------------------------------------------------
# Typed row dataclasses — nothing outside this module sees sqlite3.Row or dict
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TrackRow:
    id: int
    spotify_track_id: str
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

    def get_track(self, spotify_track_id: str) -> TrackRow | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM tracks WHERE spotify_track_id = ?",
                (spotify_track_id,),
            ).fetchone()
        return _row_to_track(row) if row else None

    def upsert_track(
        self,
        spotify_track_id: str,
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
        status: str = "pending",
    ) -> None:
        with self._connect() as conn:
            with conn:
                conn.execute(
                    """
                    INSERT INTO tracks (
                        spotify_track_id, spotify_url, isrc, title, primary_artist,
                        all_artists, album_name, album_spotify_id, release_year,
                        duration_ms, track_number, disc_number, status
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(spotify_track_id) DO UPDATE SET
                        status = excluded.status,
                        updated_at = strftime('%Y-%m-%dT%H:%M:%SZ','now')
                    WHERE tracks.status NOT IN ('downloaded')
                    """,
                    (
                        spotify_track_id, spotify_url, isrc, title, primary_artist,
                        json.dumps(all_artists), album_name, album_spotify_id,
                        release_year, duration_ms, track_number, disc_number, status,
                    ),
                )

    def set_genre(
        self,
        spotify_track_id: str,
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
                    WHERE spotify_track_id = ?
                    """,
                    (primary_genre, subgenre, genre_source, spotify_track_id),
                )

    def mark_downloading(self, spotify_track_id: str) -> None:
        with self._connect() as conn:
            with conn:
                conn.execute(
                    "UPDATE tracks SET status = 'downloading', updated_at = strftime('%Y-%m-%dT%H:%M:%SZ','now') WHERE spotify_track_id = ?",
                    (spotify_track_id,),
                )

    def mark_downloaded(
        self,
        spotify_track_id: str,
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
                    WHERE spotify_track_id = ?
                    """,
                    (str(local_path), file_size_bytes, spotify_track_id),
                )

    def mark_failed(self, spotify_track_id: str, error: str) -> None:
        with self._connect() as conn:
            with conn:
                conn.execute(
                    """
                    UPDATE tracks
                    SET status = 'failed', last_error = ?,
                        retry_count = retry_count + 1,
                        updated_at = strftime('%Y-%m-%dT%H:%M:%SZ','now')
                    WHERE spotify_track_id = ?
                    """,
                    (error[:1000], spotify_track_id),
                )

    def mark_skipped(self, spotify_track_id: str) -> None:
        with self._connect() as conn:
            with conn:
                conn.execute(
                    "UPDATE tracks SET status = 'skipped', updated_at = strftime('%Y-%m-%dT%H:%M:%SZ','now') WHERE spotify_track_id = ?",
                    (spotify_track_id,),
                )

    def mark_not_found(self, spotify_track_id: str) -> None:
        with self._connect() as conn:
            with conn:
                conn.execute(
                    """
                    UPDATE tracks
                    SET status = 'not_found', retry_count = 0, last_error = NULL,
                        updated_at = strftime('%Y-%m-%dT%H:%M:%SZ','now')
                    WHERE spotify_track_id = ?
                    """,
                    (spotify_track_id,),
                )

    def should_download(
        self,
        spotify_track_id: str,
        max_retries: int,
        not_found_retry_days: int = 3,
    ) -> bool:
        row = self.get_track(spotify_track_id)
        if row is None:
            return True
        if row.status == "downloaded" and row.local_path is not None:
            if row.local_path.exists():
                return False
        if row.status == "failed" and row.retry_count >= max_retries:
            return False
        if row.status == "not_found":
            # Retry after cooldown period has elapsed
            with self._connect() as conn:
                result = conn.execute(
                    "SELECT julianday('now') - julianday(updated_at) FROM tracks WHERE spotify_track_id = ?",
                    (spotify_track_id,),
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


# ---------------------------------------------------------------------------
# Private deserialisation helpers
# ---------------------------------------------------------------------------


def _row_to_track(row: sqlite3.Row) -> TrackRow:
    return TrackRow(
        id=row["id"],
        spotify_track_id=row["spotify_track_id"],
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
