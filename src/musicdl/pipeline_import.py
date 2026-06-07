from __future__ import annotations

from pathlib import Path

import structlog
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn

from musicdl.database import Database
from musicdl.errors import GenreError
from musicdl.genre.resolver import GenreResolver
from musicdl.importer.scanner import ScannedFile, make_local_track_id, read_file_tags, scan_directory
from musicdl.tagger.id3 import write_genre_tag

logger = structlog.get_logger()
console = Console()


def run_import(
    path: Path,
    db: Database,
    spotify: object | None,
    dry_run: bool = False,
) -> None:
    """Scan path and import audio files into the library database."""
    files = scan_directory(path)
    if not files:
        console.print(f"[yellow]No supported audio files found in {path}[/yellow]")
        return

    console.print(f"Found [bold]{len(files)}[/bold] audio file(s) to process.\n")

    counts: dict[str, int] = {"imported": 0, "skipped": 0, "matched": 0, "unmatched": 0, "failed": 0}

    for file_path in files:
        scanned = read_file_tags(file_path)
        if scanned is None:
            console.print(f"  [red]SKIP[/red]      {file_path.name}  (could not read tags)")
            counts["failed"] += 1
            continue

        _process_import(scanned, db, spotify, dry_run, counts)

    _print_import_summary(counts, dry_run)


def _process_import(
    scanned: ScannedFile,
    db: Database,
    spotify: object | None,
    dry_run: bool,
    counts: dict[str, int],
) -> None:
    title = scanned.title or scanned.path.stem
    artist = scanned.artist or "Unknown Artist"
    label = f"{artist} — {title}"

    # Try Spotify ISRC lookup to get full metadata and a stable track_id
    spotify_id: str | None = None
    track_id: str | None = None

    if spotify is not None and scanned.isrc:
        from musicdl.spotify.client import SpotifyClient
        if isinstance(spotify, SpotifyClient):
            meta = spotify.lookup_by_isrc(scanned.isrc)
            if meta:
                track_id = meta.track_id
                spotify_id = meta.track_id
                counts["matched"] += 1
                logger.debug("isrc_matched", isrc=scanned.isrc, track_id=track_id)

    if track_id is None:
        track_id = (
            f"isrc:{scanned.isrc}" if scanned.isrc
            else make_local_track_id(scanned.path, scanned.title, scanned.artist)
        )
        counts["unmatched"] += 1

    # Check if already in library — three levels: track_id, spotify_id, file path
    existing = db.get_track(track_id)
    if existing is None and spotify_id:
        existing = db.get_track_by_spotify_id(spotify_id)
    if existing is None:
        existing = db.get_track_by_local_path(scanned.path)

    if existing is not None and existing.local_path is not None and existing.local_path.exists():
        console.print(f"  [dim]SKIP[/dim]      {label}  → already in library")
        counts["skipped"] += 1
        return

    if dry_run:
        tag = "[green]matched Spotify[/green]" if spotify_id else "[dim]no Spotify match[/dim]"
        console.print(f"  [dim]WOULD IMPORT[/dim]  {label}  ({tag})")
        counts["imported"] += 1
        return

    # Determine genre from existing tag if mappable
    from musicdl.genre.normalizer import classify
    genre_result = classify([scanned.genre]) if scanned.genre else None
    primary_genre = genre_result[0] if genre_result else None
    subgenre = genre_result[1] if genre_result else None

    db.upsert_imported_track(
        track_id=track_id,
        title=title,
        primary_artist=artist,
        all_artists=[artist],
        album_name=scanned.album or "",
        local_path=scanned.path,
        duration_ms=scanned.duration_ms,
        release_year=scanned.year,
        track_number=scanned.track_number,
        isrc=scanned.isrc,
        primary_genre=primary_genre,
        subgenre=subgenre,
        spotify_id=spotify_id,
    )

    tag = f"[green]matched Spotify: {spotify_id}[/green]" if spotify_id else f"[dim]id: {track_id}[/dim]"
    console.print(f"  [green]IMPORTED[/green]   {label}  → {tag}")
    counts["imported"] += 1
    logger.info("track_imported", track_id=track_id, path=str(scanned.path), spotify_id=spotify_id)


