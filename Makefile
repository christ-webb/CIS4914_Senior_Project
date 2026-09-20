.PHONY: bootstrap api-dev web-dev demo test lint typecheck check

bootstrap:
	uv sync --all-packages --dev
	npm --prefix apps/web install

api-dev:
	uv run --package no-more-500s-api uvicorn no_more_500s_api.main:app --reload --port 8000

web-dev:
	npm --prefix apps/web run dev

demo:
	uv run --package no-more-500s-example python examples/simple-agent/main.py

test:
	uv run pytest
	npm --prefix apps/web test

lint:
	uv run ruff check .
	npm --prefix apps/web run lint

typecheck:
	uv run mypy apps/api/src packages/python-sdk/src
	npm --prefix apps/web run typecheck

check: lint typecheck test

