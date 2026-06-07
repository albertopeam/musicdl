from __future__ import annotations

import dataclasses
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import structlog
from rich.console import Console

from musicdl.config import Settings
from musicdl.database import Database, TrackStatus
from musicdl.downloader.detector import find_best_match
from musicdl.downloader.sldl import SldlDownloader
from musicdl.errors import DownloadError, NotFoundError, SpotifyError
from musicdl.genre.resolver import GenreResolver
from musicdl.organizer.filesystem import build_target_path, move_to_library
from musicdl.spotify.client import SpotifyClient
from musicdl.spotify.models import TrackMetadata
from musicdl.tagger.id3 import tag_file

logger = structlog.get_logger()
console = Console()


def run_dry(
    input_file: Path,
    db: Database,
    spotify: SpotifyClient,
) -> None:
    """Expand URLs and print what would be downloaded — no API calls, no downloads."""
    raw_urls = _read_urls(input_file)
    tracks = _expand_urls(raw_urls, spotify)

    if not tracks:
        console.print("[yellow]No tracks found in input file.[/yellow]")
        return

    console.print(f"Found [bold]{len(tracks)}[/bold] track(s):\n")
    seen: set[str] = set()
    n = 0
    for track in tracks:
        if track.track_id in seen:
            continue
        seen.add(track.track_id)
        n += 1
        already = db.get_track(track.track_id)
        status_tag = (
            "[dim](already downloaded)[/dim]"
            if already and already.status == TrackStatus.DOWNLOADED
            else ""
        )
        console.print(
            f"  [cyan]{n:02d}[/cyan]  {track.primary_artist.name} — [bold]{track.title}[/bold]"
            f"  [dim]{track.album_name}, {track.release_year}[/dim]  {status_tag}"
        )

    console.print("\n[dim]Run without --dry-run to download.[/dim]")


def run(
    input_file: Path,
    settings: Settings,
    db: Database,
    spotify: SpotifyClient,
    resolver: GenreResolver,
    downloader: SldlDownloader,
) -> dict[str, int]:
    session_id = db.create_session(str(input_file))
    counts = {"downloaded": 0, "skipped": 0, "failed": 0, "not_found": 0}

    raw_urls = _read_urls(input_file)
    tracks = _expand_urls(raw_urls, spotify)

    if not tracks:
        console.print("[yellow]No tracks found in input file.[/yellow]")
        db.finish_session(session_id, 0, 0, 0, 0)
        return counts

    console.print(f"[bold]Found {len(tracks)} track(s). Prefetching genres...[/bold]")
    _prefetch_genres(tracks, resolver, settings)

    console.print("[bold]Starting downloads...[/bold]\n")

    seen: set[str] = set()
    for track in tracks:
        if track.track_id in seen:
            continue
        seen.add(track.track_id)
        _process_track(track, settings, db, downloader, resolver, counts)

    db.finish_session(
        session_id,
        total_tracks=len(seen),
        downloaded=counts["downloaded"],
        skipped=counts["skipped"],
        failed=counts["failed"],
    )

    console.print(
        f"\n[bold]Session complete.[/bold] "
        f"[green]Downloaded: {counts['downloaded']}[/green]  "
        f"[yellow]Skipped: {counts['skipped']}[/yellow]  "
        f"[yellow]Not found: {counts['not_found']}[/yellow]  "
        f"[red]Failed: {counts['failed']}[/red]"
    )
    return counts


