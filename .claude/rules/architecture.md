---
globs: src/musicdl/**/*.py
---

# Architecture

## System Overview

```
[input.txt]          Text file, one Spotify URL per line
     ↓
[cli.py]             Parse args, load config, open DB, create session
     ↓
[pipeline.py]        Main loop — one track at a time
     ├── spotify/client.py       Expand URL → flat list of TrackMetadata
     ├── database.py             Check status → skip if already downloaded
     ├── genre/resolver.py       Artist name → (primary_genre, subgenre)
     ├── downloader/sldl.py      Invoke sldl binary → download to staging/
     ├── downloader/detector.py  Find new .mp3 in staging, verify duration
     ├── tagger/id3.py           Write ID3v2.4 tags via mutagen
     ├── organizer/filesystem.py Move to music/{genre}/{subgenre}/{artist}/
     └── database.py             Record local_path, size, status=downloaded

[pipeline_import.py] Import existing files (no download step)
     ├── importer/scanner.py     Scan dir → read tags → ScannedFile
     ├── spotify/client.py       ISRC lookup → TrackMetadata (optional)
     ├── database.py             3-level dedup → upsert_imported_track
     └── genre/resolver.py       Classify genres (if --classify)
```

## Module Responsibilities

- **`pipeline.py`** — download orchestrator. Owns the main loop, catches per-track errors without aborting the session. No I/O itself.
- **`pipeline_import.py`** — import + classify orchestrator. Mirrors pipeline.py's role for local files.
- **`database.py`** — all SQLite access. No raw SQL anywhere else. Named methods only.
- **`spotify/client.py`** — wraps spotdl's `Spotify` class. Handles track/album/playlist URLs → flat `list[TrackMetadata]`. Also `lookup_by_isrc()` for import.
- **`genre/resolver.py`** — waterfall resolution: genre_cache → Last.fm → MusicBrainz → Beatport → fallback. Caches per artist, TTL 30 days.
- **`genre/normalizer.py` + `genre/taxonomy.py`** — pure functions, no I/O. `GENRE_MAP` maps ~100 tag strings to `(primary, subgenre)` tuples. `NOISE_TAGS` filters junk Last.fm tags.
- **`downloader/sldl.py`** — builds sldl command, runs subprocess, diffs staging dir for new files. Returns `SldlResult`.
- **`downloader/detector.py`** — given new files in staging + `TrackMetadata`, picks best match by duration (±3s via mutagen).
- **`organizer/filesystem.py`** — builds target path, sanitizes components, moves without overwriting.
- **`tagger/id3.py`** — writes ID3v2.4 tags (TIT2, TPE1, TALB, TDRC, TRCK, TPOS, TCON, TSRC). Also `write_genre_tag()` for classify-only updates.
- **`importer/scanner.py`** — scans directories, reads ID3/Vorbis/iTunes tags into `ScannedFile`. Generates stable synthetic track IDs.

## Data Model

```python
@dataclass(frozen=True)
class ArtistStub:
    spotify_id: str
    name: str

@dataclass(frozen=True)
class TrackMetadata:
    track_id: str           # Spotify track ID for downloaded; synthetic for imported
    spotify_url: str
    title: str
    artists: tuple[ArtistStub, ...]   # first = primary
    album_name: str
    album_spotify_id: str
    release_year: int
    duration_ms: int
    track_number: int
    disc_number: int
    isrc: str | None
    primary_genre: str | None = None
    subgenre: str | None = None

@dataclass(frozen=True)
class ResolvedGenre:
    primary: str
    subgenre: str
    source: str          # "lastfm" | "musicbrainz" | "beatport" | "spotify" | "unknown"
    confidence: float
    raw_tags: tuple[str, ...]

@dataclass(frozen=True)
class ScannedFile:          # importer/scanner.py
    path: Path
    title: str | None
    artist: str | None
    album: str | None
    year: int | None
    track_number: int | None
    isrc: str | None
    genre: str | None
    duration_ms: int | None
    file_format: str        # "mp3" | "flac" | "m4a" | "wav"
```

## Database Schema

Current schema after all migrations:

```sql
CREATE TABLE tracks (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    track_id            TEXT NOT NULL UNIQUE,   -- dedup key; Spotify ID or synthetic
    spotify_url         TEXT NOT NULL,
    isrc                TEXT,
    title               TEXT NOT NULL,
    primary_artist      TEXT NOT NULL,
    all_artists         TEXT NOT NULL,          -- JSON array
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
    -- pending | downloading | downloaded | not_found | failed | skipped | missing
    local_path          TEXT,
    file_size_bytes     INTEGER,
    downloaded_at       TEXT,
    last_error          TEXT,
    retry_count         INTEGER NOT NULL DEFAULT 0,
    created_at          TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    updated_at          TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    source              TEXT NOT NULL DEFAULT 'soulseek',  -- 'soulseek' | 'imported'
    spotify_id          TEXT,          -- always set for soulseek; set on import if ISRC matched
    original_path       TEXT,          -- file path at import time
    tags                TEXT NOT NULL DEFAULT '[]'  -- JSON array of Last.fm tags (DJ session use)
);

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

CREATE TABLE genre_cache (
    artist_name     TEXT NOT NULL UNIQUE,
    primary_genre   TEXT,
    subgenre        TEXT,
    genre_source    TEXT,
    raw_tags        TEXT NOT NULL DEFAULT '[]',
    fetched_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    ttl_days        INTEGER NOT NULL DEFAULT 30
);
```

