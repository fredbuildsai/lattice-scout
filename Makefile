.PHONY: dev api worker check test test-integration migrate gen-client eval e2e docs docs-check lint typecheck contracts fe-check db

BACKEND = cd backend && uv run
DB_URL ?= postgresql+psycopg://scout:scout@localhost:5432/scout

db:            ## start PostgreSQL and create dev/test databases
	bash scripts/dev_db.sh

api: db        ## API with reload
	$(BACKEND) uvicorn scout.api.app:create_app --factory --reload --port 8000

worker: db     ## job worker
	$(BACKEND) python -m scout.worker

dev: db migrate  ## api + worker + (frontend if installed)
	@trap 'kill 0' INT TERM; \
	 ($(BACKEND) uvicorn scout.api.app:create_app --factory --reload --port 8000 &) ; \
	 ($(BACKEND) python -m scout.worker &) ; \
	 if [ -d frontend/node_modules ]; then cd frontend && pnpm dev; else wait; fi

lint:
	$(BACKEND) ruff check src tests && $(BACKEND) ruff format --check src tests

typecheck:
	$(BACKEND) mypy src tests

contracts:
	$(BACKEND) lint-imports

test:          ## unit + contract tests (no database)
	$(BACKEND) pytest -m "not integration and not needs_network and not needs_supabase" -q

test-integration: db  ## tests that need PostgreSQL
	$(BACKEND) pytest -m "integration" -q

migrate:       ## apply Alembic migrations to the dev database
	$(BACKEND) alembic upgrade head

gen-client:    ## regenerate the TypeScript API client from the OpenAPI schema
	$(BACKEND) python -m scout.api.export_openapi ../frontend/openapi.json
	cd frontend && pnpm gen-client

fe-check:
	cd frontend && pnpm lint && pnpm typecheck && pnpm test && pnpm build

docs-check:
	$(BACKEND) interrogate -c pyproject.toml src/scout && python3 scripts/check_docs.py

docs:
	cd docs-site && uv run --project ../backend mkdocs build --strict

eval:          ## offline eval (fixtures); live eval is a manual workflow
	@echo "eval harness arrives in M3"

e2e:           ## Playwright smoke test
	cd frontend && pnpm e2e

check: lint typecheck contracts docs-check test-integration test fe-check  ## everything CI runs