def _print_import_summary(counts: dict[str, int], dry_run: bool) -> None:
    verb = "Would import" if dry_run else "Imported"
    console.print(
        f"\n[bold]{verb}: {counts['imported']}[/bold]  "
        f"[dim]Skipped: {counts['skipped']}[/dim]  "
        f"[green]Spotify matched: {counts['matched']}[/green]  "
        f"[yellow]Unmatched: {counts['unmatched']}[/yellow]"
        + (f"  [red]Failed: {counts['failed']}[/red]" if counts["failed"] else "")
    )
    unclassified = counts["imported"] - counts["matched"]
    if not dry_run and unclassified > 0:
        console.print(
            f"\n[dim]Run [bold]musicdl classify[/bold] to resolve genres "
            f"for {unclassified} unmatched track(s).[/dim]"
        )


def run_classify(
    db: Database,
    resolver: GenreResolver,
    mode: str = "unclassified",
    dry_run: bool = False,
    move: bool = False,
    output_base: Path | None = None,
) -> None:
    """Resolve genres for library tracks that lack confident genre data."""
    tracks = db.list_unclassified_tracks(mode)

    if not tracks:
        console.print("[green]All tracks already classified.[/green]")
        return

    console.print(f"Found [bold]{len(tracks)}[/bold] track(s) to classify.\n")

    if mode == "reclassify":
        # Clear stale cache entries so resolver fetches fresh data
        artists = {t.primary_artist for t in tracks}
        for artist in artists:
            db.clear_genre_cache_for_artist(artist)

    counts = {"classified": 0, "unknown": 0, "failed": 0}

    with Progress(SpinnerColumn(), TextColumn("{task.description}"), transient=True) as progress:
        task = progress.add_task("Classifying...", total=len(tracks))

        for track in tracks:
            progress.update(task, description=f"[dim]{track.primary_artist}[/dim]")

            if dry_run:
                move_hint = "  [dim](would move)[/dim]" if move else ""
                console.print(f"  [dim]WOULD CLASSIFY[/dim]  {track.primary_artist} — {track.title}{move_hint}")
                counts["classified"] += 1
                progress.advance(task)
                continue

            try:
                resolved = resolver.resolve(track.primary_artist, spotify_genres=[])
            except GenreError:
                console.print(f"  [yellow]WARN[/yellow]  No genre found for '{track.primary_artist}'")
                counts["unknown"] += 1
                progress.advance(task)
                continue
            except Exception as exc:
                console.print(f"  [red]FAIL[/red]  {track.primary_artist} — {track.title}: {exc}")
                logger.warning("classify_failed", artist=track.primary_artist, error=str(exc), exc_info=True)
                counts["failed"] += 1
                progress.advance(task)
                continue

            db.set_genre(track.track_id, resolved.primary, resolved.subgenre, resolved.source)

            if track.local_path and track.local_path.exists() and track.local_path.suffix.lower() == ".mp3":
                try:
                    write_genre_tag(track.local_path, resolved.primary)
                except OSError as exc:
                    logger.warning("genre_tag_write_failed", path=str(track.local_path), error=str(exc))

            console.print(
                f"  [green]OK[/green]    {track.primary_artist} — {track.title}"
                f"  → [cyan]{resolved.primary}/{resolved.subgenre}[/cyan]"
                f"  [dim]({resolved.source})[/dim]"
            )

            if move and output_base and track.local_path and track.local_path.exists():
                from musicdl.organizer.filesystem import build_target_path, move_to_library
                try:
                    target = build_target_path(
                        output_base, resolved.primary, resolved.subgenre,
                        track.track_number, track.title,
                    )
                    new_path = move_to_library(track.local_path, target)
                    db.set_local_path(track.track_id, new_path)
                    console.print(f"      → [dim]{new_path.relative_to(output_base)}[/dim]")
                except Exception as exc:
                    logger.warning("classify_move_failed", track_id=track.track_id, error=str(exc), exc_info=True)

            counts["classified"] += 1
            progress.advance(task)

    verb = "Would classify" if dry_run else "Classified"
    console.print(
        f"\n[bold]{verb}: {counts['classified']}[/bold]"
        + (f"  [yellow]Unknown: {counts['unknown']}[/yellow]" if counts["unknown"] else "")
        + (f"  [red]Failed: {counts['failed']}[/red]" if counts["failed"] else "")
    )