### Track ID scheme

- **Soulseek downloads**: `track_id` = Spotify track ID (also stored in `spotify_id`)
- **Imported, ISRC matched**: `track_id` = Spotify track ID from ISRC lookup (also in `spotify_id`)
- **Imported, ISRC unmatched**: `track_id` = `isrc:{code}` or `local:{sha256(artist|title|filename)[:16]}`
- **Synthetic IDs use metadata hash**, not content hash — stable when files are cloud-synced (Google Drive, iCloud) and not fully cached locally

### Track status reference

| Status | Source | Meaning |
|---|---|---|
| `pending` | any | Queued, not yet attempted |
| `downloading` | soulseek | In progress |
| `downloaded` | soulseek/imported | On disk, all good |
| `not_found` | soulseek | Not on Soulseek; auto-retried after 3 days |
| `failed` | soulseek | Hard error (timeout, connection); retry with `--retry-failed` |
| `skipped` | soulseek | Already downloaded, skipped this session |
| `missing` | imported | File was imported but can no longer be found at its path |

### Skip / deduplication logic

```python
def should_download(track_id, not_found_retry_days) -> bool:
    row = db.get_track(track_id)
    if row is None:
        return True
    if row.status == "downloaded" and row.local_path is not None:
        if row.local_path.exists():
            return False          # on disk — skip
        if row.source == "imported":
            db.mark_missing(track_id)
            return False          # imported file gone — never re-queue for Soulseek
    if row.status == "missing":
        return False
    if row.status == "not_found":
        return updated_at < now - not_found_retry_days  # auto-retry after cooldown
    if row.status == "failed" and row.retry_count >= config.max_retries:
        return False              # exhausted retries
    return True                   # pending, failed with retries remaining, etc.
```

### Import deduplication (three levels)

```python
existing = db.get_track(track_id)               # level 1: by track_id
if existing is None and spotify_id:
    existing = db.get_track_by_spotify_id(...)  # level 2: by spotify_id
if existing is None:
    existing = db.get_track_by_local_path(...)  # level 3: by file path
```

## Module Boundaries — never cross these

- **Raw SQL only in `database.py`.** No `cursor.execute()` anywhere else.
- **`pipeline.py` and `pipeline_import.py` are the only modules that import across sub-packages.** Sub-packages are isolated: `genre/` never imports from `downloader/`, `spotify/` never imports from `genre/`.
- **`cli.py` wires up service objects and calls pipeline modules — nothing else.** It may construct `GenreResolver`, `SpotifyClient`, `SldlDownloader`, etc. and pass them to pipeline functions, but contains no business logic: no direct DB queries, no file operations, no Soulseek/Spotify calls.
- **`errors.py` is imported by every module.** No other cross-cutting imports.

## Data Flow Direction

```
cli.py → pipeline.py / pipeline_import.py → {spotify/, genre/, downloader/, organizer/, tagger/, importer/, database.py}
```

Dependency arrows go one direction only. If logic is needed from another sub-package, it belongs in a pipeline module or a new shared utility.

## Dataclass Immutability

`TrackMetadata` and `ResolvedGenre` are `frozen=True`. Never mutate them after creation.

```python
# Correct — creates a new instance
track = dataclasses.replace(track, primary_genre="electronic", subgenre="techno")

# Wrong — raises FrozenInstanceError
track.primary_genre = "electronic"
```

## SQLite Rules

Every connection must open with:
```python
conn.execute("PRAGMA journal_mode = WAL")
conn.execute("PRAGMA foreign_keys = ON")
conn.row_factory = sqlite3.Row
```

- Always use `?` placeholders — never f-strings or `.format()` in SQL.
- Wrap writes in `with conn:` — auto-commits on exit, rolls back on exception.
- All schema changes go through the `PRAGMA user_version` migration list in `database.py`.

## Error Propagation

