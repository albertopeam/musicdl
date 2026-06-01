from __future__ import annotations


class MusicdlError(Exception):
    """Base exception for all musicdl errors."""


class ConfigError(MusicdlError):
    """Raised when configuration is missing or invalid."""


class SpotifyError(MusicdlError):
    """Raised when a Spotify URL cannot be parsed or metadata cannot be fetched."""


class GenreError(MusicdlError):
    """Raised when all genre resolution sources fail."""


class DownloadError(MusicdlError):
    """Raised when sldl fails to download a track."""


class NotFoundError(DownloadError):
    """Raised when sldl finds no matching results on Soulseek."""


class DownloadTimeoutError(DownloadError):
    """Raised when sldl exceeds its configured timeout."""


class DatabaseError(MusicdlError):
    """Raised when a SQLite operation fails fatally."""
