# Decision: Docker как единственный способ деплоя

## Status

Accepted

## Context

Изначальный plan.md предполагал Postgres в docker-compose, а приложение — uv-окружение на хосте с systemd timers для инкремента и добора backfill.

Для локального тестирования на реальных каналах нужен воспроизводимый стек без установки Python/uv на машину. Тот же стек должен работать на VPS без дублирования инструкций.

## Decision

- Весь стек (Postgres + миграции + CLI + scheduler) запускается через `docker compose`.
- Systemd unit-файлы удалены; их роль выполняет контейнер `scheduler` с командой `tg scheduler`.
- Сессия Telethon хранится в именованном томе `tg_session`, не в репозитории.
- На VPS: `git clone` → `.env` → `docker compose up -d`.

## Consequences

- Один набор инструкций для локали и продакшена.
- Интерактивные команды (`tg auth`, `tg sources discover`) выполняются через `docker compose --profile cli run --rm tg ...`.
- Advisory lock по-прежнему гарантирует один процесс с Telegram; scheduler и разовый `tg pull` не конфликтуют благодаря `pg_try_advisory_lock`.
