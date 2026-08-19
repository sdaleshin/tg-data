"""Backfill: выкачка истории одного Source от newest до границы архива."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from tg_data.config import settings
from tg_data.db.models import Message, Source, SyncState, ThreadRoot
from tg_data.fetch.reader import RawMessage, TelegramReaderPort

logger = logging.getLogger(__name__)

BATCH_PAUSE = 1.0


def _archive_since() -> datetime:
    return datetime.fromisoformat(settings.archive_since).replace(
        tzinfo=timezone.utc
    )


async def backfill_source(
    source: Source,
    reader: TelegramReaderPort,
    session: Session,
) -> int:
    """Выкачать историю Source до границы архива.

    Возвращает количество сохранённых Message.
    """
    state = session.get(SyncState, source.id)
    if state is None:
        state = SyncState(source_id=source.id, backfill_done=False)
        session.add(state)
        session.flush()

    if state.backfill_done:
        logger.info("Source %s: backfill уже завершён", source.id)
        return 0

    archive_since = _archive_since()
    saved = 0

    # newest_processed_id фиксируется при старте backfill
    if state.newest_processed_id is None:
        max_id_row = session.execute(
            __import__("sqlalchemy").select(
                __import__("sqlalchemy").func.max(Message.tg_msg_id)
            ).where(Message.source_id == source.id)
        ).scalar()
        state.newest_processed_id = max_id_row or 0
        session.flush()

    max_id = state.oldest_processed_id or 0

    async for raw in reader.iter_messages(
        source.tg_id,
        max_id=state.oldest_processed_id if state.oldest_processed_id else 0,
    ):
        if raw.date.replace(tzinfo=timezone.utc) < archive_since:
            logger.info(
                "Source %s: достигнута граница архива %s", source.id, archive_since
            )
            state.backfill_done = True
            state.last_sync_at = datetime.now(timezone.utc)
            session.flush()
            return saved

        if _process_raw(raw, source, state, session):
            saved += 1

        if saved % 100 == 0:
            session.flush()
            logger.debug("Source %s: сохранено %d сообщений", source.id, saved)

    state.backfill_done = True
    state.last_sync_at = datetime.now(timezone.utc)
    session.commit()
    logger.info("Source %s: backfill завершён, сохранено %d", source.id, saved)
    return saved


def _process_raw(
    raw: RawMessage,
    source: Source,
    state: SyncState,
    session: Session,
) -> bool:
    """Обработать одно сырое сообщение. Возвращает True если Message сохранён."""
    if state.oldest_processed_id is None or raw.tg_msg_id < state.oldest_processed_id:
        state.oldest_processed_id = raw.tg_msg_id

    if raw.is_fwd_saved and raw.saved_from_peer_id is not None:
        from sqlalchemy import select
        channel_source = session.scalar(
            select(Source).where(Source.tg_id == raw.saved_from_peer_id)
        )
        if channel_source is not None:
            existing_root = session.execute(
                __import__("sqlalchemy").select(ThreadRoot).where(
                    ThreadRoot.source_id == source.id,
                    ThreadRoot.root_msg_id == raw.tg_msg_id,
                )
            ).scalar_one_or_none()
            if existing_root is None:
                root = ThreadRoot(
                    source_id=source.id,
                    root_msg_id=raw.tg_msg_id,
                    channel_source_id=channel_source.id,
                    channel_msg_id=raw.saved_from_msg_id,
                )
                session.add(root)
        return False

    if not raw.text:
        return False

    from sqlalchemy.dialects.postgresql import insert as pg_insert
    stmt = pg_insert(Message).values(
        source_id=source.id,
        tg_msg_id=raw.tg_msg_id,
        date=raw.date,
        edit_date=raw.edit_date,
        text=raw.text,
        entities=raw.entities,
        reply_to_msg_id=raw.reply_to_msg_id,
        reply_to_top_id=raw.reply_to_top_id,
        grouped_id=raw.grouped_id,
        topic_id=raw.topic_id,
        fwd_from=raw.fwd_from,
        views=raw.views,
        reactions=raw.reactions,
    ).on_conflict_do_nothing(index_elements=["source_id", "tg_msg_id"])
    session.execute(stmt)
    return True
