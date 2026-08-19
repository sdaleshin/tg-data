"""Heartbeat-отчёты в Saved Messages: и текст, и его распознавание.

Отчёты уходят в чат, который сам может быть Source, поэтому построение текста
и его узнавание при ingest живут в одном модуле. Разнеси их — и архив начнёт
вычитывать собственные отчёты обратно, наращивая себя каждым циклом.
"""

from __future__ import annotations

# Хвостовая строка каждого отчёта. Меняя её, придётся считаться с тем, что
# отчёты, отправленные старой версией, перестанут распознаваться и попадут в
# архив при следующем backfill.
MARKER = "#tg_data_report"

# Отчёты, отправленные до появления маркера: первый backfill по Избранному
# иначе втянул бы всю их историю как обычные заметки. Сверяется целая первая
# строка, чтобы не поймать заметку, которая просто начинается похоже.
_LEGACY_FIRST_LINES = ("✅ tg-data pull\n", "❌ tg-data error\n")


def pull_report(total: int, sources_count: int) -> str:
    return (
        "✅ tg-data pull\n"
        f"Новых сообщений: {total}\n"
        f"Источников: {sources_count}\n"
        f"{MARKER}"
    )


def error_report(error: str) -> str:
    return f"❌ tg-data error\n{error}\n{MARKER}"


def is_report(text: str | None) -> bool:
    """Наш ли это отчёт. Проверять только вместе с «сообщение исходящее»."""
    if not text:
        return False
    return MARKER in text or text.startswith(_LEGACY_FIRST_LINES)