- **Per-track failures** in pipeline modules must be caught and logged (`logger.warning`), never re-raised. One failed track must not abort the session.
- **Fatal startup errors** (invalid config, sldl binary missing, DB can't open) raise a `MusicdlError` subclass. `cli.py` catches these and emits a clean error via `click.ClickException`.
- **Never swallow exceptions silently.** At minimum: `logger.warning("...", exc_info=True)`.

## Error Hierarchy

```
MusicdlError
├── ConfigError          Missing/invalid config or env var
├── SpotifyError         URL parsing or metadata fetch failure
├── GenreError           All genre sources failed (non-fatal — falls back to unknown)
├── DownloadError        sldl failure (non-fatal per track)
│   ├── NotFoundError    No results on Soulseek
│   └── TimeoutError     sldl exceeded timeout
└── DatabaseError        SQLite operation failure (fatal)
```

## File Paths

- `pathlib.Path` everywhere. No `os.path`, no string concatenation for paths.
- User-visible paths in log output: `path.relative_to(Path.cwd())` so they're readable.
- Path components always sanitized via `organizer.filesystem.sanitize()` before use.

## Concurrency Constraints

- No `asyncio` in this codebase.
- Single-threaded in the download loop — one sldl call at a time (one Soulseek connection per account).
- The only concurrency is `ThreadPoolExecutor(max_workers=3)` in genre prefetching, before the download loop.

## Type Annotation Rules

- `from __future__ import annotations` at the top of every file.
- All public function signatures fully annotated (parameters + return type).
- No bare `Any`. Use explicit union types or `Unknown` where the type is genuinely variable.
- Prefer `str | None` over `Optional[str]` (Python 3.10+ union syntax).

## Logging Rules

```python
logger = structlog.get_logger()  # module-level, not inside functions
```

- DEBUG: loop iterations, retry attempts, cache hits
- INFO: track processed, genre resolved, file moved, session summary
- WARNING: fallback used, recoverable error
- ERROR: track failed, session-level failure
- Never log API keys, file contents, or raw HTTP bodies.

## Rate Limits to Enforce in Code

| Service | Limit | Implementation |
|---|---|---|
| MusicBrainz | 1 req/s hard limit | `time.sleep(1)` inside `musicbrainz.py` after every call |
| Last.fm | ~5 req/s | Token bucket or `time.sleep(0.2)` in `lastfm.py` |
| Beatport (scraping) | Unknown | `time.sleep(2)` between calls in `beatport.py` |

## External Binary (sldl) Contract

`sldl` is called with these flags — do not remove them:
- `--no-progress` — suppresses ANSI escape sequences that poison stdout parsing
- `--skip-existing` — defense-in-depth on top of the DB deduplication check
- `--length-tolerance 3` — prevents downloading wrong tracks with colliding titles
- `text=True, encoding="utf-8", errors="replace"` — always on subprocess.run calls
- `timeout=` — always specified, never omitted

## External Dependencies

| Dependency | Version | Purpose |
|---|---|---|
| `spotdl` | ≥4.2 | Spotify metadata without Premium |
| `pylast` | ≥5.2 | Last.fm genre tags |
| `musicbrainzngs` | ≥0.7 | MusicBrainz genre fallback |
| `mutagen` | ≥1.47 | ID3 tagging + duration reading |
| `click` | ≥8.1 | CLI |
| `httpx` | ≥0.27 | HTTP client for Beatport scraper |
| `tenacity` | ≥8.2 | Retry logic |
| `pydantic-settings` | ≥2.0 | Config + env var parsing |
| `structlog` | ≥24.0 | Logging |
| `rich` | ≥13.0 | Progress bars, colored output |
| `sldl` (binary) | latest | Soulseek download engine |

## Output Directory Structure

Files go into **`music/{genre}/{subgenre}/{NN - title}.mp3`** — flat, no artist or album subdirectory.

```
music/
├── electronic/
│   ├── deep house/
│   │   └── 01 - glue.mp3
│   ├── techno/
│   │   └── 01 - doppler.mp3
│   └── drum and bass/
│       └── 01 - inner city life.mp3
└── hip-hop/
    └── rap/
        └── 01 - n.y. state of mind.mp3
```

**Why flat:** Serato DJ scans genre directories and expects tracks at one level deep inside them. Deep nesting (artist/album subdirs) breaks Serato's library scanner.

All directory and file names are **lowercased**. Unsafe filesystem characters replaced with `_`. Max 200 chars per path segment. Never overwrite — append `_2`, `_3` on collision.

## Status String Constants

Track status values must be defined as constants in `database.py`, not repeated as string literals across modules:

```python
class TrackStatus:
    PENDING      = "pending"
    DOWNLOADING  = "downloading"
    DOWNLOADED   = "downloaded"
    NOT_FOUND    = "not_found"
    FAILED       = "failed"
    SKIPPED      = "skipped"
    MISSING      = "missing"
```

**Why:** Typos in status strings fail silently — a wrong string produces no type error, just broken DB queries or missed matches. Use `TrackStatus.DOWNLOADED`, not `"downloaded"`.
