# tg-data

Личный архив текста из Telegram → Postgres.

Выкачивает сообщения из whitelist-каналов, групп и их чатов комментариев.
Хранит только текстовое содержимое (text + caption). Граница архива: 2024-01-01.

## Стек

- Python 3.12 + uv
- Telethon 1.44 (личный аккаунт)
- Postgres 17 (docker-compose)
- SQLAlchemy 2.0 + Alembic, Typer, pydantic-settings

## Быстрый старт

```bash
# Установить зависимости
uv sync

# Запустить Postgres
docker compose up -d

# Применить миграции
uv run alembic upgrade head

# Войти в аккаунт
uv run tg auth

# Добавить источники
uv run tg sources discover       # найти чаты
uv run tg sources add <tg_id>    # добавить по id
uv run tg sources sync           # обновить метаданные + автодобавить comment_chat

# Выкачать историю
uv run tg pull --backfill        # backfill до границы архива

# Инкремент
uv run tg pull                   # только новые сообщения

# Статистика
uv run tg stats
```

## CLI

| Команда | Описание |
|---------|----------|
| `tg auth` | Войти в Telegram |
| `tg sources add <id>` | Добавить Source |
| `tg sources disable <id>` | Деактивировать |
| `tg sources enable <id>` | Активировать |
| `tg sources list` | Список источников |
| `tg sources purge <id>` | Удалить Source с данными |
| `tg sources discover` | Найти чаты аккаунта |
| `tg sources sync` | Обновить метаданные |
| `tg pull` | Инкрементальный pull |
| `tg pull --backfill` | Backfill до границы архива |
| `tg pull --backfill --resume-all` | Добрать незавершённые backfill |
| `tg stats` | Статистика |

## Деплой

См. [deploy/README-deploy.md](deploy/README-deploy.md).
