# DJ Session Builder — Design Plan

Status: **Design phase. Not implemented.** This document captures refined design decisions and open questions before implementation begins.

---

## What this builds

Two CLI commands on top of the existing downloaded music library:

```bash
musicdl analyze [--all | --track ID | --genre electronic/house]
musicdl session --style electronic/house --micro soulful --arc warm_up --duration 60
```

`analyze` extracts BPM, musical key, and energy from each MP3 file and writes the results both to the database and back into the file's ID3 tags (so Serato picks them up automatically). `session` reads those analysis results and generates an ordered tracklist optimised for DJ mixing.

---

## Deliverable 1 — `musicdl analyze`

This is the higher-value deliverable for Serato use. The session generator is useless without it, and Serato can read the tags immediately without the session generator being built at all.

### Audio feature extraction

Three features per track:

**BPM**
Use `librosa.beat.beat_track()`. This is prone to octave errors (detecting 87.5 BPM for a 175 BPM DnB track). Apply genre-aware octave correction after detection:

```python
GENRE_BPM_RANGES = {
    "house":              (115, 135),
    "tech house":         (122, 135),
    "deep house":         (115, 128),
    "techno":             (128, 150),
    "drum and bass":      (160, 185),
    "trance":             (128, 145),
    "ambient":            (60, 120),
}

def correct_octave(bpm: float, subgenre: str | None) -> tuple[float, bool]:
    """
    Returns (corrected_bpm, was_corrected).
    Checks bpm × 2 and bpm / 2 against the genre expected range.
    Stores whether correction was applied for transparency.
    """
```

If subgenre is unknown, leave BPM as-is and store low confidence.

**Musical key**
librosa has no built-in key detector. Must implement the Krumhansl-Schmuckler (1990) algorithm:
1. Extract chromagram with `librosa.feature.chroma_cqt()`
2. Average across time → 12-element pitch class profile
3. Correlate against 24 templates (12 major + 12 minor key profiles)
4. Pick highest correlation → key + mode + confidence

Accuracy is ~60–70% on diverse music. For electronic music with strong tonal centres it's higher. This is honest enough for a DJ tool if we show confidence alongside the result.

Key detector is isolated in `session/key_detector.py` so it can be replaced with a subprocess call to `keyfinder-cli` (open source, significantly more accurate) if accuracy proves insufficient in practice.

**Energy**
Use RMS energy from `librosa.feature.rms()`. Normalized per-genre to a 0.0–1.0 percentile scale (post-analysis pass). Raw RMS is stored alongside so normalization can be recomputed if the genre assignment changes.

### Serato integration (primary output)

After analysis, write results back to the MP3 file using standard ID3 tags that Serato reads:

| ID3 frame | Content | Serato reads it as |
|---|---|---|
| `TBPM` | BPM as integer string | Tempo |
| `TKEY` | Camelot notation e.g. `"8B"` | Key (harmonic mixing) |

Both are standard ID3v2.4 frames. `tagger/id3.py` already uses mutagen — add `TBPM` and `TKEY` writes to the existing `tag_file()` function (or a new `tag_analysis()` function to keep concerns separate).

This means `musicdl analyze` writes to two places: the `audio_analysis` DB table and the file's ID3 tags. Serato picks up the tags on next library rescan with no further steps.

### Energy normalization pass

After bulk analysis, run a normalization pass:
1. For each subgenre, collect all `energy_rms` values from analyzed tracks
2. Compute percentile rank for each track within its subgenre
3. Write `energy_normalized` (0.0–1.0 percentile) back to `audio_analysis`

This makes the arc preset energy targets (e.g., 0.8 = "high energy") meaningful and consistent across libraries.

---

## Deliverable 2 — `musicdl session`

### Three-level style hierarchy

```
primary_genre / subgenre / micro_subgenre

Examples:
  --style electronic/house            any house track
  --style electronic/house            + --micro soulful  → soulful house only
  --style electronic/techno           + --micro minimal  → minimal techno
  --style electronic/drum\ and\ bass  + --micro liquid   → liquid DnB
```

### Micro-subgenre source

Last.fm provides micro-subgenre signals. Two levels:

