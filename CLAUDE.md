# music-downloader — Claude Code Instructions

## Project Overview

Python CLI tool that reads Spotify URLs from a file, fetches track metadata, searches Soulseek via `sldl`, downloads at 320kbps MP3, organizes into genre/subgenre directories, and records everything in SQLite to avoid re-downloading. Target use: Serato DJ library management (electronic music focus).

## Stack Decisions (do not deviate without discussion)

| Concern | Choice | Reason |
|---|---|---|
| Package manager | `uv` | 10–100x faster than pip/poetry, 2025 standard |
| CLI framework | `click` | Established, clean error handling |
| Metadata source | `spotdl` library (reuse) | Handles unofficial Spotify auth without Premium |
| Soulseek download | `sldl` binary (subprocess) | Best-in-class, quality filtering built-in |
| Genre: primary | `pylast` (Last.fm) | Rich user tags, free API |
| Genre: fallback | `musicbrainzngs` (MusicBrainz) | Authoritative, free |
| Genre: electronic | Beatport scraper (best-effort) | 36+ electronic subgenres |
| Database | `sqlite3` stdlib + WAL mode | No ORM, no migrations tool needed |
| HTTP client | `httpx` | Async-ready, replaces requests |
| Retry logic | `tenacity` | Selective exception retry, backoff |
| Audio tagging | `mutagen` | ID3v2.4 writing |
| Config | `pydantic-settings` + TOML + env vars | Type-safe, env override |
| Logging | `structlog` | Processor pipeline, JSON-ready |
| Formatting | `black` (88 chars) | Modern standard |
| Type checking | `pyright` | Fast, strict |

## Project Layout

```
music-downloader/
├── src/
│   └── musicdl/
│       ├── __init__.py
│       ├── __main__.py         # `python -m musicdl` entry point
│       ├── cli.py              # Click group + all subcommands
│       ├── config.py           # pydantic-settings Config class
│       ├── pipeline.py         # Top-level orchestrator
│       ├── database.py         # SQLite schema + CRUD + migrations
│       ├── errors.py           # Custom exception hierarchy
│       ├── spotify/
│       │   ├── client.py       # spotdl wrapper for metadata
│       │   └── models.py       # TrackMetadata, ArtistStub dataclasses
│       ├── genre/
│       │   ├── resolver.py     # Waterfall: Last.fm → MusicBrainz → Beatport → fallback
│       │   ├── lastfm.py
│       │   ├── musicbrainz.py
│       │   ├── beatport.py     # Beatport HTML scraper (electronic subgenres)
│       │   ├── normalizer.py   # Raw tags → (primary_genre, subgenre) via taxonomy
│       │   ├── taxonomy.py     # Static GENRE_MAP dict + NOISE_TAGS set
│       │   └── cache.py        # SQLite-backed genre cache (30-day TTL)
│       ├── downloader/
│       │   ├── sldl.py         # subprocess wrapper, DownloadResult enum
│       │   └── detector.py     # Find downloaded file in staging dir
│       ├── organizer/
│       │   └── filesystem.py   # build_target_path, sanitize, move_to_library
│       └── tagger/
│           └── id3.py          # mutagen ID3v2.4 writer
├── tests/
│   ├── conftest.py
│   ├── unit/
│   └── integration/
├── pyproject.toml
├── uv.lock                     # Commit this
├── .env.example
├── config.example.toml
├── README.md                   # Must include legal disclaimer
├── LICENSE                     # MIT
└── ARCHITECTURE.md
```

**Always use `src/` layout.** Prevents accidental imports of the dev package.

## Coding Rules

### Types and data structures
- All public functions must have full type annotations (parameters + return type).
- Use `from __future__ import annotations` at the top of every file.
- Use `@dataclass(frozen=True)` for immutable value objects (TrackMetadata, ResolvedGenre).
- Use `pydantic.BaseModel` only when runtime validation or serialization is needed (config, API responses).
- Use `TypedDict` only for static unvalidated dicts (e.g., SQLite row shapes).
- Avoid `Any` — use `Unknown` or explicit union types instead.

### Error handling
- Use exceptions, not return codes.
- All application errors must subclass `MusicdlError` (defined in `errors.py`).
- Sub-hierarchy: `ConfigError`, `SpotifyError`, `GenreError`, `DownloadError`, `DatabaseError`.
- Use `click.ClickException` for user-facing CLI errors (clean output, no stacktrace).
- Let unexpected exceptions bubble up — Click will show a stacktrace in `--debug` mode.
- Never swallow exceptions silently. At minimum: `logger.warning("...", exc_info=True)`.

