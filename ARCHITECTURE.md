# Architecture

## System Overview

```
[input.txt]          Text file, one Spotify URL per line
     ↓
[cli.py]             Parse args, load config, open DB, create session
     ↓
[pipeline.py]        Main loop — one track at a time
     ├── spotify/client.py     Expand URL → flat list of TrackMetadata
     ├── database.py           Check status → skip if already downloaded
     ├── genre/resolver.py     Artist name → (primary_genre, subgenre)
     ├── downloader/sldl.py    Invoke sldl binary → download to staging/
     ├── downloader/detector.py  Find new .mp3 in staging, verify duration
     ├── tagger/id3.py         Write ID3v2.4 tags via mutagen
     ├── organizer/filesystem.py  Move to music/{genre}/{subgenre}/{artist}/
     └── database.py           Record local_path, size, status=downloaded
```

## Module Responsibilities

### `pipeline.py`
Single entry point for a download run. Owns the main loop and orchestrates all other modules. Does no I/O itself — delegates everything. Catches per-track errors without aborting the session.

### `database.py`
All SQLite access lives here. No raw SQL outside this module. Provides named methods (`get_track`, `upsert_track`, `mark_downloaded`, `mark_failed`, etc.). Runs migrations on first open via `PRAGMA user_version`.

### `spotify/client.py`
Wraps `spotdl`'s `Spotify` class. Handles the three URL types (track, album, playlist) and always returns a flat `list[TrackMetadata]`. Handles pagination for playlists >100 tracks. Filters `None` entries from playlists (removed tracks).

### `genre/resolver.py`
Waterfall resolution — stops at first confident result:
1. `genre_cache` table (SQLite, TTL 30 days)
2. Last.fm `artist.getTopTags` → filter weight ≥ 30 → normalize
3. MusicBrainz artist genres → normalize
4. Beatport scraper → parse genre label (electronic artists only)
5. Spotify artist genres from spotdl metadata
6. Fallback: `("unknown", "unknown")`

Result is cached per artist name. Genre prefetching (concurrent Last.fm calls) runs before the download loop.

### `genre/normalizer.py` + `genre/taxonomy.py`
Pure functions, no I/O. `GENRE_MAP` maps ~100 normalized tag strings to `(primary, subgenre)` tuples. `NOISE_TAGS` filters non-genre tags from Last.fm ("seen live", "favorite", etc.). `normalizer.classify()` walks tags by weight and returns the first hit.

### `downloader/sldl.py`
Builds the `sldl` CLI command, runs it via `subprocess.run` with timeout, diffs staging dir before/after to find new files. Returns a `SldlResult(outcome, downloaded_files, stdout, stderr)`. Does not move or tag files.

### `downloader/detector.py`
Given a list of new files in staging and a `TrackMetadata`, picks the best match by duration check (via `mutagen`) within ±3 seconds. Returns `None` if no confident match.

### `organizer/filesystem.py`
Builds target path: `base/{genre}/{subgenre}/{artist}/{NN - title}.mp3`. Sanitizes all components. Moves file without overwriting (suffix `_2`, `_3` on collision).

### `tagger/id3.py`
Writes ID3v2.4 tags using `mutagen`. Tags written: TIT2 (title), TPE1 (artist), TALB (album), TDRC (year), TRCK (track number), TPOS (disc), TCON (genre), TSRC (ISRC). Pure function: takes `Path` + `TrackMetadata`, no return value.

## Data Model

```python
@dataclass(frozen=True)
class ArtistStub:
    spotify_id: str
    name: str

@dataclass(frozen=True)
class TrackMetadata:
    spotify_track_id: str
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
    # Populated after genre resolution:
    primary_genre: str | None = None
    subgenre: str | None = None

@dataclass(frozen=True)
class ResolvedGenre:
    primary: str
    subgenre: str
    source: str          # "lastfm" | "musicbrainz" | "beatport" | "spotify" | "unknown"
    confidence: float    # 0.0–1.0
    raw_tags: tuple[str, ...]
```