1. **Track-level tags** (`track.getTopTags()`) — more accurate, but often missing for obscure tracks
2. **Artist-level tags** (`artist.getTopTags()`) — already cached in `genre_cache`, used as fallback

The plan (which stores only artist-level tags) needs to be augmented: during `musicdl analyze`, fetch track-level tags for each downloaded track and store them in a new `tags TEXT` column on the `tracks` table. Artist-level tags from `genre_cache` are the fallback if track tags are sparse (<3 tags with weight ≥30).

The `session/micro_taxonomy.py` module maps these raw tags to canonical micro-subgenre labels. The mapping is **context-aware** — the subgenre is passed in to disambiguate ambiguous tags:

```python
def classify_micro(tags: list[str], subgenre: str | None) -> str | None:
    """
    Returns the best micro-subgenre match.
    Bare tags like 'jazzy' only resolve to 'jazzy house' when subgenre='house'.
    Returns None if no confident match.
    """
```

### Session generation algorithm

**Not purely greedy.** Greedy algorithms can trap themselves in harmonically isolated corners when the library is small (which it will be early on). Use a scoring function instead of hard constraints.

**Phase 1 — Energy zoning**
Divide the target duration into N equal slots mapped to the arc preset. Assign each candidate track to its best-fit energy slot based on `energy_normalized`. Slots are soft — a track ±0.15 from the slot target is a valid candidate.

**Phase 2 — Scored harmonic sequencing within each slot**
```python
def transition_score(
    from_track: AnalysisRow,
    to_track: AnalysisRow,
    slot_target_energy: float,
) -> float:
    return (
        camelot_score(from_track, to_track) * 0.5   # key compatibility
        + bpm_score(from_track.bpm, to_track.bpm)   * 0.3   # tempo proximity
        + energy_score(to_track.energy_normalized, slot_target_energy) * 0.2
    )
```

All three are continuous 0.0–1.0 scores, not binary gates. The algorithm always finds a next track — it never dead-ends. Transitions where `camelot_score == 0.0` are flagged as `[FREE MIX]` in the output — the DJ knows to handle those manually.

**Camelot compatibility scores:**

| Relationship | Score | Description |
|---|---|---|
| Same position | 1.0 | Perfect key match |
| ±1 number, same mode | 0.75 | Adjacent key, natural |
| Same number, opposite mode | 0.5 | Relative major/minor, creative |
| +7 positions | 0.5 | Energy boost interval |
| Incompatible | 0.0 | Flag as FREE MIX |

**Phase 3 — Stitch zones together**
Connect adjacent energy zone boundaries with a transitional track chosen to bridge both energy levels and maintain Camelot compatibility. If no bridge track exists, accept a FREE MIX at the zone boundary.

### Library size floor

If the filtered candidate pool has fewer than 8 tracks, warn and suggest loosening filters rather than silently generating a poor session:

```
Warning: only 4 tracks match electronic/house/soulful. Session quality will be limited.
  Try: --micro jazzy (8 tracks available)
  Try: drop --micro flag (23 tracks available)
```

### Output

**Console table** (always shown):
```
 #   Title                          Artist         BPM    Key   Energy  Transition
 1   Glue                           Bicep          124    6A    ▅▅▅▅▅▅   —
 2   Quartz                         Mall Grab      123    7A    ▅▅▅▅▅▅   → 7A (+1)
 3   Over Again                     Mall Grab      126    6A    ▅▅▅▅▅▅   → 6A (=)
 4   Something I Can't Have         Mood II Swing  124    2A    ████████  [FREE MIX]
```

**Extended M3U** (optional, `--output session.m3u`):
```m3u
#EXTM3U
#EXT-X-SESSION:arc=warm_up,style=electronic/house,micro=soulful,generated=2026-06-02
#EXTINF:375,Bicep - Glue
#EXTBPM:124
#EXTKEY:6A
/Users/alberto/Music/electronic/deep house/bicep/01 - glue.mp3
```

---

## Session Storage and Annotation

### Rationale

Generated sessions should be stored in the database so that:
1. The user can review, annotate, and rate past sessions
2. The generator can avoid repeating the same session (same tracks in different order)
3. The generator can learn from annotated poor sessions to avoid repeating bad patterns

