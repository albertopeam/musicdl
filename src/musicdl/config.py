from __future__ import annotations

from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from musicdl.errors import ConfigError


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="MUSICDL_",
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # Last.fm (required for genre resolution)
    lastfm_api_key: str = ""
    lastfm_api_secret: str = ""

    # MusicBrainz (required by their ToS)
    mb_user_agent: str = "musicdl/0.1 user@example.com"

    # sldl binary
    sldl_binary_path: str = "sldl"
    sldl_timeout_seconds: int = 120
    sldl_max_tries: int = 5

    # Soulseek credentials (required for downloads)
    slsk_username: str = ""
    slsk_password: str = ""

    # Download paths
    output_base: Path = Path("./music")
    staging_dir: Path = Path("./staging")
    max_retries: int = 3

    # Genre resolution
    min_lastfm_tag_weight: int = 30
    cache_ttl_days: int = 30

    # How many days before retrying a track that wasn't found on Soulseek
    not_found_retry_days: int = 3

    # Extended mix preference
    # When True, tries "Artist - Title extended" first, falls back to plain title
    prefer_extended: bool = True
    # Minimum track length in seconds for the extended pass (default: 270 = 4.5 min)
    min_extended_length_seconds: int = 270

    # Database
    db_path: Path = Path("./musicdl.db")

    # Logging
    log_level: str = "INFO"
    log_file: str = ""

    @field_validator("log_level")
    @classmethod
    def validate_log_level(cls, v: str) -> str:
        valid = {"DEBUG", "INFO", "WARNING", "ERROR"}
        upper = v.upper()
        if upper not in valid:
            raise ValueError(f"log_level must be one of {valid}, got {v!r}")
        return upper

    def validate_required(self) -> None:
        missing: list[str] = []
        if not self.lastfm_api_key:
            missing.append("MUSICDL_LASTFM_API_KEY")
        if not self.lastfm_api_secret:
            missing.append("MUSICDL_LASTFM_API_SECRET")
        if not self.slsk_username:
            missing.append("MUSICDL_SLSK_USERNAME")
        if not self.slsk_password:
            missing.append("MUSICDL_SLSK_PASSWORD")
        if missing:
            raise ConfigError(
                f"Missing required environment variables: {', '.join(missing)}\n"
                "Copy .env.example to .env and fill in your API credentials."
            )


def load_settings(env_file: Path | None = None) -> Settings:
    kwargs: dict[str, object] = {}
    if env_file is not None:
        kwargs["_env_file"] = str(env_file)
    try:
        return Settings(**kwargs)  # type: ignore[arg-type]
    except Exception as exc:
        raise ConfigError(f"Failed to load configuration: {exc}") from exc