### Logging
- Use `structlog` everywhere. Import as `logger = structlog.get_logger()` at module level.
- DEBUG: loop iterations, internal state, retry attempts.
- INFO: API calls made, file moves, session start/end, DB writes.
- WARNING: degraded operation (genre lookup failed → using fallback).
- ERROR: user-facing failures.
- Never log secrets, API keys, or raw HTTP response bodies.

### SQLite
- Always enable WAL mode and foreign keys immediately after opening a connection.
- Use `sqlite3.Row` as `row_factory` so rows are accessible by column name.
- Use context managers for all connections (auto-commit/rollback).
- Schema migrations use the `PRAGMA user_version` pattern (see `database.py`).
- Never use string formatting for SQL values — always use parameterized queries (`?` placeholders).

### Subprocess (sldl)
- Never use `shell=True`.
- Always specify `timeout=`.
- Always specify `text=True, encoding="utf-8", errors="replace"`.
- Check result via `DownloadResult` enum, not raw return codes.
- Run preflight `sldl --version` check at startup.

### HTTP / external APIs
- Use `httpx.Client` (not `requests`).
- Wrap all external calls with `tenacity` retry (3 attempts, exponential backoff, retry on timeout + 429 + 503 only).
- Respect rate limits: Last.fm ≤ 5 req/s, MusicBrainz ≤ 1 req/s (hard limit — use `time.sleep(1)` between calls).
- Cache genre lookups in the `genre_cache` table (30-day TTL) to avoid repeated API calls on re-runs.

### File system
- Use `pathlib.Path` everywhere — no `os.path`.
- Sanitize all path components: strip `<>:"/\|?*\x00-\x1f`, lowercase, max 200 chars per segment.
- Never overwrite existing files — append `_2`, `_3`, etc. on collision.
- Use `target.parent.mkdir(parents=True, exist_ok=True)` before any write.

### Testing
- Test files live in `tests/unit/` (no I/O) and `tests/integration/` (real SQLite, real subprocess).
- Mock HTTP calls with `respx` (HTTPX-native).
- Mock subprocess calls with `pytest-mock`'s `mocker.patch`.
- Use `tmp_path` pytest fixture for all file system tests.
- Mark slow/network tests with `@pytest.mark.integration` and skip by default in CI.
- Aim for 70–80% unit test coverage on business logic. Don't chase 100%.

### Configuration
- Secrets (API keys) come from env vars only, prefixed `MUSICDL_`.
- Non-secret defaults live in `config.toml`.
- Never hardcode credentials or paths.
- Provide `.env.example` and `config.example.toml` with all keys documented.

### Comments and docstrings
- No comments explaining what the code does — well-named identifiers do that.
- Comments only for non-obvious WHY: hidden constraints, rate limit workarounds, protocol quirks.
- Public functions/classes: one-line Google-style docstring only if the name isn't self-explanatory.
- No multi-line docstring blocks on private functions.

## CLI Commands

```
musicdl download <input_file>   Download tracks from a Spotify URL file
  --config / -c                 Path to config.toml
  --output / -o                 Override output base dir
  --db                          Override SQLite DB path
  --dry-run                     Metadata + genre only, no download
  --retry-failed                Retry previously failed tracks
  --force                       Re-download even if already present
  --verbose / -v                Debug logging

musicdl status                  Show session history and counts
musicdl retry                   Re-queue all failed tracks
musicdl init-config             Write config.toml template
```

## Legal Requirements

The README **must** contain this disclaimer verbatim:

> This tool does not circumvent any DRM or technical protection measure. Users are solely responsible for compliance with applicable copyright laws and the terms of service of any platform. Only download music you own or have the right to download.

No DRM circumvention may ever be added to this codebase. Credentials must never be hardcoded.

## Running the Project

```bash
# Setup
uv sync

# Run
uv run musicdl download urls.txt

# Tests
uv run pytest tests/unit/
uv run pytest tests/ -m integration   # requires network + sldl binary

# Type check
uv run pyright src/

# Format
uv run black src/ tests/
```

## Environment Variables

```
MUSICDL_LASTFM_API_KEY      Last.fm API key (required)
MUSICDL_LASTFM_API_SECRET   Last.fm API secret (required)
MUSICDL_MB_USER_AGENT       MusicBrainz user agent string (required by MB ToS)
```

Spotify metadata is fetched via `spotdl`'s unofficial flow — no Spotify credentials needed.
