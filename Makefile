COMPOSE := docker compose
LIMIT ?= 15
MIN_SCORE ?= 70

.PHONY: setup build init run list test help

setup:
	@test -f .env || cp .env.example .env
	@mkdir -p data secrets
	@echo "Setup complete. Fill API keys and profile values in .env, then run: make build"

build:
	$(COMPOSE) build

init:
	$(COMPOSE) run --rm outreach init-db

run:
	$(COMPOSE) run --rm outreach run --limit $(LIMIT)

list:
	$(COMPOSE) run --rm outreach list --status drafted --min-score $(MIN_SCORE)

test:
	$(COMPOSE) run --rm --entrypoint pytest outreach -q

help:
	$(COMPOSE) run --rm outreach --help
