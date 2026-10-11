# Christopher Webb — expanded API and SDK test coverage

This work extends automated verification beyond the existing ClickHouse persistence
tests, without changing application behavior or another contributor's components.

## Test layers

- **API contract** (`apps/api/tests/test_api_contract.py`): Uses a fresh
  `InMemoryTraceRepository` per test. Covers listing order and summaries,
  missing/malformed trace IDs, empty traces, valid parent/child relationships,
  and invalid payloads (including validation without accidental persistence).
- **Python SDK** (`packages/python-sdk/tests/test_client.py`): Verifies nested
  UUID/date serialization and HTTP request/response and error propagation with
  `httpx.MockTransport`. No server or network connection is required.
- **Live ClickHouse** (`apps/api/tests/test_clickhouse_repository.py`):
  Existing opt-in integration check covering restart durability, ordering,
  multi-span retrieval, totals, and missing traces.

## Run checks from the repository root

```bash
uv sync --all-packages --dev
uv run pytest apps/api/tests/test_api_contract.py packages/python-sdk/tests/test_client.py
uv run pytest
uv run ruff check .
uv run mypy apps/api/src packages/python-sdk/src
```

The regular `uv run pytest` command skips the live ClickHouse suite unless
`NM5_TEST_CLICKHOUSE=1` is set. To run it locally, start the Compose database,
verify the matching credentials from `.env.example`, and execute:

```bash
docker compose up -d --wait clickhouse
NM5_TEST_CLICKHOUSE=1 uv run pytest apps/api/tests/test_clickhouse_repository.py
```

On PowerShell, set `$env:NM5_TEST_CLICKHOUSE = "1"` and run the `uv run pytest`
command separately. The live tests write uniquely identified traces; the database
keeps those records. The isolated API/SDK tests do not access ClickHouse.

## Coverage boundaries

These tests assert the **existing** API and SDK contracts. They do not claim
production authorization, secret filtering, failure-analysis functionality,
cross-service dashboard reliability, or generalized agent instrumentation.
Yuvika's end-to-end setup/workflow testing and John's dashboard work remain
separate. A passing unit suite is not a substitute for the manual dashboard
trace-selection checklist in `docs/development.md`.
