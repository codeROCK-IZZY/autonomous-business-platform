# Common developer commands. Run `make help`.
.PHONY: help up down demo logs test test-backend test-frontend lint evaluate

help:
	@grep -E '^[a-z-]+:.*##' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  %-15s %s\n", $$1, $$2}'

up: ## Build and start the full stack (http://localhost:8080)
	docker compose up -d --build

down: ## Stop the stack (keeps data volumes)
	docker compose down

demo: ## Load demo business data, users olivia (operator), aaron (approver), admin, and sample runs
	docker compose run --rm api python -m app.cli demo

logs: ## Follow API and worker logs
	docker compose logs -f api worker

test: test-backend test-frontend ## Run all tests

test-backend: ## Backend tests (needs PostgreSQL; see docs/DEVELOPMENT.md)
	cd backend && pytest

test-frontend: ## Frontend unit tests
	cd frontend && npm test

lint: ## Lint and type-check everything
	cd backend && ruff check app tests migrations && ruff format --check app tests migrations && mypy app
	cd frontend && npm run lint && npm run typecheck


evaluate: ## Run every labelled case (DELETES business data in the configured database) and regenerate eval/reports/
	cd backend && python -m app.cli evaluate --reset --out ../eval/reports
