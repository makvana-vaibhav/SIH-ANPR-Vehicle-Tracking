# ══════════════════════════════════════════════════════════════════════
#  Sentinel-GJ — statewide CCTV intelligence platform
#
#  A judge needs exactly three commands:
#      make up      → bring the platform online
#      make demo    → seed everything and open the command centre
#      make down    → stop
#
#  `make help` lists the rest.
#
#  Portability note: macOS ships GNU Make 3.81, which has no .ONESHELL.
#  Every recipe below is therefore written so each logical block is a single
#  shell invocation (continued with `; \`). Do not introduce bare multi-line
#  if/while blocks — they break on a judge's stock macOS toolchain.
# ══════════════════════════════════════════════════════════════════════

SHELL := /bin/bash
.DEFAULT_GOAL := help

COMPOSE          := docker compose
COMPOSE_AI       := docker compose -f docker-compose.yml -f docker-compose.ai.yml
COMPOSE_SCALE    := docker compose -f docker-compose.yml -f docker-compose.scale.yml
WEB_PORT         ?= 8080
API_PORT         ?= 8000
WEB_URL          := http://localhost:$(WEB_PORT)
API_URL          := http://localhost:$(API_PORT)
HEALTH_TIMEOUT   ?= 300

.PHONY: help
help: ## Show this help
	@printf "\033[1mSentinel-GJ\033[0m — statewide CCTV intelligence platform\n\n"
	@printf "\033[1mQuick start for a judge:\033[0m\n"
	@printf "  make up      bring the platform online\n"
	@printf "  make demo    seed data and open the command centre\n"
	@printf "  make down    stop everything\n\n"
	@printf "\033[1mAll targets:\033[0m\n"
	@grep -hE '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | \
	  awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[1m%-14s\033[0m %s\n", $$1, $$2}'

# ── Environment ───────────────────────────────────────────────────────

.PHONY: env
env: ## Create .env from .env.example if absent
	@if [ -f .env ]; then \
	  printf "\033[2m.env already exists — leaving it alone\033[0m\n"; \
	else \
	  cp .env.example .env; \
	  printf "\033[32mcreated .env from .env.example\033[0m\n"; \
	  printf "\033[2mDevelopment defaults are fine for the demo.\033[0m\n"; \
	fi

.PHONY: preflight
preflight: ## Check Docker is running and has enough memory
	@if ! docker info >/dev/null 2>&1; then \
	  printf "\033[31m✗ Docker daemon is not running.\033[0m\n"; \
	  printf "  Start Docker Desktop (or 'colima start') and retry.\n"; \
	  exit 1; \
	fi; \
	printf "\033[32m✓\033[0m Docker daemon reachable\n"; \
	mem=$$(docker info --format '{{.MemTotal}}' 2>/dev/null || echo 0); \
	if [ "$$mem" -lt 7000000000 ]; then \
	  printf "\033[33m!\033[0m Docker has <8GB RAM allocated (OpenSearch + Postgres may OOM).\n"; \
	  printf "  Raise it in Docker Desktop → Settings → Resources.\n"; \
	else \
	  printf "\033[32m✓\033[0m Docker memory allocation sufficient\n"; \
	fi

# ── Lifecycle ─────────────────────────────────────────────────────────

.PHONY: up
up: env preflight ## Build and start the platform, wait for healthy
	@printf "\033[1mStarting Sentinel-GJ\033[0m\n"
	@$(COMPOSE) up -d --build --remove-orphans
	@$(MAKE) --no-print-directory wait-healthy
	@printf "\n\033[1;32mPlatform online\033[0m\n"
	@printf "  Command centre  $(WEB_URL)\n"
	@printf "  API docs        $(API_URL)/docs\n"
	@printf "  Readiness       $(API_URL)/ready\n"

.PHONY: wait-healthy
wait-healthy: ## Block until every container reports healthy
	@printf "\033[2mwaiting for containers to become healthy…\033[0m\n"; \
	deadline=$$(( $$(date +%s) + $(HEALTH_TIMEOUT) )); \
	while true; do \
	  pending=$$($(COMPOSE) ps --format '{{.Service}} {{.State}} {{.Health}}' 2>/dev/null \
	    | awk '$$2 == "running" && $$3 != "healthy" && $$3 != "" { print $$1 }'); \
	  starting=$$($(COMPOSE) ps --format '{{.Service}} {{.State}}' 2>/dev/null \
	    | awk '$$2 == "restarting" || $$2 == "created" { print $$1 }'); \
	  if [ -z "$$pending" ] && [ -z "$$starting" ]; then \
	    printf "\033[32m✓\033[0m all containers healthy\n"; \
	    break; \
	  fi; \
	  if [ $$(date +%s) -gt $$deadline ]; then \
	    printf "\033[31m✗ timed out waiting for:\033[0m $$pending $$starting\n"; \
	    $(COMPOSE) ps; \
	    exit 1; \
	  fi; \
	  sleep 3; \
	done

.PHONY: down
down: ## Stop the platform (data volumes are kept)
	@$(COMPOSE) down --remove-orphans
	@printf "\033[32mstopped\033[0m — data volumes preserved (make clean removes them)\n"

.PHONY: restart
restart: down up ## Restart the platform

.PHONY: clean
clean: ## Stop and DELETE all data volumes
	@printf "\033[33mThis deletes the database, search index, and stored media.\033[0m\n"
	@$(COMPOSE) down -v --remove-orphans
	@printf "\033[32mcleaned\033[0m\n"

