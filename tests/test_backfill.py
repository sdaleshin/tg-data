"""Тесты backfill на заглушке без сети."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import datetime, timezone

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from tg_data.db.models import Message, Source, SourceKind, SyncState, ThreadRoot
from tg_data.fetch.backfill import backfill_source
from tg_data.fetch.reader import RawMessage
from tg_data.report import pull_report


def make_raw(
    tg_msg_id: int,
    date: datetime,
    text: str | None = "hello",
    is_fwd_saved: bool = False,
    saved_from_peer_id: int | None = None,
    saved_from_msg_id: int | None = None,
    is_outgoing: bool = False,
) -> RawMessage:
    return RawMessage(
        tg_msg_id=tg_msg_id,
        date=date,
        edit_date=None,
        text=text,
        entities=None,
        reply_to_msg_id=None,
        reply_to_top_id=None,
        grouped_id=None,
        topic_id=None,
        fwd_from=None,
        views=None,
        reactions=None,
        is_fwd_saved=is_fwd_saved,
        saved_from_peer_id=saved_from_peer_id,
        saved_from_msg_id=saved_from_msg_id,
        is_outgoing=is_outgoing,
    )


class StubReader:
    def __init__(self, messages: list[RawMessage]) -> None:
        self._messages = messages

    async def iter_messages(
        self,
        tg_id: int,
        *,
        min_id: int = 0,
        max_id: int = 0,
        limit: int | None = None,
    ) -> AsyncIterator[RawMessage]:
        count = 0
        for m in self._messages:
            if max_id and m.tg_msg_id >= max_id:
                continue
            if min_id and m.tg_msg_id <= min_id:
                continue
            yield m
            count += 1
            if limit is not None and count >= limit:
                return

    async def get_chat_info(self, tg_id: int) -> dict:
        return {"tg_id": tg_id, "username": None, "title": "Test"}


def make_source(
    session: Session, tg_id: int = 100, kind: SourceKind = SourceKind.channel
) -> Source:
    source = Source(tg_id=tg_id, kind=kind, title="Test Channel")
    session.add(source)
    session.flush()
    return source


@pytest.mark.asyncio
async def test_backfill_saves_messages_with_text(session: Session) -> None:
    source = make_source(session, tg_id=1001)
    messages = [
        make_raw(10, datetime(2024, 6, 1, tzinfo=timezone.utc), text="msg 10"),
        make_raw(9, datetime(2024, 5, 1, tzinfo=timezone.utc), text="msg 9"),
        make_raw(8, datetime(2024, 2, 1, tzinfo=timezone.utc), text="msg 8"),
    ]
    reader = StubReader(messages)

    saved = await backfill_source(source, reader, session)

    assert saved == 3
    msgs = session.query(Message).filter_by(source_id=source.id).all()
    assert len(msgs) == 3


@pytest.mark.asyncio
async def test_backfill_skips_messages_without_text(session: Session) -> None:
    source = make_source(session, tg_id=1002)
    messages = [
        make_raw(5, datetime(2024, 3, 1, tzinfo=timezone.utc), text="has text"),
        make_raw(4, datetime(2024, 2, 1, tzinfo=timezone.utc), text=None),
        make_raw(3, datetime(2024, 1, 15, tzinfo=timezone.utc), text=""),
    ]
    reader = StubReader(messages)

    saved = await backfill_source(source, reader, session)

    assert saved == 1
    msgs = session.query(Message).filter_by(source_id=source.id).all()
    assert len(msgs) == 1
    assert msgs[0].tg_msg_id == 5


@pytest.mark.asyncio
async def test_backfill_skips_own_heartbeat_report(session: Session) -> None:
    """Свой отчёт в архив не идёт, а остальные исходящие — идут."""
    source = make_source(session, tg_id=1008, kind=SourceKind.private)
    messages = [
        make_raw(
            30,
            datetime(2024, 6, 1, tzinfo=timezone.utc),
            text=pull_report(0, 4),
            is_outgoing=True,
        ),
        make_raw(
            29,
            datetime(2024, 5, 1, tzinfo=timezone.utc),
            text="моя заметка себе",
            is_outgoing=True,
        ),
    ]
    reader = StubReader(messages)

    saved = await backfill_source(source, reader, session)

    assert saved == 1
    msgs = session.query(Message).filter_by(source_id=source.id).all()
    assert [m.tg_msg_id for m in msgs] == [29]


@pytest.mark.asyncio
async def test_private_forward_is_archived_not_turned_into_thread_root(
    session: Session,
) -> None:
    """Пересылка себе — контент, а не ThreadRoot.

    saved_from_* стоят у любой пересылки, поэтому вне CommentChat их нельзя
    принимать за автокопию поста канала: иначе текст молча теряется.
    """
    channel = make_source(session, tg_id=9100)
    saved_messages = make_source(session, tg_id=9101, kind=SourceKind.private)

    messages = [
        make_raw(
            200,
            datetime(2024, 6, 1, tzinfo=timezone.utc),
            text="переслал себе важный пост",
            is_fwd_saved=True,
            saved_from_peer_id=9100,
            saved_from_msg_id=7,
            is_outgoing=True,
        ),
    ]

    saved = await backfill_source(saved_messages, StubReader(messages), session)

    assert saved == 1
    msgs = session.query(Message).filter_by(source_id=saved_messages.id).all()
    assert [m.text for m in msgs] == ["переслал себе важный пост"]
    assert session.query(ThreadRoot).filter_by(channel_source_id=channel.id).count() == 0


@pytest.mark.asyncio
async def test_backfill_checkpoints_without_saved_messages(
    session: Session, db_engine, monkeypatch
) -> None:
    """Участок без текста тоже коммитит прогресс oldest_processed_id."""
    import tg_data.fetch.backfill as bf_module

    monkeypatch.setattr(bf_module, "CHECKPOINT_EVERY", 2)

    source = make_source(session, tg_id=1009)
    source_id = source.id
    messages = [
        make_raw(50, datetime(2024, 6, 1, tzinfo=timezone.utc), text=None),
        make_raw(49, datetime(2024, 6, 1, tzinfo=timezone.utc), text=None),
        make_raw(48, datetime(2024, 6, 1, tzinfo=timezone.utc), text=None),
    ]

    class Interrupted(Exception):
        pass

    class FailingReader(StubReader):
        async def iter_messages(self, tg_id: int, **kwargs):
            count = 0
            async for m in super().iter_messages(tg_id, **kwargs):
                yield m
                count += 1
                if count == 2:
                    raise Interrupted

    with pytest.raises(Interrupted):
        await backfill_source(source, FailingReader(messages), session)

    with Session(db_engine) as fresh:
        state = fresh.get(SyncState, source_id)
        assert state is not None
        assert state.oldest_processed_id == 49
        assert state.backfill_done is False


@pytest.mark.asyncio
async def test_backfill_stops_at_archive_boundary(session: Session, monkeypatch) -> None:
    source = make_source(session, tg_id=1003)

    import tg_data.fetch.backfill as bf_module
    monkeypatch.setattr(
        bf_module,
        "_archive_since",
        lambda: datetime(2024, 3, 1, tzinfo=timezone.utc),
    )

    messages = [
        make_raw(20, datetime(2024, 6, 1, tzinfo=timezone.utc), text="after"),
        make_raw(15, datetime(2024, 4, 1, tzinfo=timezone.utc), text="after2"),
        make_raw(10, datetime(2024, 2, 1, tzinfo=timezone.utc), text="before boundary"),
    ]
    reader = StubReader(messages)

    saved = await backfill_source(source, reader, session)

    assert saved == 2
    msgs = session.query(Message).filter_by(source_id=source.id).all()
    assert all(m.tg_msg_id in (20, 15) for m in msgs)


@pytest.mark.asyncio
async def test_backfill_commits_on_archive_boundary(
    session: Session, db_engine, monkeypatch
) -> None:
    source = make_source(session, tg_id=1006)
    source_id = source.id

    import tg_data.fetch.backfill as bf_module
    monkeypatch.setattr(
        bf_module,
        "_archive_since",
        lambda: datetime(2024, 3, 1, tzinfo=timezone.utc),
    )

    messages = [
        make_raw(20, datetime(2024, 6, 1, tzinfo=timezone.utc), text="after"),
        make_raw(10, datetime(2024, 2, 1, tzinfo=timezone.utc), text="before boundary"),
    ]
    reader = StubReader(messages)

    await backfill_source(source, reader, session)
    session.commit()

    with Session(db_engine) as fresh:
        count = fresh.scalar(
            select(func.count())
            .select_from(Message)
            .where(Message.source_id == source_id)
        )
        assert count == 1


@pytest.mark.asyncio
async def test_backfill_sets_newest_processed_id(session: Session) -> None:
    source = make_source(session, tg_id=1007)
    messages = [
        make_raw(10, datetime(2024, 6, 1, tzinfo=timezone.utc), text="msg 10"),
        make_raw(9, datetime(2024, 5, 1, tzinfo=timezone.utc), text="msg 9"),
    ]
    reader = StubReader(messages)

    await backfill_source(source, reader, session)

    state = session.get(SyncState, source.id)
    assert state is not None
    assert state.newest_processed_id == 10


@pytest.mark.asyncio
async def test_backfill_marks_done(session: Session) -> None:
    source = make_source(session, tg_id=1004)
    reader = StubReader([
        make_raw(1, datetime(2024, 6, 1, tzinfo=timezone.utc), text="only"),
    ])

    await backfill_source(source, reader, session)

    state = session.get(SyncState, source.id)
    assert state is not None
    assert state.backfill_done is True


@pytest.mark.asyncio
async def test_backfill_skips_thread_roots_and_creates_record(session: Session) -> None:
    channel = make_source(session, tg_id=9000)
    comment_chat = Source(
        tg_id=9001,
        kind=SourceKind.comment_chat,
        title="CommentChat",
        linked_channel_id=9000,
    )
    session.add(comment_chat)
    session.flush()

    messages = [
        make_raw(
            100,
            datetime(2024, 6, 1, tzinfo=timezone.utc),
            text="original post",
            is_fwd_saved=True,
            saved_from_peer_id=9000,
            saved_from_msg_id=50,
        ),
        make_raw(101, datetime(2024, 6, 1, tzinfo=timezone.utc), text="comment"),
    ]
    reader = StubReader(messages)

    saved = await backfill_source(comment_chat, reader, session)

    assert saved == 1
    roots = session.query(ThreadRoot).filter_by(source_id=comment_chat.id).all()
    assert len(roots) == 1
    assert roots[0].root_msg_id == 100
    assert roots[0].channel_source_id == channel.id
    assert roots[0].channel_msg_id == 50


@pytest.mark.asyncio
async def test_backfill_idempotent(session: Session) -> None:
    source = make_source(session, tg_id=1005)
    messages = [make_raw(7, datetime(2024, 6, 1, tzinfo=timezone.utc), text="text")]
    reader = StubReader(messages)

    await backfill_source(source, reader, session)
    await backfill_source(source, reader, session)

    msgs = session.query(Message).filter_by(source_id=source.id).all()
    assert len(msgs) == 1
