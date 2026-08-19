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

    async def get_chat_info(self, tg_id: int) -> dict: ...


class TelegramReader:
    """Реальная реализация TelegramReaderPort поверх Telethon."""

    FLOOD_WAIT_EXTRA = 5
    MESSAGE_PAUSE = 1.0

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
        from telethon.tl.types import (
            MessageFwdHeader,
            MessageEntityMention,
            PeerChannel,
        )

        kwargs: dict = {"reverse": False}
        if min_id:
            kwargs["min_id"] = min_id
        if max_id:
            kwargs["max_id"] = max_id
        if limit is not None:
            kwargs["limit"] = limit

        while True:
            try:
                async for msg in self._client.iter_messages(tg_id, **kwargs):
                    if not hasattr(msg, "id"):
                        continue

                    text = getattr(msg, "message", None) or getattr(
                        msg, "caption", None
                    )

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
                            "from_id": _peer_to_dict(
                                getattr(fwd, "from_id", None)
                            ),
                            "channel_post": getattr(fwd, "channel_post", None),
                            "saved_from_peer": _peer_to_dict(saved_peer),
                            "saved_from_msg_id": saved_msg_id,
                        }

                    reply = getattr(msg, "reply_to", None)
                    reply_to_msg_id: int | None = None
                    reply_to_top_id: int | None = None
                    if reply is not None:
                        reply_to_msg_id = getattr(reply, "reply_to_msg_id", None)
                        reply_to_top_id = getattr(reply, "reply_to_top_id", None)

                    reactions_dict: dict | None = None
                    reactions = getattr(msg, "reactions", None)
                    if reactions is not None:
                        results = getattr(reactions, "results", None)
                        if results:
                            reactions_dict = {
                                "results": [
                                    {
                                        "reaction": str(
                                            getattr(r.reaction, "emoticon", r.reaction)
                                        ),
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

                    topic_id: int | None = None
                    if reply_to_top_id is not None:
                        topic_id = reply_to_top_id
                    elif getattr(msg, "reply_to", None) is not None:
                        topic_id = getattr(msg.reply_to, "forum_topic", None)

                    yield RawMessage(
                        tg_msg_id=msg.id,
                        date=msg.date,
                        edit_date=getattr(msg, "edit_date", None),
                        text=text or None,
                        entities=entities_list,
                        reply_to_msg_id=reply_to_msg_id,
                        reply_to_top_id=reply_to_top_id,
                        grouped_id=getattr(msg, "grouped_id", None),
                        topic_id=topic_id,
                        fwd_from=fwd_dict,
                        views=getattr(msg, "views", None),
                        reactions=reactions_dict,
                        is_fwd_saved=is_fwd_saved,
                        saved_from_peer_id=saved_peer_id,
                        saved_from_msg_id=saved_msg_id,
                    )
                    await asyncio.sleep(self.MESSAGE_PAUSE)
                break
            except FloodWaitError as e:
                wait = e.seconds + self.FLOOD_WAIT_EXTRA
                logger.warning("FloodWait %s секунд", wait)
                await asyncio.sleep(wait)

    async def get_chat_info(self, tg_id: int) -> dict:
        from telethon.tl.functions.channels import GetFullChannelRequest
        from telethon.tl.types import Channel, Chat

        entity = await self._client.get_entity(tg_id)
        linked_chat_id: int | None = None

        if isinstance(entity, Channel) and entity.broadcast:
            try:
                full = await self._client(GetFullChannelRequest(entity))
                linked_chat_id = getattr(full.full_chat, "linked_chat_id", None)
            except Exception:
                pass

        return {
            "tg_id": entity.id,
            "username": getattr(entity, "username", None),
            "title": getattr(entity, "title", None),
            "is_channel": isinstance(entity, Channel) and entity.broadcast,
            "linked_chat_id": linked_chat_id,
        }


def _peer_to_dict(peer) -> dict | None:
    if peer is None:
        return None
    return {"type": type(peer).__name__, "id": getattr(peer, "channel_id", None) or getattr(peer, "user_id", None) or getattr(peer, "chat_id", None)}
