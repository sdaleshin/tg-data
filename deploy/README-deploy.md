# Деплой на VPS

## Требования

- Ubuntu 22.04+
- Docker + docker-compose (для Postgres)
- Python 3.12 + uv

## Установка

```bash
# 1. Клонировать репозиторий
git clone ... /opt/tg-data
cd /opt/tg-data

# 2. Создать .env
cp .env.example .env
# Заполнить TELEGRAM_API_ID, TELEGRAM_API_HASH

# 3. Запустить Postgres
docker compose up -d

# 4. Создать виртуальное окружение и установить зависимости
uv sync

# 5. Применить миграции
uv run alembic upgrade head

# 6. Войти в Telegram-аккаунт (интерактивно)
uv run tg auth

# 7. Добавить источники
uv run tg sources add <tg_id>
uv run tg sources sync  # автодобавит comment_chat

# 8. Запустить первый backfill вручную (или через systemd)
uv run tg pull --backfill
```

## Systemd timers

```bash
# Скопировать unit-файлы
sudo cp deploy/tg-pull.service /etc/systemd/system/
sudo cp deploy/tg-pull.timer /etc/systemd/system/
sudo cp deploy/tg-backfill-resume.service /etc/systemd/system/
sudo cp deploy/tg-backfill-resume.timer /etc/systemd/system/

# Включить таймеры
sudo systemctl daemon-reload
sudo systemctl enable --now tg-pull.timer
sudo systemctl enable --now tg-backfill-resume.timer
```

Отчёты о каждом прогоне приходят в Saved Messages Telegram-аккаунта.
Отсутствие отчёта — сигнал о проблеме.
