from __future__ import annotations

import sys
from pathlib import Path

import click
import structlog
from rich.console import Console
from rich.table import Table

from musicdl.errors import ConfigError, MusicdlError

console = Console()


def _configure_logging(level: str) -> None:
    import logging
    structlog.configure(
        processors=[
            structlog.stdlib.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.dev.ConsoleRenderer(colors=False),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, level.upper(), logging.INFO)
        ),
        logger_factory=structlog.PrintLoggerFactory(file=sys.stderr),
    )


@click.group()
@click.version_option(package_name="musicdl")
def cli() -> None:
    """musicdl — Spotify-to-Soulseek 320kbps MP3 downloader."""


@cli.command("download")
@click.argument("input_file", type=click.Path(exists=True, path_type=Path))
@click.option("--config", "-c", type=click.Path(path_type=Path), default=None,
              help="Path to config.toml (default: .env in current directory)")
@click.option("--output", "-o", type=click.Path(path_type=Path), default=None,
              help="Override output base directory")
@click.option("--db", type=click.Path(path_type=Path), default=None,
              help="Override SQLite database path")
@click.option("--staging", type=click.Path(path_type=Path), default=None,
              help="Override staging directory")
@click.option("--dry-run", is_flag=True,
              help="Resolve metadata and genres only — do not download")
@click.option("--retry-failed", is_flag=True,
              help="Reset all failed tracks to pending before running")
@click.option("--retry-not-found", is_flag=True,
              help="Immediately retry all not-found tracks regardless of cooldown")
@click.option("--force", is_flag=True,
              help="Re-download even if track is already marked as downloaded")
@click.option("--verbose", "-v", is_flag=True, help="Enable debug logging")
def download_cmd(
    input_file: Path,
    config: Path | None,
    output: Path | None,
    db: Path | None,
    staging: Path | None,
    dry_run: bool,
    retry_failed: bool,
    retry_not_found: bool,
    force: bool,
    verbose: bool,
) -> None:
    """Download tracks listed in INPUT_FILE (one Spotify URL per line)."""
    from musicdl.config import load_settings
    from musicdl.database import Database
    from musicdl.downloader.sldl import SldlDownloader
    from musicdl.genre.beatport import BeatportScraper
    from musicdl.genre.cache import GenreCache
    from musicdl.genre.lastfm import LastFmClient
    from musicdl.genre.musicbrainz import MusicBrainzClient
    from musicdl.genre.resolver import GenreResolver
    from musicdl.spotify.client import SpotifyClient
    import musicdl.pipeline as pipeline_mod

    try:
        settings = load_settings(env_file=config)
        if not dry_run:
            settings.validate_required()
    except ConfigError as exc:
        raise click.ClickException(str(exc)) from exc

    # Apply CLI overrides
    if output:
        settings = settings.model_copy(update={"output_base": output})
    if db:
        settings = settings.model_copy(update={"db_path": db})
    if staging:
        settings = settings.model_copy(update={"staging_dir": staging})

    _configure_logging("DEBUG" if verbose else settings.log_level)

    database = Database(settings.db_path)
    try:
        database.migrate()
    except MusicdlError as exc:
        raise click.ClickException(str(exc)) from exc

    if retry_failed:
        reset = database.reset_failed_tracks()
        console.print(f"[yellow]Reset {reset} failed track(s) to pending.[/yellow]")

    if retry_not_found:
        reset = database.reset_not_found_tracks()
        console.print(f"[yellow]Reset {reset} not-found track(s) to pending.[/yellow]")

    try:
        spotify = SpotifyClient()

        if dry_run:
            console.print("[bold yellow]DRY RUN — no files will be downloaded.[/bold yellow]\n")
            try:
                pipeline_mod.run_dry(input_file=input_file, db=database, spotify=spotify)
            except MusicdlError as exc:
                raise click.ClickException(str(exc)) from exc
            return

        downloader = SldlDownloader(
            binary=settings.sldl_binary_path,
            staging_dir=settings.staging_dir,
            quality="320",
            timeout=settings.sldl_timeout_seconds,
            max_tries=settings.sldl_max_tries,
            username=settings.slsk_username,
            password=settings.slsk_password,
            prefer_extended=settings.prefer_extended,
            min_extended_length_seconds=settings.min_extended_length_seconds,
        )
        downloader.preflight()

        lastfm = LastFmClient(
            api_key=settings.lastfm_api_key,
            api_secret=settings.lastfm_api_secret,
            min_weight=settings.min_lastfm_tag_weight,
        )
        mb = MusicBrainzClient(user_agent=settings.mb_user_agent)
        beatport = BeatportScraper()
        cache = GenreCache(database, ttl_days=settings.cache_ttl_days)
        resolver = GenreResolver(lastfm=lastfm, musicbrainz=mb, beatport=beatport, cache=cache)

    except MusicdlError as exc:
        raise click.ClickException(str(exc)) from exc

    try:
        pipeline_mod.run(
            input_file=input_file,
            settings=settings,
            db=database,
            spotify=spotify,
            resolver=resolver,
            downloader=downloader,
        )
    except MusicdlError as exc:
        raise click.ClickException(str(exc)) from exc