### Quality annotation

Store a quality enum, not a numeric grade.

**Why not a grade (1–5)?**
A grade is context-dependent — a session might score 5/5 for a warm-up set but 1/5 for peak hour. Reducing a session to a number loses the context. A grade can be added later as an enhancement (see Open Questions below) once we understand the usage patterns.

**Quality enum:**
```
unrated   — default, not yet listened/evaluated
good      — worked well, usable as a reference
poor      — finished the set but something was wrong (bad transitions, energy wrong, etc.)
broken    — unusable — wrong genre, key detection errors, totally wrong for the context
```

The distinction between `poor` and `broken` matters for the generator:
- `broken` sessions should never be reproduced in any form
- `poor` sessions can inform parameter avoidance but aren't necessarily all wrong

### Database schema (additions via migration 5)

```sql
-- DJ sessions (separate from download sessions)
CREATE TABLE dj_sessions (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    name                TEXT,                       -- optional user label
    style               TEXT,                       -- "electronic/house"
    micro_subgenre      TEXT,                       -- "soulful", nullable
    arc                 TEXT,                       -- "warm_up", "peak_hour", etc.
    duration_min        INTEGER,                    -- target duration in minutes
    track_count         INTEGER NOT NULL,
    generation_params   TEXT NOT NULL,              -- JSON: all params used to generate
    track_set_hash      TEXT NOT NULL,              -- SHA-256 of sorted(spotify_track_ids)
                                                    -- enables same-set detection regardless of order
    quality             TEXT NOT NULL DEFAULT 'unrated',
    quality_notes       TEXT,                       -- free text from user
    rated_at            TEXT,
    generated_at        TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);

-- Ordered track list per session (the actual sequence)
CREATE TABLE dj_session_tracks (
    session_id          INTEGER NOT NULL REFERENCES dj_sessions(id) ON DELETE CASCADE,
    position            INTEGER NOT NULL,
    spotify_track_id    TEXT NOT NULL REFERENCES tracks(spotify_track_id),
    camelot             TEXT,           -- "8B" — snapshot at generation time
    bpm                 REAL,           -- snapshot at generation time
    transition_score    REAL,           -- score computed by the generator
    is_free_mix         INTEGER NOT NULL DEFAULT 0,  -- 1 = no key compatibility, flagged
    PRIMARY KEY (session_id, position)
);
```

Also add to `tracks` table (migration 4 — done in Deliverable 1):
```sql
ALTER TABLE tracks ADD COLUMN tags TEXT DEFAULT '[]';
-- JSON array of normalized Last.fm tag strings (track-level preferred, artist-level fallback)
```

### `track_set_hash` — duplicate detection

The hash is computed from the **sorted** list of `spotify_track_id` values. This means the same set of tracks in different order produces the same hash.

```python
import hashlib, json

def compute_track_set_hash(track_ids: list[str]) -> str:
    canonical = json.dumps(sorted(track_ids), separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()[:16]  # 16 hex = 64 bits, enough for dedup
```

Before saving a new session, the generator checks:
1. Exact match: `track_set_hash` already exists → warn "This session contains the same tracks as session #N from YYYY-MM-DD"
2. Similarity: Jaccard similarity against recent sessions > 0.7 → warn "This session shares 70%+ tracks with session #N"

The user can override and save anyway, or ask for a different generation.

### How the generator uses quality history

**Anti-repetition (track level)**
When building the candidate pool, penalise tracks that have appeared recently:

```python
def recency_penalty(track_id: str, recent_sessions: list[DjSessionRow]) -> float:
    """
    Returns 0.0 (no penalty) to 1.0 (full penalty).
    Full penalty if track appeared in the most recent session.
    Decays linearly: 0.5 after 3 sessions, 0.0 after 6 sessions.
    """
```

This doesn't prevent reuse — it just makes recently-used tracks less likely to appear at the top of the candidate list. Configurable window (default: last 5 sessions).

**Anti-repetition (session level)**
Computed via `track_set_hash` (see above). If the new generation would produce a hash matching an existing session, retry with randomised starting track.

