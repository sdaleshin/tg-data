"""CLI точка входа — команды: auth, sources, pull, stats, scheduler."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Annotated

import typer
from sqlalchemy import Text, delete, func, select, update
from sqlalchemy.orm import Session

from tg_data.config import settings
from tg_data.db.engine import engine
from tg_data.db.models import InactiveReason, Message, Source, SourceKind, SyncState, ThreadRoot

app = typer.Typer(help="tg-data: архив Telegram → Postgres")
sources_app = typer.Typer(help="Управление whitelist источников")
app.add_typer(sources_app, name="sources")

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def _with_telegram(
    action: Callable[..., Awaitable[None]],
    *,
    interactive: bool = False,
) -> None:
    """Выполнить action с подключённым клиентом под advisory lock.

    Лок и подключение живут ровно столько, сколько работает команда, — см.
    telegram_session. Занятый лок и неавторизованная сессия превращаются в
    сообщение и код возврата 1, а не в traceback.
    """
    from tg_data.fetch.advisory_lock import LockBusy
    from tg_data.fetch.client import NotAuthorized, telegram_session

    async def _main() -> None:
        async with telegram_session(interactive=interactive) as client:
            await action(client)

    try:
        asyncio.run(_main())
    except (LockBusy, NotAuthorized) as e:
        typer.echo(str(e))
        raise typer.Exit(1) from e


# ─── auth ────────────────────────────────────────────────────────────────────


@app.command()
def auth() -> None:
    """Войти в личный аккаунт Telegram (интерактивно)."""

    async def _auth(client) -> None:  # noqa: ANN001
        me = await client.get_me()
        typer.echo(f"Вошли как {me.first_name} (id={me.id})")

    _with_telegram(_auth, interactive=True)


# ─── sources ─────────────────────────────────────────────────────────────────


def _parse_peer(peer: str) -> int | str:
    return int(peer) if peer.lstrip("-").isdigit() else peer


async def _add_source(
    reader,  # noqa: ANN001
    session: Session,
    peer: str,
    kind: SourceKind | None = None,
) -> Source | None:
    """Добавить Source в whitelist. Возвращает None, если добавить нечего.

    Общее ядро для `sources add` и `sources discover`: обе команды работают
    внутри уже запущенного event loop и не могут звать друг друга через
    asyncio.run.
    """
    info = await reader.get_chat_info(_parse_peer(peer))

    existing = session.scalar(select(Source).where(Source.tg_id == info["tg_id"]))
    if existing:
        typer.echo(f"  Source уже существует: id={existing.id}")
        return None

    source = Source(
        tg_id=info["tg_id"],
        kind=kind or SourceKind(info["kind"]),
        username=info.get("username"),
        title=info.get("title"),
        is_active=True,
    )
    session.add(source)
    session.flush()
    session.add(SyncState(source_id=source.id, backfill_done=False))
    session.commit()
    typer.echo(f"  Добавлен Source id={source.id}: {source.title or source.username}")

    linked = info.get("linked_chat")
    if linked:
        typer.echo(
            f"    → у канала есть CommentChat «{linked.get('title')}», "
            "добавить его: make sources-sync"
        )

    return source


@sources_app.command("add")
def sources_add(
    peer: Annotated[str, typer.Argument(help="Telegram peer id или @username")],
    kind: Annotated[
        SourceKind | None, typer.Option(help="Тип источника (авто, если не указан)")
    ] = None,
) -> None:
    """Добавить Source в whitelist."""

    async def _add(client) -> None:  # noqa: ANN001
        from tg_data.fetch.reader import TelegramReader

        reader = TelegramReader(client)
        with Session(engine) as session:
            await _add_source(reader, session, peer, kind)

    _with_telegram(_add)


@sources_app.command("disable")
def sources_disable(source_id: int) -> None:
    """Деактивировать Source (данные остаются)."""
    with Session(engine) as session:
        source = session.get(Source, source_id)
        if not source:
            typer.echo(f"Source {source_id} не найден")
            raise typer.Exit(1)
        source.is_active = False
        source.inactive_reason = InactiveReason.disabled_by_user
        session.commit()
        typer.echo(f"Source {source_id} деактивирован")


@sources_app.command("enable")
def sources_enable(source_id: int) -> None:
    """Активировать Source."""
    with Session(engine) as session:
        source = session.get(Source, source_id)
        if not source:
            typer.echo(f"Source {source_id} не найден")
            raise typer.Exit(1)
        source.is_active = True
        source.inactive_reason = None
        session.commit()
        typer.echo(f"Source {source_id} активирован")


@sources_app.command("list")
def sources_list() -> None:
    """Показать все источники."""
    with Session(engine) as session:
        sources = session.scalars(select(Source).order_by(Source.added_at)).all()
        if not sources:
            typer.echo("Whitelist пуст")
            return

        for s in sources:
            state = session.get(SyncState, s.id)
            status = "✓" if s.is_active else f"✗ ({s.inactive_reason.value if s.inactive_reason else '?'})"
            backfill = "backfill_done" if (state and state.backfill_done) else "backfill_pending"
            typer.echo(
                f"  [{s.id}] {status} {s.kind.value:12} {s.title or s.username or '?'} ({backfill})"
            )


@sources_app.command("purge")
def sources_purge(
    source_id: int,
    yes: Annotated[bool, typer.Option("--yes", help="Подтвердить без интерактива")] = False,
) -> None:
    """Физически удалить Source вместе со всеми его Message."""
    if not yes:
        typer.confirm(
            f"Удалить Source {source_id} и все его сообщения? Это необратимо.",
            abort=True,
        )
    with Session(engine) as session:
        source = session.get(Source, source_id)
        if not source:
            typer.echo(f"Source {source_id} не найден")
            raise typer.Exit(1)
        title = source.title or source.username
        tg_id = source.tg_id

        # CommentChat переживает удаление своего канала, но теряет мост к его
        # постам — про это надо сказать вслух, иначе он молча останется в
        # whitelist без linked_channel_id.
        detached = session.execute(
            select(Source.id, func.coalesce(Source.title, Source.tg_id.cast(Text)))
            .where(Source.linked_channel_id == tg_id)
        ).all()
        session.execute(
            update(Source)
            .where(Source.linked_channel_id == tg_id)
            .values(linked_channel_id=None)
        )
        session.execute(
            delete(ThreadRoot).where(
                (ThreadRoot.source_id == source_id)
                | (ThreadRoot.channel_source_id == source_id)
            )
        )
        session.execute(delete(Message).where(Message.source_id == source_id))
        session.execute(delete(SyncState).where(SyncState.source_id == source_id))
        session.execute(delete(Source).where(Source.id == source_id))
        session.commit()
        typer.echo(f"Source {source_id} ({title}) удалён")

        for chat_id, chat_title in detached:
            typer.echo(
                f"  ! Source {chat_id} ({chat_title}) остался без канала: "
                "его комментарии больше не связаны с постами. "
                f"Удалить: make sources-purge ID={chat_id}"
            )


@sources_app.command("discover")
def sources_discover(
    filter_str: Annotated[str | None, typer.Argument(help="Фильтр по подстроке")] = None,
) -> None:
    """Список чатов аккаунта с возможностью добавить по номеру."""

    async def _discover(client) -> None:  # noqa: ANN001
        from tg_data.fetch.reader import TelegramReader

        me = await client.get_me()
        dialogs = await client.get_dialogs(limit=200)
        reader = TelegramReader(client)

        with Session(engine) as session:
            added_ids = {s.tg_id for s in session.scalars(select(Source)).all()}

            chats: list[tuple[int, str]] = []
            for d in dialogs:
                entity = d.entity
                title = (
                    getattr(entity, "title", None)
                    or getattr(entity, "first_name", None)
                    or "?"
                )
                if filter_str and filter_str.lower() not in title.lower():
                    continue
                chats.append((entity.id, title))

            if not chats:
                typer.echo("Чаты не найдены")
                return

            typer.echo(f"{'#':>3}  {'ID':>14}  {'Название'}")
            for i, (eid, title) in enumerate(chats, 1):
                marks = []
                # Диалог с самим собой Telegram отдаёт как чат с собственным
                # User — по названию его от тёзки не отличить.
                if eid == me.id:
                    marks.append("Избранное")
                if eid in added_ids:
                    marks.append("добавлен")
                suffix = f" [{', '.join(marks)}]" if marks else ""
                typer.echo(f"{i:>3}  {eid:>14}  {title}{suffix}")

            raw = typer.prompt(
                "\nНомера для добавления (через запятую, Enter=пропустить)", default=""
            )
            if not raw.strip():
                return

            for token in (t.strip() for t in raw.split(",")):
                if not token.isdigit():
                    continue
                idx = int(token) - 1
                if not 0 <= idx < len(chats):
                    typer.echo(f"  Пропуск {token} — нет такого номера")
                    continue

                eid, title = chats[idx]
                if eid in added_ids:
                    typer.echo(f"  Пропуск {eid} — уже в whitelist")
                    continue

                typer.echo(f"  Добавляю {eid} ({title})...")
                source = await _add_source(reader, session, str(eid))
                if source is not None:
                    added_ids.add(source.tg_id)

    _with_telegram(_discover)


def _ensure_comment_chat(session: Session, channel: Source, linked: dict) -> None:
    """Автодобавить CommentChat канала, если его ещё нет в whitelist."""
    if session.scalar(select(Source).where(Source.tg_id == linked["tg_id"])):
        return

    comment_chat = Source(
        tg_id=linked["tg_id"],
        kind=SourceKind.comment_chat,
        username=linked.get("username"),
        title=linked.get("title"),
        linked_channel_id=channel.tg_id,
        is_active=True,
    )
    session.add(comment_chat)
    session.flush()
    session.add(SyncState(source_id=comment_chat.id, backfill_done=False))
    typer.echo(f"  + CommentChat: {comment_chat.title or comment_chat.tg_id}")


def _handle_sync_error(source: Source, error: Exception) -> None:
    """Гасить Source только за отказ доступа.

    Сетевой сбой или таймаут — повод повторить позже, а не выкидывать Source
    из whitelist: inactive_reason=no_access означает конечный ответ Telegram
    (ADR-0004), и снимать его пришлось бы вручную.
    """
    from telethon.errors import ChannelPrivateError, ChatAdminRequiredError

    # ValueError — то, чем Telethon отвечает на «Could not find the input entity».
    if isinstance(error, (ChannelPrivateError, ChatAdminRequiredError, ValueError)):
        source.is_active = False
        source.inactive_reason = InactiveReason.no_access
        typer.echo(f"  ✗ Source {source.id}: нет доступа — {error}")
        return

    logger.warning(
        "Source %s: %s, статус не меняем — %s", source.id, type(error).__name__, error
    )
    typer.echo(f"  ! Source {source.id}: временная ошибка, статус сохранён — {error}")


@sources_app.command("sync")
def sources_sync() -> None:
    """Обновить метаданные Source и автодобавить CommentChat каналов."""

    async def _sync(client) -> None:  # noqa: ANN001
        from tg_data.fetch.reader import TelegramReader

        reader = TelegramReader(client)

        with Session(engine) as session:
            sources = session.scalars(
                select(Source).where(Source.is_active.is_(True))
            ).all()

            for source in sources:
                try:
                    info = await reader.get_chat_info(source.tg_id)
                except Exception as e:
                    _handle_sync_error(source, e)
                    continue

                source.username = info.get("username")
                source.title = info.get("title")

                linked = info.get("linked_chat")
                if linked and source.kind == SourceKind.channel:
                    _ensure_comment_chat(session, source, linked)

                typer.echo(f"  ✓ {source.title or source.tg_id}")

            session.commit()

    _with_telegram(_sync)


# ─── pull / scheduler ────────────────────────────────────────────────────────


async def run_pull(
    client,
    *,
    backfill: bool = False,
    resume_all: bool = False,
) -> tuple[int, int]:
    """Выкачать сообщения. Возвращает (total_messages, sources_count).

    Вызывается уже под advisory lock — его держит telegram_session.
    """
    from tg_data.fetch.backfill import backfill_source
    from tg_data.fetch.increment import increment_source
    from tg_data.fetch.reader import TelegramReader

    reader = TelegramReader(client)

    with Session(engine) as session:
        if backfill:
            if resume_all:
                sources = session.scalars(
                    select(Source)
                    .join(SyncState, Source.id == SyncState.source_id)
                    .where(
                        Source.is_active.is_(True),
                        SyncState.backfill_done.is_(False),
                    )
                ).all()
            else:
                sources = session.scalars(
                    select(Source).where(Source.is_active.is_(True))
                ).all()

            total = 0
            for source in sources:
                n = await backfill_source(source, reader, session)
                total += n
                typer.echo(f"  Source {source.id}: +{n} сообщений")

            typer.echo(f"Backfill: {total} сообщений по {len(sources)} источникам")
        else:
            sources = session.scalars(
                select(Source)
                .join(SyncState, Source.id == SyncState.source_id)
                .where(
                    Source.is_active.is_(True),
                    SyncState.backfill_done.is_(True),
                )
            ).all()

            total = 0
            for source in sources:
                total += await increment_source(source, reader, session)

            typer.echo(f"Инкремент: {total} новых сообщений по {len(sources)} источникам")

        await _send_report(client, total, len(sources), ok=True)
        return total, len(sources)


async def _send_report(
    client,
    total: int,
    sources_count: int,
    *,
    ok: bool = True,
    error: str | None = None,
) -> None:
    from tg_data.report import error_report, pull_report

    try:
        text = (
            pull_report(total, sources_count) if ok else error_report(error or "?")
        )
        await client.send_message("me", text)
    except Exception as e:
        logging.warning("Не удалось отправить отчёт в Saved Messages: %s", e)


@app.command()
def pull(
    backfill: Annotated[bool, typer.Option(help="Backfill до границы архива")] = False,
    resume_all: Annotated[
        bool, typer.Option("--resume-all", help="Добрать все незавершённые backfill")
    ] = False,
) -> None:
    """Выкачать сообщения из Telegram в Postgres."""

    async def _pull(client) -> None:  # noqa: ANN001
        await run_pull(client, backfill=backfill, resume_all=resume_all)

    _with_telegram(_pull)


@app.command()
def scheduler() -> None:
    """Фоновый цикл: добор backfill + инкремент по расписанию."""
    from tg_data.fetch.advisory_lock import LockBusy
    from tg_data.fetch.client import NotAuthorized, telegram_session

    async def _cycle(*, backfill: bool, resume_all: bool = False) -> None:
        # Подключение и лок берутся на цикл, а не на весь срок жизни
        # контейнера: между циклами разовые команды могут работать с Telegram.
        async with telegram_session() as client:
            try:
                await run_pull(client, backfill=backfill, resume_all=resume_all)
            except Exception as e:
                logger.exception("Ошибка pull (backfill=%s)", backfill)
                await _send_report(client, 0, 0, ok=False, error=str(e))

    async def _loop() -> None:
        typer.echo(
            f"Scheduler запущен: backfill каждые {settings.backfill_resume_interval_seconds}s, "
            f"инкремент каждые {settings.pull_interval_seconds}s"
        )
        last_backfill = 0.0
        last_pull = 0.0
        while True:
            now = time.monotonic()
            if now - last_backfill >= settings.backfill_resume_interval_seconds:
                try:
                    await _cycle(backfill=True, resume_all=True)
                except LockBusy:
                    logger.info("Backfill пропущен: другой процесс держит lock")
                last_backfill = now

            if now - last_pull >= settings.pull_interval_seconds:
                try:
                    await _cycle(backfill=False)
                except LockBusy:
                    logger.info("Инкремент пропущен: другой процесс держит lock")
                last_pull = now

            await asyncio.sleep(60)

    try:
        asyncio.run(_loop())
    except NotAuthorized as e:
        typer.echo(str(e))
        raise typer.Exit(1) from e


# ─── stats ────────────────────────────────────────────────────────────────────


@app.command()
def stats() -> None:
    """Статистика: источники, сообщения, прогресс backfill."""
    with Session(engine) as session:
        total_sources = session.scalar(select(func.count()).select_from(Source))
        active_sources = session.scalar(
            select(func.count()).select_from(Source).where(Source.is_active.is_(True))
        )
        total_messages = session.scalar(select(func.count()).select_from(Message))
        backfill_done = session.scalar(
            select(func.count())
            .select_from(SyncState)
            .where(SyncState.backfill_done.is_(True))
        )
        backfill_pending = session.scalar(
            select(func.count())
            .select_from(SyncState)
            .where(SyncState.backfill_done.is_(False))
        )

        last_sync = session.scalar(
            select(func.max(SyncState.last_sync_at))
        )

        typer.echo(f"Источников: {active_sources} активных / {total_sources} всего")
        typer.echo(f"Сообщений: {total_messages}")
        typer.echo(f"Backfill: {backfill_done} завершён / {backfill_pending} в очереди")
        if last_sync:
            typer.echo(f"Последний синк: {last_sync.strftime('%Y-%m-%d %H:%M UTC')}")
        else:
            typer.echo("Последний синк: никогда")


if __name__ == "__main__":
    app()
