COMPOSE := docker compose
LIMIT ?= 15
MIN_SCORE ?= 70

.PHONY: setup build build-web init run discover list status doctor cli web demo-web test help

setup:
	@test -f .env || cp .env.example .env
	@mkdir -p data secrets
	@echo "Setup complete. Fill API keys and profile values in .env, then run: make build"

build:
	$(COMPOSE) build nuntago-cli

build-web:
	$(COMPOSE) build nuntago-web

init:
	$(COMPOSE) run --rm nuntago-cli init-db

run:
	$(COMPOSE) run --rm nuntago-cli run --limit $(LIMIT)

discover:
	$(COMPOSE) run --rm nuntago-cli discover --limit $(LIMIT)

list:
	$(COMPOSE) run --rm nuntago-cli list --status drafted --min-score $(MIN_SCORE)

status:
	$(COMPOSE) run --rm nuntago-cli status

doctor:
	$(COMPOSE) run --rm nuntago-cli doctor

cli:
	$(COMPOSE) run --rm nuntago-cli --help

web:
	$(COMPOSE) up nuntago-web

demo-web:
	$(COMPOSE) run --rm --service-ports -e NUNTAGO_DEMO_MODE=true nuntago-web

test:
	$(COMPOSE) run --rm --entrypoint pytest outreach -q

help:
	$(COMPOSE) run --rm nuntago-cli --help