@cli.command("status")
@click.option("--db", type=click.Path(path_type=Path), default=Path("./musicdl.db"),
              show_default=True, help="SQLite database path")
@click.option("--limit", default=10, show_default=True, help="Number of recent sessions to show")
def status_cmd(db: Path, limit: int) -> None:
    """Show recent download sessions and track status counts."""
    from musicdl.database import Database, TrackStatus

    database = Database(db)
    try:
        database.migrate()
    except MusicdlError as exc:
        raise click.ClickException(str(exc)) from exc

    sessions = database.list_sessions(limit=limit)
    if not sessions:
        console.print("No sessions found.")
        return

    table = Table(title="Recent Sessions", show_lines=True)
    table.add_column("ID", style="dim")
    table.add_column("Input file")
    table.add_column("Started")
    table.add_column("Total", justify="right")
    table.add_column("Downloaded", style="green", justify="right")
    table.add_column("Skipped", style="yellow", justify="right")
    table.add_column("Failed", style="red", justify="right")

    for s in sessions:
        table.add_row(
            str(s.id),
            s.input_file,
            s.started_at[:19],
            str(s.total_tracks or "—"),
            str(s.downloaded),
            str(s.skipped),
            str(s.failed),
        )
    console.print(table)

    # Overall library counts
    for status_val in (TrackStatus.DOWNLOADED, TrackStatus.NOT_FOUND, TrackStatus.FAILED, TrackStatus.PENDING):
        tracks = database.list_tracks_by_status(status_val)
        if tracks:
            console.print(f"  {status_val.replace('_', ' ').capitalize()}: {len(tracks)} track(s)")


@cli.command("retry")
@click.option("--db", type=click.Path(path_type=Path), default=Path("./musicdl.db"),
              show_default=True, help="SQLite database path")
def retry_cmd(db: Path) -> None:
    """Reset all failed tracks to pending so they will be retried on next download run."""
    from musicdl.database import Database

    database = Database(db)
    try:
        database.migrate()
        reset = database.reset_failed_tracks()
    except MusicdlError as exc:
        raise click.ClickException(str(exc)) from exc

    console.print(f"[green]Reset {reset} failed track(s) to pending.[/green]")


@cli.command("import")
@click.argument("path", type=click.Path(exists=True, path_type=Path))
@click.option("--config", "-c", type=click.Path(path_type=Path), default=None)
@click.option("--db", type=click.Path(path_type=Path), default=None)
@click.option("--no-spotify", is_flag=True, help="Skip Spotify ISRC lookup (fully offline)")
@click.option("--classify", "run_classify", is_flag=True, help="Run genre classification after import")
@click.option("--move", is_flag=True, default=False,
              help="Move files into genre/subgenre directories after classifying. Requires --classify.")
