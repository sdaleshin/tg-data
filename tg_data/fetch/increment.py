"""Инкрементальный pull: только новые сообщения по Source с backfill_done=True."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from tg_data.db.models import Message, Source, SyncState
from tg_data.fetch.backfill import _process_raw
from tg_data.fetch.reader import TelegramReaderPort

logger = logging.getLogger(__name__)


async def increment_source(
    source: Source,
    reader: TelegramReaderPort,
    session: Session,
) -> int:
    """Выкачать новые сообщения Source.

    Возвращает количество сохранённых Message.
    """
    state = session.get(SyncState, source.id)
    if state is None or not state.backfill_done:
        logger.debug("Source %s: backfill не завершён, пропускаем", source.id)
        return 0

    min_id = state.newest_processed_id or 0
    saved = 0

    async for raw in reader.iter_messages(source.tg_id, min_id=min_id):
        if state.newest_processed_id is None or raw.tg_msg_id > (state.newest_processed_id or 0):
            state.newest_processed_id = raw.tg_msg_id

        if _process_raw(raw, source, state, session):
            saved += 1

    state.last_sync_at = datetime.now(timezone.utc)
    session.commit()
    logger.info("Source %s: инкремент, сохранено %d", source.id, saved)
    return saved
