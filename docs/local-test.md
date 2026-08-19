# Локальный тест tg-data в Docker

Пошаговый сценарий прогона на тестовых Telegram-каналах перед деплоем на VPS.

## Предварительные требования

- Docker и Docker Compose
- Telegram API credentials с [my.telegram.org](https://my.telegram.org)
- Аккаунт уже состоит в тестовых каналах/группах (вступление — вручную в Telegram)

## 1. Настройка окружения

```bash
cp .env.example .env
```

Заполните в `.env`:

```env
TELEGRAM_API_ID=123456
TELEGRAM_API_HASH=your_hash_here

# Для быстрого теста — короткая граница архива
ARCHIVE_SINCE=2025-07-01

# Ускоренные интервалы scheduler (минуты, не часы)
REQUEST_PAUSE=0.2
PULL_INTERVAL_SECONDS=300
BACKFILL_RESUME_INTERVAL_SECONDS=120
```

## 2. Сборка и запуск инфраструктуры

```bash
docker compose build
docker compose up -d postgres
docker compose run --rm migrate
```

Проверка Postgres:

```bash
docker compose exec postgres psql -U tgdata -d tgdata -c '\dt'
```

## 3. Авторизация в Telegram

Интерактивная команда — нужен терминал:

```bash
docker compose --profile cli run --rm tg auth
```

Введите номер телефона и код из Telegram. Сессия сохранится в томе `tg_session`.

## 4. Добавление тестовых источников

Найти чаты аккаунта:

```bash
docker compose --profile cli run --rm tg sources discover
```

Добавить канал по id или @username:

```bash
docker compose --profile cli run --rm tg sources add -1001234567890
docker compose --profile cli run --rm tg sources add @your_test_channel
```

Автодобавить CommentChat для каналов:

```bash
docker compose --profile cli run --rm tg sources sync
```

Проверить whitelist:

```bash
docker compose --profile cli run --rm tg sources list
```

## 5. Backfill

```bash
docker compose --profile cli run --rm tg pull --backfill
```

Для длинного backfill можно прервать (`Ctrl+C`) и продолжить позже — checkpoint в `sync_state.oldest_processed_id` сохраняется.

## 6. Проверки приёмки

### Сообщения в БД

```bash
docker compose exec postgres psql -U tgdata -d tgdata \
  -c "SELECT count(*) FROM messages;"
```

### newest_processed_id зафиксирован

```bash
docker compose exec postgres psql -U tgdata -d tgdata \
  -c "SELECT source_id, newest_processed_id, backfill_done FROM sync_state;"
```

`newest_processed_id` не должен быть NULL или 0 после backfill канала с сообщениями.

### ThreadRoot для канала с комментариями

Если тестовый канал имеет linked discussion group:

```bash
docker compose exec postgres psql -U tgdata -d tgdata \
  -c "SELECT count(*) FROM thread_roots;"
```

Автокопии постов не должны дублироваться в `messages` — только в `thread_roots`.

### Продолжение после обрыва

1. Запустите backfill, прервите на середине
2. Проверьте `oldest_processed_id` в `sync_state`
3. Запустите снова: `docker compose --profile cli run --rm tg pull --backfill --resume-all`
4. Убедитесь, что сообщения не дублируются (`UNIQUE(source_id, tg_msg_id)`)

### Инкремент через scheduler

```bash
docker compose up -d scheduler
```

Опубликуйте новый пост в тестовом канале, подождите `PULL_INTERVAL_SECONDS`, проверьте:

```bash
docker compose --profile cli run --rm tg stats
```

### Heartbeat в Saved Messages

После каждого прогона `pull` или scheduler в «Избранное» (Saved Messages) должен прийти отчёт вида:

```
✅ tg-data pull
Новых сообщений: N
Источников: M
```

## 7. Автотесты

```bash
docker compose --profile tools run --rm tests
```

## 8. Остановка

```bash
docker compose down
```

Данные Postgres и сессия Telegram сохраняются в томах `postgres_data` и `tg_session`.

Полная очистка:

```bash
docker compose down -v
```
