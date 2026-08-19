.DEFAULT_GOAL := help

COMPOSE        := docker compose
COMPOSE_CLI    := $(COMPOSE) --profile cli run --rm tg
PSQL           := $(COMPOSE) exec postgres psql -U tgdata -d tgdata

.PHONY: help env build up down down-v init migrate auth \
        discover sources-list sources-sync sources-add sources-disable sources-enable sources-purge \
        pull pull-backfill pull-resume stats scheduler scheduler-logs scheduler-stop \
        test test-local psql psql-messages psql-sync-state logs clean

help: ## Показать эту справку
	@grep -E '^[a-zA-Z0-9_-]+:.*##' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*## "}; {printf "  \033[36m%-22s\033[0m %s\n", $$1, $$2}'

env: ## Создать .env из .env.example (если ещё нет)
	@test -f .env || cp .env.example .env
	@echo ".env готов — заполните TELEGRAM_API_ID и TELEGRAM_API_HASH"

build: ## Собрать Docker-образы
	$(COMPOSE) build

up: env ## Запустить Postgres
	$(COMPOSE) up -d postgres

down: ## Остановить контейнеры (данные сохраняются)
	$(COMPOSE) down

down-v: ## Остановить и удалить тома (Postgres + сессия Telegram)
	$(COMPOSE) down -v

migrate: up ## Применить миграции Alembic
	$(COMPOSE) run --rm migrate

init: build migrate ## Первый запуск: build + postgres + migrate
	@echo "Готово. Дальше: make auth"

auth: migrate ## Войти в Telegram (интерактивно)
	$(COMPOSE_CLI) auth

discover: migrate ## Список чатов аккаунта
	$(COMPOSE_CLI) sources discover

sources-list: migrate ## Whitelist источников
	$(COMPOSE_CLI) sources list

sources-sync: migrate ## Обновить метаданные + автодобавить CommentChat
	$(COMPOSE_CLI) sources sync

sources-add: migrate ## Добавить источник: make sources-add PEER=@channel
	@test -n "$(PEER)" || { echo "Usage: make sources-add PEER=-1001234567890"; exit 1; }
	$(COMPOSE_CLI) sources add $(PEER)

sources-disable: migrate ## Деактивировать: make sources-disable ID=1
	@test -n "$(ID)" || { echo "Usage: make sources-disable ID=1"; exit 1; }
	$(COMPOSE_CLI) sources disable $(ID)

sources-enable: migrate ## Активировать: make sources-enable ID=1
	@test -n "$(ID)" || { echo "Usage: make sources-enable ID=1"; exit 1; }
	$(COMPOSE_CLI) sources enable $(ID)

sources-purge: migrate ## Удалить источник: make sources-purge ID=1
	@test -n "$(ID)" || { echo "Usage: make sources-purge ID=1"; exit 1; }
	$(COMPOSE_CLI) sources purge $(ID) --yes

pull: migrate ## Инкрементальный pull
	$(COMPOSE_CLI) pull

pull-backfill: migrate ## Backfill до границы архива
	$(COMPOSE_CLI) pull --backfill

pull-resume: migrate ## Добрать незавершённые backfill
	$(COMPOSE_CLI) pull --backfill --resume-all

stats: migrate ## Статистика архива
	$(COMPOSE_CLI) stats

scheduler: migrate ## Запустить фоновый scheduler
	$(COMPOSE) up -d scheduler

scheduler-logs: ## Логи scheduler
	$(COMPOSE) logs -f scheduler

scheduler-stop: ## Остановить scheduler
	$(COMPOSE) stop scheduler

test: up ## Pytest в Docker (БД tgdata_test)
	$(COMPOSE) --profile tools run --rm tests

test-local: ## Pytest локально (uv, Postgres на :5433)
	uv run pytest -v

psql: up ## psql в боевую БД
	$(PSQL)

psql-messages: up ## Количество сообщений в архиве
	$(PSQL) -c "SELECT count(*) AS messages FROM messages;"

psql-sync-state: up ## Состояние backfill по источникам
	$(PSQL) -c "SELECT source_id, newest_processed_id, oldest_processed_id, backfill_done FROM sync_state;"

logs: ## Логи всех сервисов
	$(COMPOSE) logs -f

clean: down ## alias для down
