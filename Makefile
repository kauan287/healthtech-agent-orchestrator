.PHONY: help install dev test test-postgres eval-demo eval-groq lint format typecheck check e2e up down

help:  ## List targets
	@grep -E '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*## "}; {printf "  %-10s %s\n", $$1, $$2}'

install:  ## Install dependencies, including dev tools
	uv sync

dev:  ## Run the API with reload (uses .env, SQLite by default)
	uv run uvicorn src.server:app --reload

test:  ## Run the test suite with coverage
	uv run pytest --cov=src --cov-report=term-missing

lint:  ## Lint and check formatting
	uv run ruff check src tests scripts
	uv run ruff format --check src tests scripts

format:  ## Format code
	uv run ruff format src tests scripts

typecheck:  ## Type-check
	uv run mypy src tests scripts

test-postgres:  ## Run the test suite on PostgreSQL. Set TEST_POSTGRES_URL to a server you can create databases on
	uv run pytest -q

check: lint typecheck test  ## Everything CI runs before Docker

up:  ## Start Postgres and the API with Docker Compose
	docker compose up --build

down:  ## Stop Docker Compose and delete its volume
	docker compose down -v

eval-demo:  ## Evaluate the demo model on evals/cases.jsonl and rewrite evals/REPORT.md
	uv run python -m evals.run --models demo
	uv run python -m evals.report

eval-groq:  ## Evaluate both Groq models too. Needs GROQ_API_KEY
	uv run python -m evals.run --models groq:openai/gpt-oss-120b,groq:openai/gpt-oss-20b
	uv run python -m evals.report

e2e:  ## Run the end-to-end script against the Compose stack, with a restart
	python3 scripts/e2e.py http://localhost:8000 --restart-cmd "docker compose restart api"
