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
I would like to define strings in a reusable way instead of repeating it. ie: "downloaded" and others.
I have seen that we are creating directoris for artists, not clear for me this strategy, I would prefer to have plain genres/subgenres dir with the songs there, so I can use in serato.