**Learning from poor/broken sessions**
When a session is rated `poor` or `broken`, the generator stores `generation_params` (arc, style, micro, duration, etc.) alongside the quality annotation. Before generating a new session, the generator:

1. Retrieves `poor`/`broken` sessions with matching style
2. Logs a warning if the new request uses the same arc + micro combination as a recently rated-poor session:
   ```
   Note: session #12 (2026-05-20) with arc=peak_hour/micro=soulful was rated 'poor'.
   Quality notes: "soulful tracks weren't energetic enough for peak hour"
   Continue anyway? [y/N]
   ```
3. Does NOT automatically change the request — the user decides. But they're informed.

This is conservative: we don't try to automatically fix the algorithm from quality feedback (that requires understanding WHY something was poor, which is hard to infer). We just surface the history so the user can make an informed decision.

---

## Module Structure

```
src/musicdl/session/
├── __init__.py
├── analyzer.py           # Orchestrator: load MP3 → BPM → key → energy → write to DB + ID3
├── key_detector.py       # Krumhansl-Schmuckler algorithm on librosa chroma features
│                         # Strategy: can swap in keyfinder-cli subprocess if accuracy insufficient
├── bpm_corrector.py      # Octave correction using genre BPM ranges
├── camelot.py            # Camelot Wheel number/mode lookup + compatibility scoring
├── micro_taxonomy.py     # Raw tag → canonical micro-subgenre (context-aware, subgenre parameter)
├── arc.py                # Energy arc presets + slot assignment + stitch logic
├── generator.py          # Two-phase generation: energy zones → scored harmonic sequencing
│                         # + history-awareness: recency penalty, hash dedup, poor-session warning
├── session_store.py      # DB read/write for dj_sessions + dj_session_tracks
└── export.py             # Extended M3U + console table
```

`session_store.py` is the database boundary for this module — raw SQL only here, not in generator.py or export.py. Follows the same pattern as `database.py` for the download pipeline.

---

---

## Library Import and Classification

### Core principle: source-agnostic library

A track is a track. The only structural difference between a Soulseek download and a local file is how it was acquired. Once it's in the database, every subsequent step — genre resolution, audio analysis, ID3 tagging, session generation — operates identically on both.

Source is a column, not an architecture.

The modules `genre/resolver.py`, `session/analyzer.py`, `tagger/id3.py`, and `organizer/filesystem.py` are already source-agnostic — they operate on artist names, file paths, or DB records. Only `downloader/sldl.py` and the download loop in `pipeline.py` are source-specific. Everything else is reused unchanged.

### Track identity problem — the `spotify_track_id` rename

The current schema uses `spotify_track_id` as both the primary deduplication key AND the Spotify-specific ID. These are two different things conflated into one column. An imported file that can't be matched to Spotify still needs a stable, unique identity — it just can't be a Spotify ID.

**Decision: rename `spotify_track_id` → `track_id` and add a separate nullable `spotify_id` column.**

This is a schema migration (see migration 4 below). It also requires a full-codebase rename of the Python field name in `TrackRow`, `TrackMetadata`, all `Database` method parameters, and all SQL queries. This is significant churn (~30 call sites in database.py, pipeline.py, and tests) but it's the right time — before the session module is built.

**`track_id` value formats by source:**

| Source | `track_id` format | Example |
|---|---|---|
| Soulseek download | Spotify ID verbatim (backward compatible) | `4uLU6hMCjMI75M1A2tKUQC` |
| Imported + Spotify match (ISRC or search) | Spotify ID verbatim | `4uLU6hMCjMI75M1A2tKUQC` |
| Imported + ISRC, no Spotify match | `isrc:{ISRC_CODE}` | `isrc:GBUM71029604` |
| Imported + no ISRC, no Spotify match | `local:{sha256[:16]}` | `local:8a3f2b9c4d1e6f7a` |

Backward compatible: existing rows keep their current values (plain Spotify IDs). No data migration needed for existing records.

**New columns added to `tracks` (migration 4):**

