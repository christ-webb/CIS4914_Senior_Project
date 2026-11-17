.PHONY: bootstrap clickhouse api-dev web-dev collector demo demo-diagnostics test lint typecheck check

bootstrap:
	uv sync --all-packages --dev
	npm --prefix apps/web install

clickhouse:
	docker compose up -d --wait clickhouse

api-dev:
	uv run --package no-more-500s-api uvicorn no_more_500s_api.main:app --reload --port 8000

web-dev:
	npm --prefix apps/web run dev

demo:
	uv run --package no-more-500s-example python examples/simple-agent/main.py

demo-diagnostics:
	uv run --package no-more-500s-example python examples/simple-agent/diagnostic_demo.py

test:
	uv run pytest
	npm --prefix apps/web test

lint:
	uv run ruff check .
	npm --prefix apps/web run lint

collector:
	uv run --package no-more-500s-collector nm5-collector

typecheck:
	uv run mypy apps/api/src packages/python-sdk/src packages/collector/src
	npm --prefix apps/web run typecheck

check: lint typecheck test

