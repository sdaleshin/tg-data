"""CLI точка входа — команды: auth, sources, pull, stats, scheduler."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Annotated

import typer
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from tg_data.config import settings
from tg_data.db.engine import engine
from tg_data.db.models import InactiveReason, Message, Source, SourceKind, SyncState

app = typer.Typer(help="tg-data: архив Telegram → Postgres")
sources_app = typer.Typer(help="Управление whitelist источников")
app.add_typer(sources_app, name="sources")

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)


# ─── auth ────────────────────────────────────────────────────────────────────


@app.command()
def auth() -> None:
    """Войти в личный аккаунт Telegram (интерактивно)."""
    from tg_data.fetch.client import make_client

    async def _auth() -> None:
        client = make_client()
        await client.start()
        me = await client.get_me()
        typer.echo(f"Вошли как {me.first_name} (id={me.id})")
        await client.disconnect()

    asyncio.run(_auth())


# ─── sources ─────────────────────────────────────────────────────────────────


@sources_app.command("add")
def sources_add(
    peer: Annotated[str, typer.Argument(help="Telegram peer id или @username")],
    kind: Annotated[SourceKind, typer.Option(help="Тип источника")] = SourceKind.channel,
) -> None:
    """Добавить Source в whitelist."""
    from tg_data.fetch.client import make_client

    async def _add() -> None:
        client = make_client()
        await client.start()
        try:
            from tg_data.fetch.reader import TelegramReader
            reader = TelegramReader(client)
            lookup: int | str = int(peer) if peer.lstrip("-").isdigit() else peer
            info = await reader.get_chat_info(lookup)
        finally:
            await client.disconnect()

        with Session(engine) as session:
            existing = session.scalar(select(Source).where(Source.tg_id == info["tg_id"]))
            if existing:
                typer.echo(f"Source уже существует: id={existing.id}")
                return

            source = Source(
                tg_id=info["tg_id"],
                kind=kind,
                username=info.get("username"),
                title=info.get("title"),
                is_active=True,
            )
            session.add(source)
            session.flush()

            state = SyncState(source_id=source.id, backfill_done=False)
            session.add(state)
            session.commit()
            typer.echo(f"Добавлен Source id={source.id}: {source.title or source.username}")

            if info.get("linked_chat_id"):
                typer.echo(
                    f"  → Канал имеет CommentChat tg_id={info['linked_chat_id']}. "
                    "Добавьте вручную: tg sources add <id> --kind comment_chat"
                )

    asyncio.run(_add())


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
            status = "✓" if s.is_active else f"✗ ({s.inactive_reason})"
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
        session.delete(source)
        session.commit()
        typer.echo(f"Source {source_id} ({title}) удалён")


@sources_app.command("discover")
def sources_discover(
    filter_str: Annotated[str | None, typer.Argument(help="Фильтр по подстроке")] = None,
) -> None:
    """Список чатов аккаунта с возможностью добавить по номеру."""
    from tg_data.fetch.client import make_client

    async def _discover() -> None:
        client = make_client()
        await client.start()
        try:
            dialogs = await client.get_dialogs(limit=200)
        finally:
            await client.disconnect()

        with Session(engine) as session:
            added_ids = {s.tg_id for s in session.scalars(select(Source)).all()}

        chats = []
        for d in dialogs:
            entity = d.entity
            title = getattr(entity, "title", None) or getattr(entity, "first_name", "?")
            if filter_str and filter_str.lower() not in title.lower():
                continue
            chats.append((entity.id, title, d.unread_count))

        if not chats:
            typer.echo("Чаты не найдены")
            return

        typer.echo(f"{'#':>3}  {'ID':>14}  {'Название'}")
        for i, (eid, title, _) in enumerate(chats, 1):
            marker = " [добавлен]" if eid in added_ids else ""
            typer.echo(f"{i:>3}  {eid:>14}  {title}{marker}")

        raw = typer.prompt("\nНомера для добавления (через запятую, Enter=пропустить)", default="")
        if not raw.strip():
            return

        nums = [n.strip() for n in raw.split(",") if n.strip().isdigit()]
        for n in nums:
            idx = int(n) - 1
            if 0 <= idx < len(chats):
                eid, title, _ = chats[idx]
                typer.echo(f"  tg sources add {eid}  # {title}")

    asyncio.run(_discover())


@sources_app.command("sync")
def sources_sync() -> None:
    """Обновить метаданные Source и автодобавить CommentChat каналов."""
    from tg_data.fetch.client import make_client

    async def _sync() -> None:
        client = make_client()
        await client.start()
        try:
            from tg_data.fetch.reader import TelegramReader
            reader = TelegramReader(client)

            with Session(engine) as session:
                sources = session.scalars(select(Source).where(Source.is_active.is_(True))).all()
                for source in sources:
                    try:
                        info = await reader.get_chat_info(source.tg_id)
                        source.username = info.get("username")
                        source.title = info.get("title")

                        linked = info.get("linked_chat_id")
                        if linked and source.kind == SourceKind.channel:
                            existing = session.scalar(
                                select(Source).where(Source.tg_id == linked)
                            )
                            if not existing:
                                comment_info = await reader.get_chat_info(linked)
                                cc = Source(
                                    tg_id=linked,
                                    kind=SourceKind.comment_chat,
                                    username=comment_info.get("username"),
                                    title=comment_info.get("title"),
                                    linked_channel_id=source.tg_id,
                                    is_active=True,
                                )
                                session.add(cc)
                                session.flush()
                                session.add(SyncState(source_id=cc.id, backfill_done=False))
                                typer.echo(f"  + CommentChat: {cc.title or cc.tg_id}")

                        typer.echo(f"  ✓ {source.title or source.tg_id}")
                    except Exception as e:
                        typer.echo(f"  ✗ Source {source.id}: {e}")
                        source.is_active = False
                        source.inactive_reason = InactiveReason.no_access

                session.commit()
        finally:
            await client.disconnect()

    asyncio.run(_sync())


# ─── pull / scheduler ────────────────────────────────────────────────────────


async def run_pull(
    client,
    *,
    backfill: bool = False,
    resume_all: bool = False,
) -> tuple[int, int]:
    """Выкачать сообщения. Возвращает (total_messages, sources_count)."""
    from tg_data.fetch.advisory_lock import advisory_lock
    from tg_data.fetch.reader import TelegramReader

    reader = TelegramReader(client)

    with Session(engine) as session:
        with advisory_lock(session):
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
                    from tg_data.fetch.backfill import backfill_source
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
                    from tg_data.fetch.increment import increment_source
                    n = await increment_source(source, reader, session)
                    total += n

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
    try:
        if ok:
            text = (
                f"✅ tg-data pull\n"
                f"Новых сообщений: {total}\n"
                f"Источников: {sources_count}"
            )
        else:
            text = f"❌ tg-data error\n{error}"
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
    from tg_data.fetch.advisory_lock import LockBusy
    from tg_data.fetch.client import make_client

    async def _pull() -> None:
        client = make_client()
        await client.start()
        try:
            await run_pull(client, backfill=backfill, resume_all=resume_all)
        except LockBusy as e:
            typer.echo(str(e))
            raise typer.Exit(1) from e
        finally:
            await client.disconnect()

    asyncio.run(_pull())


@app.command()
def scheduler() -> None:
    """Фоновый цикл: добор backfill + инкремент по расписанию."""
    from tg_data.fetch.advisory_lock import LockBusy
    from tg_data.fetch.client import make_client

    async def _scheduler() -> None:
        client = make_client()
        await client.start()
        typer.echo(
            f"Scheduler запущен: backfill каждые {settings.backfill_resume_interval_seconds}s, "
            f"инкремент каждые {settings.pull_interval_seconds}s"
        )
        last_backfill = 0.0
        last_pull = 0.0
        try:
            while True:
                now = time.monotonic()
                if now - last_backfill >= settings.backfill_resume_interval_seconds:
                    try:
                        await run_pull(client, backfill=True, resume_all=True)
                    except LockBusy:
                        logger.info("Backfill пропущен: другой процесс держит lock")
                    except Exception as e:
                        logger.exception("Ошибка backfill")
                        await _send_report(client, 0, 0, ok=False, error=str(e))
                    last_backfill = now

                if now - last_pull >= settings.pull_interval_seconds:
                    try:
                        await run_pull(client, backfill=False)
                    except LockBusy:
                        logger.info("Инкремент пропущен: другой процесс держит lock")
                    except Exception as e:
                        logger.exception("Ошибка инкремента")
                        await _send_report(client, 0, 0, ok=False, error=str(e))
                    last_pull = now

                await asyncio.sleep(60)
        finally:
            await client.disconnect()

    asyncio.run(_scheduler())


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
