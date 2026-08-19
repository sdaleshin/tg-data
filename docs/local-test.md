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
make init
# или по шагам: make build && make up && make migrate
```

Проверка Postgres:

```bash
make psql-messages
# или: docker compose exec postgres psql -U tgdata -d tgdata -c '\dt'
```

## 3. Авторизация в Telegram

Интерактивная команда — нужен терминал:

```bash
make auth
```

Введите номер телефона и код из Telegram. Сессия сохранится в томе `tg_session`.

## 4. Добавление тестовых источников

Найти чаты аккаунта:

```bash
make discover
```

Добавить канал по id или @username:

```bash
make sources-add PEER=-1001234567890
make sources-add PEER=@your_test_channel
```

Автодобавить CommentChat для каналов:

```bash
make sources-sync
```

Проверить whitelist:

```bash
make sources-list
```

## 5. Backfill

```bash
make pull-backfill
```

Для длинного backfill можно прервать (`Ctrl+C`) и продолжить позже — checkpoint в `sync_state.oldest_processed_id` сохраняется.

## 6. Проверки приёмки

### Сообщения в БД

```bash
make psql-messages
```

### newest_processed_id зафиксирован

```bash
make psql-sync-state
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
3. Запустите снова: `make pull-resume`
4. Убедитесь, что сообщения не дублируются (`UNIQUE(source_id, tg_msg_id)`)

### Инкремент через scheduler

```bash
make scheduler
```

Опубликуйте новый пост в тестовом канале, подождите `PULL_INTERVAL_SECONDS`, проверьте:

```bash
make stats
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
make test
```

## 8. Остановка

```bash
make down
```

Данные Postgres и сессия Telegram сохраняются в томах `postgres_data` и `tg_session`.

Полная очистка:

```bash
make down-v
```
