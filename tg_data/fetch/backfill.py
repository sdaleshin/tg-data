"""Backfill: выкачка истории одного Source от newest до границы архива."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from tg_data.config import settings
from tg_data.db.models import Message, Source, SourceKind, SyncState, ThreadRoot
from tg_data.fetch.reader import RawMessage, TelegramReaderPort
from tg_data.report import is_report

logger = logging.getLogger(__name__)

CHECKPOINT_EVERY = 100


def _archive_since() -> datetime:
    return datetime.fromisoformat(settings.archive_since).replace(
        tzinfo=timezone.utc
    )


async def _fix_newest_processed_id(
    source: Source,
    state: SyncState,
    reader: TelegramReaderPort,
    session: Session,
) -> None:
    """Зафиксировать max(tg_msg_id) в Telegram на момент старта backfill."""
    async for raw in reader.iter_messages(source.tg_id, limit=1):
        state.newest_processed_id = raw.tg_msg_id
        session.flush()
        return

    state.newest_processed_id = 0
    session.flush()


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
    processed = 0

    if state.newest_processed_id is None:
        await _fix_newest_processed_id(source, state, reader, session)

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
            session.commit()
            return saved

        if _process_raw(raw, source, state, session):
            saved += 1

        # Checkpoint по обработанным, а не по сохранённым: длинный участок
        # медиа без текста иначе прошёл бы без единого commit и обрыв потерял
        # бы весь прогресс oldest_processed_id.
        processed += 1
        if processed % CHECKPOINT_EVERY == 0:
            session.commit()
            logger.debug(
                "Source %s: checkpoint на %d обработанных, сохранено %d",
                source.id,
                processed,
                saved,
            )

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

    # Только в CommentChat автокопия поста — ThreadRoot. В личном чате те же
    # saved_from_* поля стоят у любой пересылки себе, и её текст надо хранить,
    # а не подменять записью о треде.
    if (
        source.kind == SourceKind.comment_chat
        and raw.is_fwd_saved
        and raw.saved_from_peer_id is not None
    ):
        channel_source = session.scalar(
            select(Source).where(Source.tg_id == raw.saved_from_peer_id)
        )
        if channel_source is not None:
            existing_root = session.execute(
                select(ThreadRoot).where(
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

    # Свой heartbeat-отчёт в архив не идёт: Saved Messages может быть Source, и
    # без этой отсечки каждый цикл дописывал бы себе новую строку. Остальные
    # исходящие — обычный контент и сохраняются.
    if raw.is_outgoing and is_report(raw.text):
        return False

    if not raw.text:
        return False

    stmt = (
        pg_insert(Message)
        .values(
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
        )
        .on_conflict_do_nothing(index_elements=["source_id", "tg_msg_id"])
        .returning(Message.id)
    )
    inserted = session.execute(stmt).scalar_one_or_none()
    return inserted is not None