## Database Schema

```sql
-- Primary download record. UNIQUE(spotify_track_id) is the deduplication key.
CREATE TABLE tracks (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    spotify_track_id    TEXT NOT NULL UNIQUE,
    spotify_url         TEXT NOT NULL,
    isrc                TEXT,
    title               TEXT NOT NULL,
    primary_artist      TEXT NOT NULL,
    all_artists         TEXT NOT NULL,        -- JSON array
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
    -- pending | downloading | downloaded | failed | skipped
    local_path          TEXT,
    file_size_bytes     INTEGER,
    downloaded_at       TEXT,
    last_error          TEXT,
    retry_count         INTEGER NOT NULL DEFAULT 0,
    created_at          TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    updated_at          TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);

-- One row per CLI invocation.
CREATE TABLE sessions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    input_file      TEXT NOT NULL,
    started_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    finished_at     TEXT,
    total_tracks    INTEGER,
    downloaded      INTEGER DEFAULT 0,
    skipped         INTEGER DEFAULT 0,
    failed          INTEGER DEFAULT 0
);

-- Keyed by artist name. Prevents repeated API calls across runs.
CREATE TABLE genre_cache (
    artist_name     TEXT NOT NULL UNIQUE,
    primary_genre   TEXT,
    subgenre        TEXT,
    genre_source    TEXT,
    raw_tags        TEXT,                     -- JSON array
    fetched_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    ttl_days        INTEGER NOT NULL DEFAULT 30
);
```

## Skip / Deduplication Logic

```python
def should_download(db, spotify_track_id: str) -> bool:
    row = db.get_track(spotify_track_id)
    if row is None:
        return True
    if row["status"] == "downloaded" and row["local_path"]:
        if Path(row["local_path"]).exists():
            return False   # on disk and recorded — skip
    if row["status"] == "failed" and row["retry_count"] >= config.max_retries:
        return False       # exhausted retries
    return True            # pending, failed with retries remaining, or missing file
```

## Directory Structure Example

```
music/
├── electronic/
│   ├── deep house/
│   │   └── bicep/
│   │       └── bicep/
│   │           └── 01 - glue.mp3
│   ├── techno/
│   │   └── charlotte de witte/
│   │       └── 01 - doppler.mp3
│   └── drum and bass/
│       └── goldie/
│           └── timeless/
│               └── 01 - inner city life.mp3
└── hip-hop/
    └── rap/
        └── nas/
            └── illmatic/
                └── 01 - n.y. state of mind.mp3
```

All directory and file names are **lowercased**. Unsafe filesystem characters replaced with `_`.

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

## Rate Limit Summary

| Service | Limit | Enforcement |
|---|---|---|
| Last.fm | ~5 req/s | Token bucket in `lastfm.py` |
| MusicBrainz | 1 req/s (hard) | `time.sleep(1)` between calls |
| Beatport (scraping) | No published limit | `time.sleep(2)` between calls |
| Spotify (via spotdl) | Internal | spotdl handles it |
| Soulseek | Connection-based | `sldl` handles it internally |

## Error Hierarchy

```
MusicdlError
├── ConfigError          Missing/invalid config or env var
├── SpotifyError         Spotify URL parsing or metadata fetch failure
├── GenreError           All genre sources failed (non-fatal — falls back to unknown)
├── DownloadError        sldl failure (non-fatal per track)
│   ├── NotFoundError    No results on Soulseek
│   └── TimeoutError     sldl exceeded timeout
└── DatabaseError        SQLite operation failure (fatal)
```

## Concurrency Model

The pipeline is intentionally **single-threaded** for downloads:
- sldl uses a single Soulseek connection — parallel calls would conflict.
- MusicBrainz enforces 1 req/s — concurrency provides no benefit.
- SQLite WAL mode handles concurrent reads, but we never need them.

**Exception:** Genre prefetching runs 3 concurrent Last.fm calls via `ThreadPoolExecutor` before the download loop starts, since Last.fm allows ~5 req/s and the genre cache population is network-bound.
