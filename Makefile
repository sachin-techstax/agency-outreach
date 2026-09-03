COMPOSE := docker compose
LIMIT ?= 15
MIN_SCORE ?= 70

.PHONY: setup build build-web init run discover list status doctor cli web demo-web test help

setup:
	@test -f .env || cp .env.example .env
	@mkdir -p data secrets
	@echo "Setup complete. Fill API keys and profile values in .env, then run: make build"

build:
	$(COMPOSE) build outreach

build-web:
	$(COMPOSE) build pactsignal-web

init:
	$(COMPOSE) run --rm outreach init-db

run:
	$(COMPOSE) run --rm outreach run --limit $(LIMIT)

discover:
	$(COMPOSE) run --rm outreach discover --limit $(LIMIT)

list:
	$(COMPOSE) run --rm outreach list --status drafted --min-score $(MIN_SCORE)

status:
	$(COMPOSE) run --rm outreach status

doctor:
	$(COMPOSE) run --rm outreach doctor

cli:
	$(COMPOSE) run --rm outreach --help

web:
	$(COMPOSE) up pactsignal-web

demo-web:
	$(COMPOSE) run --rm --service-ports -e PACTSIGNAL_DEMO_MODE=true pactsignal-web

test:
	$(COMPOSE) run --rm --entrypoint pytest outreach -q

help:
	$(COMPOSE) run --rm outreach --help
