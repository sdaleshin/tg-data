# Деплой на VPS

Весь стек работает через Docker Compose — те же команды, что и локально.

## Требования

- Ubuntu 22.04+ (или любой Linux с Docker)
- Docker + Docker Compose v2
- SSH-доступ к серверу

## Установка

```bash
# 1. Клонировать репозиторий
git clone git@github.com:sdaleshin/tg-data.git /opt/tg-data
cd /opt/tg-data

# 2. Создать .env
cp .env.example .env
# Заполнить TELEGRAM_API_ID, TELEGRAM_API_HASH

# 3. Собрать и запустить
docker compose build
docker compose up -d postgres
docker compose run --rm migrate

# 4. Войти в Telegram-аккаунт (интерактивно по SSH)
docker compose --profile cli run --rm tg auth

# 5. Добавить источники
docker compose --profile cli run --rm tg sources add <tg_id>
docker compose --profile cli run --rm tg sources sync

# 6. Первый backfill
docker compose --profile cli run --rm tg pull --backfill

# 7. Запустить scheduler (инкремент каждые 6ч + добор backfill каждый час)
docker compose up -d scheduler
```

## Обновление

```bash
cd /opt/tg-data
git pull
docker compose build
docker compose run --rm migrate
docker compose up -d scheduler
```

## Мониторинг

Отчёты о каждом прогоне приходят в Saved Messages Telegram-аккаунта.
Отсутствие отчёта — сигнал о проблеме.

Логи scheduler:

```bash
docker compose logs -f scheduler
```

## Бэкап Postgres

```bash
docker compose exec postgres pg_dump -U tgdata tgdata > backup.sql
```

Сессия Telethon хранится в томе `tg_session` — при `docker compose down -v` она удалится.
