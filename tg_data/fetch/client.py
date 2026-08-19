"""Telethon client factory — единственное место, знающее про сессию."""

from telethon import TelegramClient
from telethon.sessions import StringSession

from tg_data.config import settings


def make_client() -> TelegramClient:
    session_path = settings.session_path
    session_path.parent.mkdir(parents=True, exist_ok=True)
    return TelegramClient(
        str(session_path),
        settings.telegram_api_id,
        settings.telegram_api_hash,
    )