```sql
ALTER TABLE tracks RENAME COLUMN spotify_track_id TO track_id;

ALTER TABLE tracks ADD COLUMN source TEXT NOT NULL DEFAULT 'soulseek';
-- 'soulseek' | 'imported'

ALTER TABLE tracks ADD COLUMN spotify_id TEXT;
-- The actual Spotify track ID when known. For soulseek tracks: same as track_id.
-- For imported tracks with Spotify match: populated. For local-only: NULL.

ALTER TABLE tracks ADD COLUMN original_path TEXT;
-- Absolute path at import time, before any organize step. Audit trail.
-- NULL for soulseek downloads.

-- spotify_url: effectively nullable for local-only tracks.
-- Enforce at application layer: imported tracks without Spotify match store '' for spotify_url.
```

SQLite supports `ALTER TABLE RENAME COLUMN` since v3.25.0 (2018). macOS ships SQLite ≥3.39. Safe to use.

### Import pipeline — `musicdl import <dir_or_file>`

For each `.mp3`, `.flac`, `.m4a`, or `.wav` file found:

**Step 1 — Read existing ID3 tags** (via mutagen):
Collect title, primary artist, album, year, track number, ISRC (TSRC), existing genre (TCON), existing BPM (TBPM), duration.

**Step 2 — Spotify matching** (best-effort, skip with `--no-spotify`):

1. **ISRC match** (deterministic): `SpotifyClient.lookup_by_isrc(isrc)` — new method. ISRC is exact; always auto-accept.
2. **Title + artist search**: `SpotifyClient.search(title, artist)` — new method. Accept only if title similarity AND artist similarity are above threshold AND durations are within 5 seconds. See Open Questions for threshold values.
3. **No match**: proceed with local tags only, generate `isrc:` or `local:` track_id.

If Spotify match found and that `spotify_id` already exists in DB: update `local_path` if existing file is gone; otherwise warn "already in library" and skip.

**Step 3 — Deduplication check**:
1. Look up `track_id` in DB → if found: update `local_path` if changed, skip upsert.
2. If no `track_id` match but `spotify_id` known: look up by `spotify_id` → same merge logic.
3. If title + artist + duration closely matches an existing record: warn "probable duplicate of #N" but still import.

**Step 4 — Upsert DB record**:
```
source        = 'imported'
status        = 'downloaded'   # file already exists
local_path    = absolute path to file
original_path = same as local_path at import time
```
Genre fields: populated from TCON tag if it maps to a known taxonomy entry, otherwise NULL. Do not run the full genre resolver at import time — that is `musicdl classify`'s job.

**Step 5 — Console output per file**:
```
  IMPORTED   Bicep — Glue                → matched Spotify: 4uLU6hMCjMI75M1A2tKUQC
  IMPORTED   Some Local Track            → no Spotify match, id: local:8a3f2b
  SKIP       Charlotte de Witte — Doppler → already in library
  DUPLICATE? Unknown Artist — Track 1    → similar to: Charlotte de Witte — Track 1 (#42)
```

**Step 6 — Final tally**:
```
Import complete. Imported: 47  Skipped: 12  Duplicates flagged: 2  Matched to Spotify: 39
Run `musicdl classify --unclassified` to resolve genres for 8 unmatched tracks.
```

### `musicdl classify` — genre resolution for existing library tracks

Runs the full genre resolver (`genre/resolver.py`) for tracks that don't have confident genre data. Works on both imported and downloaded tracks. This is the replacement for manual tagging — instead of categorising 500 tracks by hand, run classify and let Last.fm/MusicBrainz do it.

```
musicdl classify [--unclassified | --reclassify | --all] [--dry-run]
  --unclassified   Only tracks with primary_genre NULL or 'unknown' (default)
  --reclassify     Also re-run tracks with genre_source='fallback'
  --all            Re-run all tracks, overwrite existing classifications
  --dry-run        Show what would be classified, don't write
```

After resolving genre for each track:
1. Update `primary_genre`, `subgenre`, `genre_source` in DB
2. Write `TCON` tag to the file via `tagger/id3.py`

**Respects existing tags by default.** If a track already has TCON that maps to a known taxonomy entry, classify treats it as valid and skips it (acts like a cache hit). Use `--reclassify` to override.

