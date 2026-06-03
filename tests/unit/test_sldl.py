from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from musicdl.downloader.sldl import DownloadResult, SldlDownloader
from musicdl.errors import DownloadTimeoutError, NotFoundError
from musicdl.spotify.models import TrackMetadata


def _make_downloader(staging_dir: Path) -> SldlDownloader:
    return SldlDownloader(binary="sldl", staging_dir=staging_dir, quality="320", timeout=30, max_tries=5)


def _completed(stdout: str = "", stderr: str = "", returncode: int = 0) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr=stderr)


class TestClassifyOutcome:
    def test_success_when_new_files_present(self, tmp_path: Path) -> None:
        f = tmp_path / "track.mp3"
        f.write_bytes(b"audio")
        result = SldlDownloader._classify_outcome(_completed(), [f])
        assert result == DownloadResult.SUCCESS

    def test_not_found_when_no_results_in_stdout(self) -> None:
        result = SldlDownloader._classify_outcome(
            _completed(stdout="No results found for query", returncode=1), []
        )
        assert result == DownloadResult.NOT_FOUND

    def test_not_found_when_returncode_zero_no_files(self) -> None:
        # No downloaded files means not found regardless of exit code
        result = SldlDownloader._classify_outcome(_completed(returncode=0), [])
        assert result == DownloadResult.NOT_FOUND

    def test_not_found_when_nonzero_no_files(self) -> None:
        # No downloaded files means not found even on connection errors
        result = SldlDownloader._classify_outcome(_completed(returncode=1, stderr="connection refused"), [])
        assert result == DownloadResult.NOT_FOUND


class TestDownload:
    def test_raises_not_found_error(
        self, mocker: MagicMock, staging_dir: Path, sample_track: TrackMetadata
    ) -> None:
        mocker.patch(
            "subprocess.run",
            return_value=_completed(stdout="No results found for query", returncode=1),
        )
        dl = _make_downloader(staging_dir)
        with pytest.raises(NotFoundError):
            dl.download(sample_track)

    def test_raises_timeout_error(
        self, mocker: MagicMock, staging_dir: Path, sample_track: TrackMetadata
    ) -> None:
        mocker.patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd=["sldl"], timeout=30))
        dl = _make_downloader(staging_dir)
        with pytest.raises(DownloadTimeoutError):
            dl.download(sample_track)

    def test_returns_result_on_success(
        self, mocker: MagicMock, staging_dir: Path, sample_track: TrackMetadata
    ) -> None:
        fake_file = staging_dir / "Bicep - Glue.mp3"

        def _write_file(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
            # Simulate sldl writing a file during the subprocess call
            fake_file.write_bytes(b"audio")
            return _completed()

        mocker.patch("subprocess.run", side_effect=_write_file)
        dl = _make_downloader(staging_dir)
        result = dl.download(sample_track)
        assert result.outcome == DownloadResult.SUCCESS
        assert len(result.downloaded_files) == 1


class TestSpotifyUrlParser:
    @pytest.mark.parametrize("url,expected_type,expected_id", [
        ("https://open.spotify.com/track/4uLU6hMCjMI75M1A2tKUQC", "track", "4uLU6hMCjMI75M1A2tKUQC"),
        ("https://open.spotify.com/album/5ht7ItJgpBH7W6vJ3Tv4lE", "album",  "5ht7ItJgpBH7W6vJ3Tv4lE"),
        ("https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M", "playlist", "37i9dQZF1DXcBWIGoYBM5M"),
    ])
    def test_parse_valid_urls(self, url: str, expected_type: str, expected_id: str) -> None:
        from musicdl.spotify.client import parse_spotify_url
        url_type, spotify_id = parse_spotify_url(url)
        assert url_type == expected_type
        assert spotify_id == expected_id

    def test_invalid_url_raises(self) -> None:
        from musicdl.errors import SpotifyError
        from musicdl.spotify.client import parse_spotify_url
        with pytest.raises(SpotifyError):
            parse_spotify_url("https://youtube.com/watch?v=abc")
