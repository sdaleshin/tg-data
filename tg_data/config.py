from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    database_url: str = "postgresql://tgdata:tgdata@localhost:5433/tgdata"
    session_path: Path = Path.home() / ".tg-data" / "session"
    archive_since: str = "2024-01-01"

    telegram_api_id: int = 0
    telegram_api_hash: str = ""

    request_pause: float = 1.0
    pull_interval_seconds: int = 21600
    backfill_resume_interval_seconds: int = 3600

    @field_validator("session_path", mode="before")
    @classmethod
    def _expand_session_path(cls, value: str | Path) -> Path:
        return Path(value).expanduser()


settings = Settings()
