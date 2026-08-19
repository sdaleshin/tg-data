from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    database_url: str = "postgresql://tgdata:tgdata@localhost:5433/tgdata"
    session_path: Path = Path.home() / ".tg-data" / "session"
    archive_since: str = "2024-01-01"

    telegram_api_id: int = 0
    telegram_api_hash: str = ""


settings = Settings()
