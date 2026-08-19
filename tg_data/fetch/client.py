"""Telethon client factory — единственное место, знающее про сессию."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.orm import Session
from telethon import TelegramClient

from tg_data.config import settings
from tg_data.db.engine import engine
from tg_data.fetch.advisory_lock import advisory_lock


class NotAuthorized(Exception):
    """Файл сессии есть, но аккаунт в нём не авторизован."""


def make_client() -> TelegramClient:
    session_path = settings.session_path.expanduser()
    session_path.parent.mkdir(parents=True, exist_ok=True)
    return TelegramClient(
        str(session_path),
        settings.telegram_api_id,
        settings.telegram_api_hash,
    )


@asynccontextmanager
async def telegram_session(
    *, interactive: bool = False
) -> AsyncIterator[TelegramClient]:
    """Единственный вход в Telegram: advisory lock, затем подключение.

    Лок берётся до открытия файла сессии: два процесса на одном
    session.session ломают SQLite и поднимают два MTProto-соединения на
    один auth_key.

    interactive=True разрешает Telethon спросить телефон и код — только для
    `tg auth`. Во всех остальных командах неавторизованная сессия должна
    падать с внятной ошибкой, а не ждать ввода в фоновом контейнере.
    """
    with Session(engine) as lock_session:
        with advisory_lock(lock_session):
            client = make_client()
            if interactive:
                await client.start()
            else:
                await client.connect()
            try:
                if not interactive and not await client.is_user_authorized():
                    raise NotAuthorized(
                        "Сессия Telegram не авторизована. Выполните: make auth"
                    )
                yield client
            finally:
                await client.disconnect()
