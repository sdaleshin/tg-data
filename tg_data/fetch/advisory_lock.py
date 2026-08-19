"""Postgres advisory lock: гарантирует один активный процесс с Telegram."""

from contextlib import contextmanager

from sqlalchemy import text
from sqlalchemy.orm import Session

LOCK_ID = 7777_7777  # произвольный стабильный id


@contextmanager
def advisory_lock(session: Session):
    """Блокирует выполнение: только один процесс может работать с Telegram."""
    result = session.execute(
        text("SELECT pg_try_advisory_lock(:lock_id)"), {"lock_id": LOCK_ID}
    ).scalar()

    if not result:
        raise RuntimeError(
            "Другой процесс уже работает с Telegram. Дождитесь его завершения."
        )

    try:
        yield
    finally:
        session.execute(
            text("SELECT pg_advisory_unlock(:lock_id)"), {"lock_id": LOCK_ID}
        )