### Status lifecycle for imported tracks

```
imported → status = 'downloaded'   (file exists at local_path)
         → status = 'missing'      (file no longer at local_path)
```

`missing` is a new status (added in migration 4). A `missing` imported track is never queued for Soulseek download — the user must locate or re-import the file. The `should_download()` guard: if `source = 'imported'` AND file is missing, return `False` and update status to `missing`.

For soulseek tracks where `status = 'downloaded'` but file is gone, the existing re-download logic is unchanged.

### Organize command — deferred

`musicdl organize` would move imported files into the standard `music/{genre}/{subgenre}/{artist}/` directory structure using the existing `organizer/filesystem.py`. Deferred because:

1. Serato stores absolute file paths. Moving files silently breaks the Serato library. The user must run Serato's "Relocate Lost Files" after any moves — this needs to be understood and documented before implementing.
2. Many DJ libraries have existing organisation the user prefers.

When implemented: prominent warning before any moves, `--dry-run` mode, and `--copy` flag to leave originals in place.

### New CLI commands summary

```
musicdl import <path> [--no-spotify] [--classify] [--analyze] [--dry-run]
  Scan a directory (or single file) and import into the library database.
  --no-spotify    Skip Spotify matching (fully offline, faster)
  --classify      Run genre resolution immediately after import
  --analyze       Run audio analysis immediately after import
  --dry-run       Show what would be imported without writing to DB

musicdl classify [--unclassified | --reclassify | --all] [--dry-run]
  Resolve genres for library tracks that lack confident genre data.
  Works on both imported and downloaded tracks.
```

### New code required

| File | Change |
|---|---|
| `database.py` | Migration 4 SQL + rename column throughout all methods + `mark_missing()` + `should_download()` guard for imported tracks |
| `spotify/client.py` | `lookup_by_isrc(isrc)` + `search(title, artist)` methods |
| `pipeline_import.py` | New orchestrator for the import flow, analogous to `pipeline.py` |
| `cli.py` | `import` and `classify` subcommands |
| All Python files referencing `spotify_track_id` | Rename field to `track_id` |

---

## Database Migration Sequence

| Migration | When | What |
|---|---|---|
| 1–3 | Already shipped | tracks, sessions, genre_cache |
| 4 | Import feature | Rename `spotify_track_id` → `track_id`; add `source`, `spotify_id`, `original_path` columns; add `missing` status; add `tags TEXT DEFAULT '[]'` column |
| 5 | Analyze deliverable | `CREATE TABLE audio_analysis` |
| 6 | Session deliverable | `CREATE TABLE dj_sessions` + `CREATE TABLE dj_session_tracks` |

---

## pyproject.toml additions

```toml
[project.optional-dependencies]
session = [
    "librosa>=0.10",
    "soundfile>=0.12",
    "numpy>=1.26",
]
```

Install with: `uv sync --extra session`

`librosa` depends on `numba` for JIT compilation. First run after install will be slow (~30s) while numba compiles. Subsequent runs are fast. This is expected and should be documented.

`essentia` has superior key detection but has no pure-Python wheels on macOS ARM. Start with librosa + K-S. If key accuracy proves insufficient in practice, the isolated `key_detector.py` module can add a `keyfinder-cli` backend without touching the rest of the code.

---

## CLI commands

```
musicdl analyze [--all | --track <spotify_id>] [--genre <style>] [--force]
  Extract BPM, key, energy for downloaded tracks that haven't been analyzed yet.
  --all                 Analyze all downloaded tracks
  --track               Analyze a specific track by Spotify ID
  --genre               Analyze only tracks matching this genre (e.g. electronic/house)
  --force               Re-analyze even if already analyzed (e.g. after algorithm update)
  Writes TBPM and TKEY ID3 tags to the MP3 files.
  Shows progress bar (can be slow: ~2–5s per track).

musicdl session [options]
  --duration MINUTES    Target set length (default: 60)
  --style GENRE         Genre filter: "electronic/house", "electronic/techno", etc.
  --micro LABEL         Micro-subgenre: "soulful", "jazzy", "minimal", "liquid", etc.
  --arc PRESET          Energy arc: warm_up | peak_hour | cool_down | full_set
  --start-bpm BPM       Start near this BPM (optional)
  --output FILE         Write M3U playlist (default: console only)
  --save                Save session to DB for future reference
  --name NAME           Label for the saved session

musicdl session list [--quality good|poor|broken|unrated] [--style GENRE]
  List saved sessions with quality annotations.

musicdl session rate <id> <good|poor|broken> [--notes "text"]
  Annotate a saved session with a quality rating.
```