@click.option("--dry-run", is_flag=True, help="Show what would be imported without writing to DB")
@click.option("--verbose", "-v", is_flag=True)
def import_cmd(
    path: Path,
    config: Path | None,
    db: Path | None,
    no_spotify: bool,
    run_classify: bool,
    move: bool,
    dry_run: bool,
    verbose: bool,
) -> None:
    """Import local audio files into the library database."""
    from musicdl.config import load_settings
    from musicdl.database import Database
    import musicdl.pipeline_import as imp

    try:
        settings = load_settings(env_file=config)
    except Exception as exc:
        raise click.ClickException(str(exc)) from exc

    if db:
        settings = settings.model_copy(update={"db_path": db})

    _configure_logging("DEBUG" if verbose else settings.log_level)

    database = Database(settings.db_path)
    try:
        database.migrate()
    except MusicdlError as exc:
        raise click.ClickException(str(exc)) from exc

    spotify = None
    if not no_spotify:
        try:
            from musicdl.spotify.client import SpotifyClient
            spotify = SpotifyClient()
        except Exception:
            console.print("[yellow]WARN[/yellow]  Spotify unavailable — proceeding without ISRC lookup")

    if move and not run_classify:
        console.print("[yellow]WARN[/yellow]  --move has no effect without --classify")

    imp.run_import(path=path, db=database, spotify=spotify, dry_run=dry_run)

    if run_classify and not dry_run:
        from musicdl.genre.beatport import BeatportScraper
        from musicdl.genre.cache import GenreCache
        from musicdl.genre.lastfm import LastFmClient
        from musicdl.genre.musicbrainz import MusicBrainzClient
        from musicdl.genre.resolver import GenreResolver
        try:
            lastfm = LastFmClient(
                api_key=settings.lastfm_api_key,
                api_secret=settings.lastfm_api_secret,
                min_weight=settings.min_lastfm_tag_weight,
            )
            mb = MusicBrainzClient(user_agent=settings.mb_user_agent)
            resolver = GenreResolver(
                lastfm=lastfm,
                musicbrainz=mb,
                beatport=BeatportScraper(),
                cache=GenreCache(database, ttl_days=settings.cache_ttl_days),
            )
            console.print("\n[bold]Running genre classification...[/bold]\n")
            imp.run_classify(
                db=database, resolver=resolver, mode="unclassified",
                move=move, output_base=settings.output_base,
            )
        except MusicdlError as exc:
            raise click.ClickException(str(exc)) from exc


@cli.command("classify")
@click.option("--config", "-c", type=click.Path(path_type=Path), default=None)
@click.option("--db", type=click.Path(path_type=Path), default=None)
@click.option("--mode", type=click.Choice(["unclassified", "reclassify", "all"]),
              default="unclassified", show_default=True,
              help="unclassified: missing/unknown genre only  "
                   "reclassify: also retry fallback results  "
                   "all: re-run every downloaded track")
@click.option("--move", is_flag=True, default=False,
              help="Move files into genre/subgenre directories after classifying.")
@click.option("--dry-run", is_flag=True, help="Show what would be classified without writing")
@click.option("--verbose", "-v", is_flag=True)
def classify_cmd(
    config: Path | None,
    db: Path | None,
    mode: str,
    move: bool,
    dry_run: bool,
    verbose: bool,
) -> None:
    """Resolve genres for library tracks that lack confident genre data."""
    from musicdl.config import load_settings
    from musicdl.database import Database
    from musicdl.genre.beatport import BeatportScraper
    from musicdl.genre.cache import GenreCache
    from musicdl.genre.lastfm import LastFmClient
    from musicdl.genre.musicbrainz import MusicBrainzClient
    from musicdl.genre.resolver import GenreResolver
    import musicdl.pipeline_import as imp

    try:
        settings = load_settings(env_file=config)
        settings.validate_required()
    except Exception as exc:
        raise click.ClickException(str(exc)) from exc

    if db:
        settings = settings.model_copy(update={"db_path": db})

    _configure_logging("DEBUG" if verbose else settings.log_level)

    database = Database(settings.db_path)
    try:
        database.migrate()
    except MusicdlError as exc:
        raise click.ClickException(str(exc)) from exc

    try:
        lastfm = LastFmClient(
            api_key=settings.lastfm_api_key,
            api_secret=settings.lastfm_api_secret,
            min_weight=settings.min_lastfm_tag_weight,
        )
        mb = MusicBrainzClient(user_agent=settings.mb_user_agent)
        resolver = GenreResolver(
            lastfm=lastfm,
            musicbrainz=mb,
            beatport=BeatportScraper(),
            cache=GenreCache(database, ttl_days=settings.cache_ttl_days),
        )
    except MusicdlError as exc:
        raise click.ClickException(str(exc)) from exc

    imp.run_classify(
        db=database, resolver=resolver, mode=mode, dry_run=dry_run,
        move=move, output_base=settings.output_base,
    )


@cli.command("init-config")
@click.option("--output", "-o", type=click.Path(path_type=Path),
              default=Path("./config.toml"), show_default=True)
def init_config_cmd(output: Path) -> None:
    """Write a commented config.toml template to OUTPUT."""
    import shutil
    template = Path(__file__).parent.parent.parent / "config.example.toml"
    if not template.exists():
        raise click.ClickException(f"Template not found at {template}")
    shutil.copy(template, output)
    console.print(f"[green]Config template written to {output}[/green]")
    console.print("Edit it and copy [bold].env.example[/bold] → [bold].env[/bold] with your API keys.")
