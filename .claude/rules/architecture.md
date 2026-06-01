---
globs: src/musicdl/**/*.py
---

# Architecture Rules

## Module Boundaries — never cross these

- **Raw SQL only in `database.py`.** All other modules call named `Database` methods (`get_track`, `upsert_track`, `mark_downloaded`, etc.). No `cursor.execute()` outside `database.py`.
- **`pipeline.py` is the only module that imports across sub-packages.** Sub-packages are isolated: `genre/` never imports from `downloader/`, `spotify/` never imports from `genre/`.
- **`cli.py` only calls `pipeline.py`.** It has no direct knowledge of Spotify, sldl, SQLite, or the filesystem.
- **`errors.py` is imported by every module.** No other cross-cutting imports.

## Data Flow Direction

```
cli.py → pipeline.py → {spotify/, genre/, downloader/, organizer/, tagger/, database.py}
```

Dependency arrows go one direction only. If you need logic from another sub-package, it belongs in `pipeline.py` or a new shared utility, not a cross-package import.

## Dataclass Immutability

`TrackMetadata` and `ResolvedGenre` are `frozen=True`. Never mutate them after creation.

To attach genre data to a track after resolution:
```python
# Correct — creates a new instance
track = dataclasses.replace(track, primary_genre="electronic", subgenre="techno")

# Wrong — will raise FrozenInstanceError
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

- **Per-track failures** in `pipeline.py` must be caught and logged (`logger.warning`), never re-raised. One failed track must not abort the session.
- **Fatal startup errors** (invalid config, sldl binary missing, DB can't open) raise a `MusicdlError` subclass. `cli.py` catches these and emits a clean error message via `click.ClickException`.
- **Never swallow exceptions silently.** At minimum: `logger.warning("...", exc_info=True)`.

## File Paths

- `pathlib.Path` everywhere. No `os.path`, no string concatenation for paths.
- User-visible paths in log output: `path.relative_to(Path.cwd())` so they're readable.
- Path components are always sanitized via `organizer.filesystem.sanitize()` before use.

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
- WARNING: fallback used (genre unknown, file moved with suffix), recoverable error
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
