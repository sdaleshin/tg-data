"""Тесты чистых функций reader — без сети и без Telethon."""

from __future__ import annotations

from tg_data.fetch.reader import _resolve_topic_id


def test_plain_reply_is_not_a_topic() -> None:
    assert _resolve_topic_id(42, None, forum_topic=False) is None


def test_comment_thread_reply_is_not_a_topic() -> None:
    assert _resolve_topic_id(101, 100, forum_topic=False) is None


def test_forum_topic_uses_top_id() -> None:
    assert _resolve_topic_id(101, 100, forum_topic=True) == 100


def test_forum_topic_root_uses_reply_to_msg_id() -> None:
    assert _resolve_topic_id(100, None, forum_topic=True) == 100
