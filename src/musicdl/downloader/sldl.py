from __future__ import annotations

import subprocess
import time
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

import structlog

from musicdl.errors import ConfigError, DownloadError, DownloadTimeoutError, NotFoundError
from musicdl.spotify.models import TrackMetadata

logger = structlog.get_logger()

_NOT_FOUND_MARKERS = (
    "no results",
    "not found",
    "failed to find",
    "all downloads failed",
    "failed: 1",
)


class DownloadResult(Enum):
    SUCCESS = "success"
    NOT_FOUND = "not_found"
    TIMEOUT = "timeout"
    ERROR = "error"


@dataclass(frozen=True)
class SldlResult:
    outcome: DownloadResult
    downloaded_files: list[Path]
    stdout: str
    stderr: str
    return_code: int
    duration_seconds: float


class SldlDownloader:
    def __init__(
        self,
        binary: str,
        staging_dir: Path,
        quality: str = "320",
        timeout: int = 120,
        max_tries: int = 5,
        username: str = "",
        password: str = "",
        prefer_extended: bool = True,
        min_extended_length_seconds: int = 270,
    ) -> None:
        self._binary = binary
        self._staging_dir = staging_dir
        self._quality = quality
        self._timeout = timeout
        self._max_tries = max_tries
        self._username = username
        self._password = password
        self._prefer_extended = prefer_extended
        self._min_extended_length = min_extended_length_seconds

    def preflight(self) -> str:
        """Verify sldl binary is available. Returns version string. Raises ConfigError."""
        try:
            result = subprocess.run(
                [self._binary, "--version"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=10,
            )
            version = result.stdout.strip() or result.stderr.strip()
            logger.debug("sldl_preflight_ok", version=version)
            return version
        except FileNotFoundError as exc:
            raise ConfigError(
                f"sldl binary not found at '{self._binary}'.\n"
                "Install with: dotnet tool install -g slsk-batchdl\n"
                "Or set sldl_binary_path in config.toml."
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise ConfigError("sldl binary did not respond within 10 seconds.") from exc

    def download(self, track: TrackMetadata) -> SldlResult:
        self._staging_dir.mkdir(parents=True, exist_ok=True)

        # Pass 1: extended mix preferred
        if self._prefer_extended:
            result = self._run(track, extended=True)
            if result.outcome == DownloadResult.SUCCESS:
                logger.info("extended_found", title=track.title)
                return result
            # Extended not found — fall through to plain query
            logger.debug("extended_not_found_falling_back", title=track.title)

        # Pass 2 (or only pass): plain query, any length
        result = self._run(track, extended=False)

        if result.outcome == DownloadResult.NOT_FOUND:
            raise NotFoundError(f"No Soulseek results for '{track.search_query}'")
        if result.outcome == DownloadResult.ERROR:
            raise DownloadError(
                f"sldl error for '{track.search_query}': {result.stderr[:400] or result.stdout[:400]}"
            )
        return result

    def _run(self, track: TrackMetadata, extended: bool) -> SldlResult:
        before = set(self._staging_dir.glob("**/*.mp3"))
        cmd = self._build_command(track, extended=extended)

        logger.info(
            "sldl_download_start",
            title=track.title,
            artist=track.primary_artist.name,
            query=track.search_query,
            extended=extended,
        )

        t0 = time.monotonic()
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=self._timeout,
            )
        except subprocess.TimeoutExpired as exc:
            logger.warning("sldl_timeout", title=track.title, timeout=self._timeout)
            raise DownloadTimeoutError(
                f"sldl timed out after {self._timeout}s for '{track.search_query}'"
            ) from exc

        duration = time.monotonic() - t0
        after = set(self._staging_dir.glob("**/*.mp3"))
        new_files = sorted(after - before)

        result = SldlResult(
            outcome=self._classify_outcome(proc, new_files),
            downloaded_files=new_files,
            stdout=proc.stdout,
            stderr=proc.stderr,
            return_code=proc.returncode,
            duration_seconds=duration,
        )

        logger.info(
            "sldl_download_done",
            title=track.title,
            outcome=result.outcome.value,
            duration=round(duration, 1),
            new_files=len(new_files),
            extended=extended,
        )
        return result

    def _build_command(self, track: TrackMetadata, extended: bool = False) -> list[str]:
        if extended:
            query = f"{track.search_query} extended"
        else:
            query = track.search_query

        cmd = [
            self._binary,
            query,
            "--path", str(self._staging_dir),
            "--format", "mp3",
            "--min-bitrate", self._quality,
            "--no-progress",
            "--length-tol", "3",
            "--max-stale-time", "30000",
            "--username", self._username,
            "--password", self._password,
        ]

        if extended:
            cmd += ["--min-length", str(self._min_extended_length)]

        return cmd

    @staticmethod
    def _classify_outcome(
        proc: subprocess.CompletedProcess[str],
        new_files: list[Path],
    ) -> DownloadResult:
        if new_files:
            return DownloadResult.SUCCESS
        stdout_lower = (proc.stdout or "").lower()
        if any(marker in stdout_lower for marker in _NOT_FOUND_MARKERS):
            return DownloadResult.NOT_FOUND
        # No new files means nothing was downloaded regardless of exit code
        return DownloadResult.NOT_FOUND
