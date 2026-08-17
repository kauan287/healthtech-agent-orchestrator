# ── Makefile ─────────────────────────────────────────────────────────────────
# Convenience targets for local development.
# Run `make help` to see all available commands.

.PHONY: help install dev test test-unit test-integration lint format type-check up down clean

help:  ## Show this help message
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-20s\033[0m %s\n", $$1, $$2}'

install:  ## Install all dependencies (including dev)
	uv sync --all-groups

dev:  ## Start development server with hot-reload
	uv run uvicorn src.server:app --reload --host 0.0.0.0 --port 8000

test:  ## Run full test suite with coverage
	uv run pytest tests/ -v --cov=src --cov-report=term-missing

test-unit:  ## Run only unit tests (fast, no infrastructure)
	uv run pytest tests/unit/ -v

test-integration:  ## Run only integration tests
	uv run pytest tests/integration/ -v

lint:  ## Run ruff linter
	uv run ruff check src/ tests/

format:  ## Auto-format code with ruff
	uv run ruff format src/ tests/

type-check:  ## Run mypy static type checker
	uv run mypy src/ --ignore-missing-imports

up:  ## Start PostgreSQL + API via Docker Compose
	docker compose up --build

down:  ## Stop and remove Docker Compose services
	docker compose down

clean:  ## Remove Python cache and coverage artifacts
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete 2>/dev/null || true
	rm -rf .pytest_cache coverage.xml .coverage
