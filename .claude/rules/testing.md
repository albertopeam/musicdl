---
globs: tests/**/*.py, tests/**/conftest.py
---

# Test Writing Rules

## Layout

- `tests/unit/` — pure logic only. No I/O, no network, no real SQLite, no subprocess.
- `tests/integration/` — real SQLite via `tmp_path`, real subprocess (requires `sldl` binary). Always mark with `@pytest.mark.integration`.
- Mirror source structure: `tests/unit/test_database.py` tests `src/musicdl/database.py`.
- Function names: `test_<function>_<scenario>` — e.g. `test_should_download_returns_false_when_file_exists`.

## Standard Fixtures (conftest.py)

Define these in `tests/conftest.py` — available to all tests:

```python
@pytest.fixture
def tmp_db(tmp_path) -> Database:
    """Fresh SQLite DB per test, schema migrated, WAL enabled."""
    db = Database(tmp_path / "test.db")
    db.migrate()
    return db

@pytest.fixture
def sample_track() -> TrackMetadata:
    """Realistic frozen TrackMetadata for use in assertions."""
    return TrackMetadata(
        spotify_track_id="4uLU6hMCjMI75M1A2tKUQC",
        spotify_url="https://open.spotify.com/track/4uLU6hMCjMI75M1A2tKUQC",
        title="Glue",
        artists=(ArtistStub(spotify_id="1", name="Bicep"),),
        album_name="Bicep",
        album_spotify_id="abc123",
        release_year=2017,
        duration_ms=375000,
        track_number=1,
        disc_number=1,
        isrc="GBAHT1700555",
    )

@pytest.fixture
def staging_dir(tmp_path) -> Path:
    """Empty temp directory simulating sldl's --path output."""
    d = tmp_path / "staging"
    d.mkdir()
    return d
```

Never share mutable fixtures across test modules. Never use `autouse=True` for fixtures that cause side effects.

## HTTP Mocking — use `respx`

```python
import respx, httpx

def test_beatport_scraper_returns_genre(respx_mock):
    respx_mock.get("https://www.beatport.com/search").mock(
        return_value=httpx.Response(200, text="<html>...techno...</html>")
    )
    result = BeatportScraper().get_genre("Charlotte de Witte")
    assert result == ("electronic", "techno")
```

Never use `responses` (requests-only) or `httpretty` (socket-level, fragile).

## Subprocess Mocking — use `pytest-mock`

```python
from subprocess import CompletedProcess

def test_sldl_returns_not_found_when_no_results(mocker, staging_dir, sample_track):
    mocker.patch("subprocess.run", return_value=CompletedProcess(
        args=[], returncode=1,
        stdout="No results found for query", stderr=""
    ))
    result = SldlDownloader(binary="sldl", staging_dir=staging_dir, quality="320", timeout=30).download(sample_track)
    assert result.outcome == DownloadResult.NOT_FOUND
```

Patch at the call site (`musicdl.downloader.sldl.subprocess.run`), not the stdlib directly.

## SQLite Tests — always use real DB

```python
def test_mark_downloaded_sets_local_path(tmp_db, sample_track):
    tmp_db.upsert_track(sample_track, status="pending")
    tmp_db.mark_downloaded(sample_track.spotify_track_id, Path("/music/track.mp3"), 9_500_000)
    row = tmp_db.get_track(sample_track.spotify_track_id)
    assert row["status"] == "downloaded"
    assert row["local_path"] == "/music/track.mp3"
```

Never mock `sqlite3`. It's fast, and mocks miss constraint violations and transaction rollback behavior.

## File System Tests — always use `tmp_path`

```python
def test_move_to_library_does_not_overwrite(tmp_path):
    existing = tmp_path / "music/electronic/house/track.mp3"
    existing.parent.mkdir(parents=True)
    existing.write_bytes(b"original")
    source = tmp_path / "staging/track.mp3"
    source.write_bytes(b"new")
    result = move_to_library(source, existing)
    assert result.name == "track_2.mp3"
    assert existing.read_bytes() == b"original"
```

## What to Test

Cover every branch of these — they are the core logic:

- `database.should_download()`: None row, downloaded+file exists, downloaded+file missing, failed+retries exhausted, failed+retries remaining
- `GenreResolver.resolve()`: cache hit, Last.fm success, MusicBrainz fallback, Beatport fallback, all-unknown
- `sanitize()`: empty string, only unsafe chars, exactly 200 chars, unicode, leading/trailing spaces
- `build_target_path()`: known genre+subgenre, unknown genre, missing album name
- `SldlDownloader._classify_outcome()`: each `DownloadResult` variant
- `SpotifyClient.expand_url()`: track, album, playlist URL types, and an invalid URL raising `ValueError`
- `GenreNormalizer.classify()`: matched tag, noise tag filtered, no match returns `None`

## What to Skip

- Testing that `pylast` or `spotipy` make correct HTTP calls — those libraries have their own test suites.
- Testing `mutagen` ID3 writing at the unit level — verify in integration tests only.
- 100% branch coverage on `cli.py` — Click's testing utilities are fragile; test the pipeline under the CLI, not the CLI wiring itself.
- Testing the `GENRE_MAP` dictionary contents — it's data, not logic.

## Coverage Target

75–80% on `src/musicdl/` for unit tests. Do not chase 100%.

```bash
uv run pytest tests/unit/ --cov=src/musicdl --cov-report=term-missing
uv run pytest tests/ -m integration  # requires sldl binary on PATH
```

## Parametrize for Multiple Scenarios

```python
@pytest.mark.parametrize("raw,expected", [
    ("House",           "house"),
    ("Drum & Bass",     "drum & bass"),
    ("",                "unknown"),
    ("A" * 300,         "a" * 200),
    ("rock/metal",      "rock_metal"),
])
def test_sanitize(raw, expected):
    assert sanitize(raw) == expected
```

Prefer `parametrize` over duplicated test functions for the same function with different inputs.