---

## Open Questions

These are unresolved design decisions. Document the decision here when resolved.

**Grade vs enum for session quality**
Current choice: `good | poor | broken | unrated` enum. A numeric grade (1–5) would allow weighted track selection in future (tracks appearing in 5-star sessions get a small probability boost). Held back because grades are context-dependent (warm-up vs peak vs cool-down) and it's unclear how to normalize them. Revisit after real usage — if users consistently want to express "pretty good" vs "great", add a grade alongside the enum.

**Key detection accuracy floor**
Krumhansl-Schmuckler gives ~60–70% accuracy. Should we require a minimum `key_confidence` before writing `TKEY` to the file and using the result in session generation? Proposal: only write `TKEY` to file if `key_confidence >= 0.6`. For session generation, use tracks with any key confidence but weight the `camelot_score` component by `key_confidence` (lower confidence → lower key weight in the transition score). Decide after testing on real library.

**Re-analysis on algorithm update**
The `analyzer_version` column enables detecting when tracks were analyzed with an older algorithm. Should `musicdl analyze` automatically re-analyze when `analyzer_version != current_version`, or only on `--force`? Proposal: auto re-analyze in background (don't block the session generator). Implement after first algorithm change is needed.

**Anti-repetition window**
How many sessions back to consider for the recency penalty? Default: 5. Should be configurable in `config.toml` under `[session]`.

**Micro-subgenre on sessions with quality history**
If a session tagged `poor` had `micro=soulful` + `arc=peak_hour`, should the warning trigger for ALL poor sessions with that combination, or only the most recent one? Proposal: warn if any of the last 3 poor sessions match the combination. This avoids warning fatigue while still surfacing relevant history.

**Session similarity threshold**
Jaccard similarity for "similar enough to warn" — proposed 0.7 (>70% track overlap). May need tuning once real sessions are generated.

**Spotify matching confidence threshold for imports**
What's the right threshold for auto-accepting a title+artist Spotify match? ISRC is deterministic (always accept). For title+artist search: proposal is title similarity >0.9 AND artist similarity >0.85 AND duration within 5s. Anything below → flag for review, use local tags only. Better to have no Spotify match than a wrong one (wrong match overwrites metadata with incorrect data).

**Duplicate handling on import**
If importing a file whose `spotify_id` already exists in DB but with a different `local_path` and the existing file is still present: options are (a) update path to new file, (b) keep existing, (c) create secondary entry. Proposal: keep existing, warn user. They may have two copies intentionally.

**Non-MP3 formats**
Existing tagger only handles MP3. Import should scan and DB-record FLAC/M4A/WAV files, but TBPM/TKEY writing requires per-format tagger extensions. Proposal: import all formats into DB, but `musicdl analyze` only writes ID3 tags to MP3 files for now. Flag non-MP3 files in output so user knows tag writing was skipped.

**Trust existing genre tags**
If an imported file has TCON="Deep House", should `musicdl classify` (a) trust it as-is, (b) ignore it and re-classify from Last.fm, or (c) run TCON through the normalizer and use it as a cache hit if it maps? Proposal: option (c) — run through normalizer; known taxonomy entry = cache hit, skip resolver; unmappable = run full resolver. Add `--ignore-existing-tags` flag to force re-classification regardless.

---

## What is NOT in scope

- Automatic pitch correction or BPM-matching in the audio output
- Streaming or playback — this generates metadata and playlists only
- Serato cue points or loops (those require Serato-proprietary ID3 frames — possible future addition)
- Rekordbox integration (different export format, possible future addition)
- Crowd-sourced session quality data
- `musicdl organize` (move imported files into library structure) — deferred; Serato path implications need to be understood first