def _process_track(
    track: TrackMetadata,
    settings: Settings,
    db: Database,
    downloader: SldlDownloader,
    resolver: GenreResolver,
    counts: dict[str, int],
) -> None:
    # Deduplication check
    if not db.should_download(track.track_id, settings.max_retries, settings.not_found_retry_days):
        console.print(f"  [dim]SKIP[/dim]  {track.primary_artist.name} — {track.title}")
        db.mark_skipped(track.track_id)
        counts["skipped"] += 1
        return

    # Ensure track is in DB before downloading
    db.upsert_track(
        track_id=track.track_id,
        spotify_url=track.spotify_url,
        title=track.title,
        primary_artist=track.primary_artist.name,
        all_artists=[a.name for a in track.artists],
        album_name=track.album_name,
        album_spotify_id=track.album_spotify_id,
        isrc=track.isrc,
        release_year=track.release_year,
        duration_ms=track.duration_ms,
        track_number=track.track_number,
        disc_number=track.disc_number,
        status=TrackStatus.PENDING,
        source="soulseek",
        spotify_id=track.track_id,
    )

    # Genre resolution (uses cache — cheap if artist was prefetched)
    resolved = resolver.resolve(
        track.primary_artist.name,
        spotify_genres=[],
    )
    track = dataclasses.replace(
        track,
        primary_genre=resolved.primary,
        subgenre=resolved.subgenre,
    )
    db.set_genre(track.track_id, resolved.primary, resolved.subgenre, resolved.source)

    db.mark_downloading(track.track_id)

    # Download
    try:
        result = downloader.download(track)
    except NotFoundError:
        console.print(f"  [yellow]NOT FOUND[/yellow]  {track.primary_artist.name} — {track.title}  (will retry in {settings.not_found_retry_days}d)")
        db.mark_not_found(track.track_id)
        counts["not_found"] += 1
        return
    except DownloadError as exc:
        console.print(f"  [red]FAIL[/red]  {track.primary_artist.name} — {track.title}  ({exc})")
        db.mark_failed(track.track_id, str(exc))
        counts["failed"] += 1
        return
    except Exception as exc:
        console.print(f"  [red]FAIL[/red]  {track.primary_artist.name} — {track.title}  (unexpected: {exc})")
        logger.warning("unexpected_download_error", title=track.title, error=str(exc), exc_info=True)
        db.mark_failed(track.track_id, f"unexpected: {exc}")
        counts["failed"] += 1
        return

    matched = find_best_match(result.downloaded_files, track)
    if matched is None:
        msg = "File downloaded but could not be matched to track"
        console.print(f"  [red]FAIL[/red]  {track.primary_artist.name} — {track.title}  ({msg})")
        db.mark_failed(track.track_id, msg)
        counts["failed"] += 1
        return

    # Tag
    try:
        tag_file(matched, track)
    except Exception as exc:
        console.print(f"  [yellow]WARN[/yellow]  Tagging failed for '{track.title}': {exc}")
        logger.warning("tagging_failed", title=track.title, error=str(exc), exc_info=True)

    # Organise
    try:
        target = build_target_path(settings.output_base, track)
        final_path = move_to_library(matched, target)
    except Exception as exc:
        console.print(f"  [red]FAIL[/red]  {track.primary_artist.name} — {track.title}  (file move failed: {exc})")
        logger.warning("file_move_failed", title=track.title, error=str(exc), exc_info=True)
        db.mark_failed(track.track_id, f"file move failed: {exc}")
        counts["failed"] += 1
        return

    db.mark_downloaded(track.track_id, final_path, final_path.stat().st_size)
    rel = _relative_path(final_path)
    console.print(f"  [green]OK[/green]    {track.primary_artist.name} — {track.title}  → {rel}")
    counts["downloaded"] += 1


def _expand_urls(raw_urls: list[str], spotify: SpotifyClient) -> list[TrackMetadata]:
    tracks: list[TrackMetadata] = []
    for url in raw_urls:
        try:
            expanded = spotify.expand_url(url)
            tracks.extend(expanded)
        except SpotifyError as exc:
            console.print(f"  [red]ERROR[/red]  Could not expand URL {url!r}: {exc}")
            logger.warning("url_expansion_failed", url=url, error=str(exc), exc_info=True)
    return tracks


def _prefetch_genres(
    tracks: list[TrackMetadata],
    resolver: GenreResolver,
    settings: Settings,
) -> None:
    unique_artists = {t.primary_artist.name for t in tracks}

    def _resolve_one(artist: str) -> None:
        try:
            resolver.resolve(artist)
        except Exception as exc:
            console.print(f"  [yellow]WARN[/yellow]  No genre found for '{artist}' — using 'unknown'")
            logger.warning("genre_prefetch_failed", artist=artist, error=str(exc), exc_info=True)

    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = {pool.submit(_resolve_one, a): a for a in unique_artists}
        for future in as_completed(futures):
            # Exceptions are already handled inside _resolve_one
            future.result()


def _read_urls(input_file: Path) -> list[str]:
    lines = input_file.read_text(encoding="utf-8").splitlines()
    return [line.strip() for line in lines if line.strip() and not line.startswith("#")]


def _relative_path(path: Path) -> str:
    try:
        return str(path.relative_to(Path.cwd()))
    except ValueError:
        return str(path)
