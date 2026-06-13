# Pending improvements

## import / classify

### Multi-artist genre lookup gives wrong results
When a track has multiple artists in the TPE1 tag (e.g. "Kenny Dope, Crystal Waters, Alok, Ella Eyre"),
the full comma-separated string is passed to Last.fm and MusicBrainz as the artist name.
These APIs don't recognise multi-artist strings and return wrong or unrelated results.

Fix: extract only the first artist before calling the genre resolver.
The first comma-delimited segment is almost always the primary artist.
The download pipeline already does this correctly via TrackMetadata.primary_artist (artists[0]);
the import pipeline bypasses TrackMetadata and passes primary_artist directly from the TPE1 tag.

Location: pipeline_import.py — _process_import() — the `artist` variable sent to the resolver.

---

## tests

### Missing: --move path in run_classify
No unit test covers the `--move` branch added to `run_classify()` in `pipeline_import.py`.
A test should use `tmp_path` + real `tmp_db` + mocked resolver and verify:
- the file is physically moved to `output_base/genre/subgenre/NN - title.mp3`
- `db.get_track(track_id).local_path` reflects the new path after the move
- a move failure (e.g. source file missing) is caught and logged, not raised

---

## download pipeline

### Genre quality: several common artists resolve to wrong or unknown genre
Observed in session log:
- **Birdee** → `rock` (cache hit — wrong, should be electronic/nu-disco or house)
- **Cheek** → `hip-hop` (cache hit — wrong for a DJ Gregory house remix)
- **Vaudafunk** → `unknown` (no resolution from Last.fm or MusicBrainz despite being a known nu-disco artist)
- **HP Vince**, **Togetherness**, **Judy Albanese** → `unknown`

These are taxonomy and Last.fm tag quality issues. Short-term fix: add these artists / tag mappings to
`genre/taxonomy.py` GENRE_MAP. Longer term: consider weighting Last.fm tags more carefully or adding
a Beatport fallback for artists with no Last.fm genre signal.

