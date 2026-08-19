# tg-data

Личный архив текста из Telegram → Postgres.

Выкачивает сообщения из whitelist-каналов, групп, их чатов комментариев и личных чатов
(включая Избранное). Хранит только текстовое содержимое (text + caption).
Граница архива: 2024-01-01.

## Стек

- Python 3.12 + uv
- Telethon 1.44 (личный аккаунт)
- Postgres 17 (Docker)
- SQLAlchemy 2.0 + Alembic, Typer, pydantic-settings

## Быстрый старт (Docker)

```bash
make env          # создать .env из .env.example
# заполнить TELEGRAM_API_ID, TELEGRAM_API_HASH

make init         # build + postgres + migrate
make auth         # войти в Telegram
make discover     # найти чаты
make sources-add PEER=@your_channel
make sources-sync
make pull-backfill
make scheduler    # фоновый инкремент + добор backfill
make stats
```

Все команды: `make help`. После правок кода — `make build` (пересобирает и образы
профилей `cli`/`tools`, которые `docker compose build` сам по себе пропускает).

<details>
<summary>Эквивалент через docker compose</summary>

```bash
# Настроить окружение
cp .env.example .env
# Заполнить TELEGRAM_API_ID, TELEGRAM_API_HASH

# Собрать и запустить
docker compose build
docker compose up -d postgres
docker compose run --rm migrate

# Войти в аккаунт (интерактивно)
docker compose --profile cli run --rm tg auth

# Добавить источники
docker compose --profile cli run --rm tg sources discover
docker compose --profile cli run --rm tg sources add @channel_or_id
docker compose --profile cli run --rm tg sources sync

# Выкачать историю
docker compose --profile cli run --rm tg pull --backfill

# Запустить фоновый scheduler (инкремент + добор backfill)
docker compose up -d scheduler

# Статистика
docker compose --profile cli run --rm tg stats
```

</details>

Подробный сценарий локального теста: [docs/local-test.md](docs/local-test.md).

## CLI

| Команда | Описание |
|---------|----------|
| `tg auth` | Войти в Telegram |
| `tg sources add <id\|@username>` | Добавить Source |
| `tg sources disable <id>` | Деактивировать |
| `tg sources enable <id>` | Активировать |
| `tg sources list` | Список источников |
| `tg sources purge <id>` | Удалить Source с данными |
| `tg sources discover` | Найти чаты аккаунта |
| `tg sources sync` | Обновить метаданные |
| `tg pull` | Инкрементальный pull |
| `tg pull --backfill` | Backfill до границы архива |
| `tg pull --backfill --resume-all` | Добрать незавершённые backfill |
| `tg scheduler` | Фоновый цикл pull + backfill |
| `tg stats` | Статистика |

## Разработка без Docker

```bash
uv sync
docker compose up -d postgres   # только БД
uv run alembic upgrade head
uv run tg auth
uv run pytest
```

## Деплой

См. [deploy/README-deploy.md](deploy/README-deploy.md).