.PHONY: ps
ps: ## Show container status
	@$(COMPOSE) ps

.PHONY: status
status: ## Show platform readiness (which dependencies are up)
	@curl -fsS $(API_URL)/ready 2>/dev/null | python3 -m json.tool || \
	  printf "\033[31mAPI unreachable — is the platform up? (make up)\033[0m\n"

.PHONY: logs
logs: ## Tail logs (make logs S=api for one service)
	@if [ -n "$(S)" ]; then \
	  $(COMPOSE) logs -f --tail=200 $(S); \
	else \
	  $(COMPOSE) logs -f --tail=100; \
	fi

# ── Data ──────────────────────────────────────────────────────────────

.PHONY: migrate
migrate: ## Apply database migrations
	@$(COMPOSE) exec -T -w /app/services/api api alembic upgrade head

.PHONY: seed
seed: ## Seed cameras, watchlist, and users
	@$(COMPOSE) exec -T api python -m scripts.seed

.PHONY: demo
demo: ## Full judge demo: fresh data, fleet, worker, verified
	@./scripts/demo_up.sh
	@command -v open >/dev/null 2>&1 && open "$(WEB_URL)" || true

.PHONY: models
models: ## Download and export AI model weights to ONNX
	@./scripts/fetch_models.sh

.PHONY: videos
videos: ## Download sample traffic clips
	@./scripts/fetch_videos.sh

.PHONY: tiles
tiles: ## (Optional) Fetch Gujarat MBTiles for a street basemap
	@./scripts/fetch_tiles.sh

.PHONY: contracts
contracts: ## Regenerate Pydantic + TypeScript types from JSON Schema
	@$(COMPOSE) exec -T api python -m scripts.generate_contracts

# ── Quality ───────────────────────────────────────────────────────────

.PHONY: test
test: ## Run backend and frontend test suites
	@printf "\033[1mAPI tests\033[0m\n"
	@$(COMPOSE) exec -T -w /app/services/api api python -m pytest tests -q
	@printf "\n\033[1mAI worker tests\033[0m\n"
	@$(COMPOSE) exec -T -e PYTHONPATH=/app/services/ai-worker api \
		python -m pytest /app/services/ai-worker/tests -q
	@printf "\n\033[1mEnd-to-end: the five judge moments\033[0m\n"
	@# Run from the suite's own directory so tests/e2e/pytest.ini applies —
	@# without it async fixtures are never awaited and every test fails.
	@$(COMPOSE) exec -T -w /app/tests/e2e -e WEB_URL=http://web api \
		python -m pytest . -q
	@printf "\n\033[1mFrontend tests\033[0m\n"
	@cd web && npm run test

.PHONY: docs
docs: ## Regenerate docs/API.md from the running API
	@python3 scripts/generate_api_docs.py

.PHONY: lint
lint: ## Lint and type-check everything
	@printf "\033[1mPython\033[0m\n"
	@$(COMPOSE) exec -T -w /app/services/api api ruff check .
	@$(COMPOSE) exec -T -w /app/services/api api ruff format --check .
	@$(COMPOSE) exec -T -w /app -e RUFF_CACHE_DIR=/tmp/ruff api ruff check services/ai-worker services/simulator scripts
	@$(COMPOSE) exec -T -w /app -e RUFF_CACHE_DIR=/tmp/ruff api ruff format --check services/ai-worker services/simulator scripts
	@printf "\n\033[1mTypeScript\033[0m\n"
	@cd web && npm run typecheck

.PHONY: format
format: ## Auto-format Python sources
	@$(COMPOSE) exec -T -w /app/services/api api ruff format .
	@$(COMPOSE) exec -T -w /app/services/api api ruff check --fix .
	@$(COMPOSE) exec -T -w /app -e RUFF_CACHE_DIR=/tmp/ruff api ruff format services/ai-worker services/simulator scripts
	@$(COMPOSE) exec -T -w /app -e RUFF_CACHE_DIR=/tmp/ruff api ruff check --fix services/ai-worker services/simulator scripts

# ── Profiles ──────────────────────────────────────────────────────────

.PHONY: ai
ai: ## Start with the AI worker profile
	@$(COMPOSE_AI) up -d --build
	@$(MAKE) --no-print-directory wait-healthy

.PHONY: scale
scale: ## Start the scale profile (Redpanda, replicas, Grafana)
	@$(COMPOSE_SCALE) up -d --build
	@printf "\033[32mscale profile up\033[0m — Grafana http://localhost:3000\n"

.PHONY: load
load: ## Run the 80,000-camera load test
	@$(COMPOSE_SCALE) exec -T simulator python -m app.load_mode
	@cd tests/load/k6 && k6 run api_load.js

# ── Development ───────────────────────────────────────────────────────

.PHONY: web-dev
web-dev: ## Run the web tier with hot reload on :5173
	@cd web && npm install && npm run dev

.PHONY: shell-api
shell-api: ## Open a shell in the API container
	@$(COMPOSE) exec api /bin/bash

.PHONY: shell-db
shell-db: ## Open psql in the database container
	@$(COMPOSE) exec postgres psql -U $${POSTGRES_USER:-sentinel} -d $${POSTGRES_DB:-sentinel}

.PHONY: config
config: ## Validate the compose configuration
	@$(COMPOSE) config --quiet && printf "\033[32m✓\033[0m docker-compose.yml is valid\n"
