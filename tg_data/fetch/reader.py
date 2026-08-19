"""TelegramReader — узкий порт к Telethon.

Вся логика курсоров, границы архива и фильтрации пустых сообщений
живёт снаружи, в backfill.py и increment.py.
Этот модуль только передаёт сырые данные из Telegram.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from tg_data.config import settings

logger = logging.getLogger(__name__)


@dataclass
class RawMessage:
    tg_msg_id: int
    date: datetime
    edit_date: datetime | None
    text: str | None
    entities: list | None
    reply_to_msg_id: int | None
    reply_to_top_id: int | None
    grouped_id: int | None
    topic_id: int | None
    fwd_from: dict | None
    views: int | None
    reactions: dict | None
    # ThreadRoot detection
    is_fwd_saved: bool = False
    saved_from_peer_id: int | None = None
    saved_from_msg_id: int | None = None
    is_outgoing: bool = False


class TelegramReaderPort(Protocol):
    """Абстракция над источником сообщений — подменяется заглушкой в тестах."""

    def iter_messages(
        self,
        tg_id: int,
        *,
        min_id: int = 0,
        max_id: int = 0,
        limit: int | None = None,
    ) -> AsyncIterator[RawMessage]: ...

    async def get_chat_info(self, tg_id: int | str) -> dict: ...


def _resolve_topic_id(
    reply_to_msg_id: int | None,
    reply_to_top_id: int | None,
    *,
    forum_topic: bool,
) -> int | None:
    """Топик форума. Обычный reply топиком не является — там None.

    В корневом сообщении топика reply_to_top_id пуст, и топик задаёт
    reply_to_msg_id.
    """
    if not forum_topic:
        return None
    if reply_to_top_id is not None:
        return reply_to_top_id
    return reply_to_msg_id


class TelegramReader:
    """Реальная реализация TelegramReaderPort поверх Telethon."""

    FLOOD_WAIT_EXTRA = 5

    def __init__(self, client) -> None:  # noqa: ANN001
        self._client = client

    async def iter_messages(
        self,
        tg_id: int,
        *,
        min_id: int = 0,
        max_id: int = 0,
        limit: int | None = None,
    ) -> AsyncIterator[RawMessage]:
        from telethon.errors import FloodWaitError

        # Курсор переживает FloodWait: iter_messages идёт от новых к старым,
        # поэтому id последнего отданного сообщения — это новый max_id
        # (он exclusive), и после паузы мы продолжаем, а не начинаем заново.
        cursor_max_id = max_id
        remaining = limit

        while True:
            if remaining is not None and remaining <= 0:
                return

            kwargs: dict = {"reverse": False, "wait_time": settings.request_pause}
            if min_id:
                kwargs["min_id"] = min_id
            if cursor_max_id:
                kwargs["max_id"] = cursor_max_id
            if remaining is not None:
                kwargs["limit"] = remaining

            try:
                async for msg in self._client.iter_messages(tg_id, **kwargs):
                    if not hasattr(msg, "id"):
                        continue

                    yield _to_raw(msg)

                    cursor_max_id = msg.id
                    if remaining is not None:
                        remaining -= 1
                return
            except FloodWaitError as e:
                wait = e.seconds + self.FLOOD_WAIT_EXTRA
                logger.warning("FloodWait %s секунд", wait)
                await asyncio.sleep(wait)

    async def get_chat_info(self, tg_id: int | str) -> dict:
        from telethon.tl.types import Channel, User

        entity = await self._client.get_entity(tg_id)
        linked_chat: dict | None = None

        is_broadcast = isinstance(entity, Channel) and entity.broadcast
        if is_broadcast:
            linked_chat = await self._linked_chat(entity)

        # Chat, megagroup и всё остальное — group: единственные особые случаи
        # это broadcast-канал и личный чат.
        if isinstance(entity, User):
            kind = "private"
        elif is_broadcast:
            kind = "channel"
        else:
            kind = "group"

        return {
            "tg_id": entity.id,
            "username": getattr(entity, "username", None),
            "title": getattr(entity, "title", None)
            or getattr(entity, "first_name", None),
            "linked_chat": linked_chat,
            "kind": kind,
        }

    async def _linked_chat(self, channel) -> dict | None:  # noqa: ANN001
        """CommentChat канала — из channels.getFullChannel(канал).chats.

        Отдельный resolve по linked_chat_id запрещён ADR-0004: у чата, в
        котором аккаунт не состоит, нет access_hash нигде, кроме этого ответа.
        """
        from telethon.tl.functions.channels import GetFullChannelRequest

        try:
            full = await self._client(GetFullChannelRequest(channel))
        except Exception as e:
            logger.warning("getFullChannel для %s не удался: %s", channel.id, e)
            return None

        linked_id = getattr(full.full_chat, "linked_chat_id", None)
        if not linked_id:
            return None

        chat = next((c for c in full.chats if c.id == linked_id), None)
        if chat is None:
            logger.warning(
                "linked_chat_id=%s не найден в ответе getFullChannel", linked_id
            )
            return None

        return {
            "tg_id": chat.id,
            "username": getattr(chat, "username", None),
            "title": getattr(chat, "title", None),
        }


def _to_raw(msg) -> RawMessage:  # noqa: ANN001
    from telethon.tl.types import MessageFwdHeader, PeerChannel

    text = getattr(msg, "message", None) or getattr(msg, "caption", None)

    fwd: MessageFwdHeader | None = getattr(msg, "fwd_from", None)
    fwd_dict: dict | None = None
    is_fwd_saved = False
    saved_peer_id: int | None = None
    saved_msg_id: int | None = None

    if fwd is not None:
        saved_peer = getattr(fwd, "saved_from_peer", None)
        saved_msg_id = getattr(fwd, "saved_from_msg_id", None)
        if saved_peer is not None and saved_msg_id is not None:
            is_fwd_saved = True
            if isinstance(saved_peer, PeerChannel):
                saved_peer_id = saved_peer.channel_id

        fwd_dict = {
            "from_id": _peer_to_dict(getattr(fwd, "from_id", None)),
            "channel_post": getattr(fwd, "channel_post", None),
            "saved_from_peer": _peer_to_dict(saved_peer),
            "saved_from_msg_id": saved_msg_id,
        }

    reply = getattr(msg, "reply_to", None)
    reply_to_msg_id: int | None = None
    reply_to_top_id: int | None = None
    forum_topic = False
    if reply is not None:
        reply_to_msg_id = getattr(reply, "reply_to_msg_id", None)
        reply_to_top_id = getattr(reply, "reply_to_top_id", None)
        forum_topic = bool(getattr(reply, "forum_topic", False))

    reactions_dict: dict | None = None
    reactions = getattr(msg, "reactions", None)
    if reactions is not None:
        results = getattr(reactions, "results", None)
        if results:
            reactions_dict = {
                "results": [
                    {
                        "reaction": str(getattr(r.reaction, "emoticon", r.reaction)),
                        "count": r.count,
                    }
                    for r in results
                ]
            }

    entities_list: list | None = None
    entities = getattr(msg, "entities", None)
    if entities:
        entities_list = [
            {"type": type(e).__name__, "offset": e.offset, "length": e.length}
            for e in entities
        ]

    return RawMessage(
        tg_msg_id=msg.id,
        date=msg.date,
        edit_date=getattr(msg, "edit_date", None),
        text=text or None,
        entities=entities_list,
        reply_to_msg_id=reply_to_msg_id,
        reply_to_top_id=reply_to_top_id,
        grouped_id=getattr(msg, "grouped_id", None),
        topic_id=_resolve_topic_id(
            reply_to_msg_id, reply_to_top_id, forum_topic=forum_topic
        ),
        fwd_from=fwd_dict,
        views=getattr(msg, "views", None),
        reactions=reactions_dict,
        is_fwd_saved=is_fwd_saved,
        saved_from_peer_id=saved_peer_id,
        saved_from_msg_id=saved_msg_id,
        is_outgoing=bool(getattr(msg, "out", False)),
    )


def _peer_to_dict(peer) -> dict | None:  # noqa: ANN001
    if peer is None:
        return None
    return {
        "type": type(peer).__name__,
        "id": getattr(peer, "channel_id", None)
        or getattr(peer, "user_id", None)
        or getattr(peer, "chat_id", None),
    }
