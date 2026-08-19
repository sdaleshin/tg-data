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

.env:
	cp .env.example .env
	@echo ".env создан — заполните TELEGRAM_API_ID и TELEGRAM_API_HASH"

env: .env ## Создать .env из .env.example (если ещё нет)
	@echo ".env на месте — нужны TELEGRAM_API_ID и TELEGRAM_API_HASH"

# Профили перечислены явно: docker compose build собирает только сервисы
# активных профилей, поэтому без них образы tg и tests остаются со старым кодом.
build: ## Собрать Docker-образы
	$(COMPOSE) --profile cli --profile tools build

up: .env ## Запустить Postgres
	$(COMPOSE) up -d postgres

down: ## Остановить контейнеры (данные сохраняются)
	$(COMPOSE) down

down-v: ## Остановить и удалить тома (Postgres + сессия Telegram)
	$(COMPOSE) down -v

migrate: up ## Применить миграции Alembic
	$(COMPOSE) run --rm migrate

init: build migrate ## Первый запуск: build + postgres + migrate
	@echo "Готово. Дальше: make auth"

# CLI-цели не зависят от migrate: у сервиса tg в compose есть
# depends_on migrate/service_completed_successfully, и docker compose run сам
# его поднимает. Пререквизит здесь гонял бы alembic дважды на каждую команду.

auth: .env ## Войти в Telegram (интерактивно)
	$(COMPOSE_CLI) auth

discover: .env ## Список чатов аккаунта
	$(COMPOSE_CLI) sources discover

sources-list: .env ## Whitelist источников
	$(COMPOSE_CLI) sources list

sources-sync: .env ## Обновить метаданные + автодобавить CommentChat
	$(COMPOSE_CLI) sources sync

sources-add: .env ## Добавить источник: make sources-add PEER=@channel
	@test -n "$(PEER)" || { echo "Usage: make sources-add PEER=-1001234567890"; exit 1; }
	$(COMPOSE_CLI) sources add $(PEER)

sources-disable: .env ## Деактивировать: make sources-disable ID=1
	@test -n "$(ID)" || { echo "Usage: make sources-disable ID=1"; exit 1; }
	$(COMPOSE_CLI) sources disable $(ID)

sources-enable: .env ## Активировать: make sources-enable ID=1
	@test -n "$(ID)" || { echo "Usage: make sources-enable ID=1"; exit 1; }
	$(COMPOSE_CLI) sources enable $(ID)

sources-purge: .env ## Удалить источник: make sources-purge ID=1
	@test -n "$(ID)" || { echo "Usage: make sources-purge ID=1"; exit 1; }
	$(COMPOSE_CLI) sources purge $(ID) --yes

pull: .env ## Инкрементальный pull
	$(COMPOSE_CLI) pull

pull-backfill: .env ## Backfill до границы архива
	$(COMPOSE_CLI) pull --backfill

pull-resume: .env ## Добрать незавершённые backfill
	$(COMPOSE_CLI) pull --backfill --resume-all

stats: .env ## Статистика архива
	$(COMPOSE_CLI) stats

scheduler: .env ## Запустить фоновый scheduler
	$(COMPOSE) up -d scheduler

scheduler-logs: ## Логи scheduler
	$(COMPOSE) logs -f scheduler

scheduler-stop: ## Остановить scheduler
	$(COMPOSE) stop scheduler

test: up ## Pytest в Docker (БД tgdata_test)
	$(COMPOSE) --profile tools run --rm --build tests

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
