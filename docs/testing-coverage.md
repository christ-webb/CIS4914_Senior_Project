# Christopher Webb — expanded API and SDK test coverage

This work extends automated verification beyond the existing ClickHouse persistence
tests, without changing application behavior or another contributor's components.

## Test layers

- **Failure-analysis HTTP API** (`apps/api/tests/test_failure_api.py`): Tests the read-only\n  `GET /v1/traces/{trace_id}/failures` endpoint, repeated identical tool calls,\n  wasted token/cost estimates, empty findings, non-tool spans, 404/422 responses,\n  and preservation of original stored traces and trace summaries.\n- **API contract** (`apps/api/tests/test_api_contract.py`): Uses a fresh
  `InMemoryTraceRepository` per test. Covers listing order and summaries,
  missing/malformed trace IDs, empty traces, valid parent/child relationships,
  and invalid payloads (including validation without accidental persistence).
- **HTTP integration regressions** (`apps/api/tests/test_trace_integration_regressions.py`):\n  Exercises POST/GET/list together with a fresh in-memory repository for each\n  test. Verifies summary/detail consistency, newest-first ordering, empty\n  traces, missing/malformed IDs, invalid request atomicity for existing data,\n  multi-level parent links, nested input/output, and aggregate totals.\n- **Python SDK** (`packages/python-sdk/tests/test_client.py`): Verifies nested
  UUID/date serialization and HTTP request/response and error propagation with
  `httpx.MockTransport`. No server or network connection is required.
- **Live ClickHouse** (`apps/api/tests/test_clickhouse_repository.py`):
  Existing opt-in integration check covering restart durability, ordering,
  multi-span retrieval, totals, and missing traces.

## Run checks from the repository root

```bash
uv sync --all-packages --dev
uv run pytest apps/api/tests/test_api_contract.py apps/api/tests/test_trace_integration_regressions.py apps/api/tests/test_failure_api.py packages/python-sdk/tests/test_client.py
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

## API integration acceptance checklist

These checks are automated by the new HTTP regression file, using an isolated
FastAPI instance and in-memory persistence:

1. Create multiple traces with distinct timestamps and verify newest-first
   listing and that summary metrics match each trace's details.
2. Confirm list responses omit full span bodies and trace attributes.
3. Submit malformed and missing fields, invalid span values, and orphan
   parent IDs; verify HTTP 422 and no change to existing saved traces.
4. Create and retrieve an empty trace, then request both an unknown UUID
   (404) and malformed UUID (422); verify listing remains unchanged.
5. Create a three-level parent/child/grandchild trace with children before
   their parents and nested input/output; verify IDs, links, values, totals,
   and durations survive the POST/GET round trip.

Run only these regressions (no database required):

```bash
uv run pytest apps/api/tests/test_trace_integration_regressions.py -v
```

This suite uses application-level integration with the in-memory repository;
it **does not** prove ClickHouse's ordering, durability, or transaction behavior.
For ClickHouse, run the opt-in suite below and follow the manual UI selection
checklist in `docs/development.md`. The database's span query sorts by
`started_at, span_id`, so its retrieval order is not guaranteed to match the
original ingestion array. Do not assume the two backends have identical span
ordering without an explicit contract decision.

## Coverage boundaries

These tests assert the **existing** API and SDK contracts. They do not claim
production authorization, secret filtering, failure-analysis functionality,
cross-service dashboard reliability, or generalized agent instrumentation.
Yuvika's end-to-end setup/workflow testing and John's dashboard work remain
separate. A passing unit suite is not a substitute for the manual dashboard
trace-selection checklist in `docs/development.md`.

## Failure detection endpoint acceptance

The read-only endpoint `GET /v1/traces/{trace_id}/failures` runs the existing
identical-input tool-call detector over a persisted trace. The response is a JSON
list of findings with `span_name`, `repeat_count`, `span_ids`,
`wasted_tokens`, and `wasted_cost_usd`. The first invocation in each
repeated group is treated as legitimate for estimated waste accounting.

There are no new database tables or trace mutations. A valid trace with no
matching pattern returns `200 []`; a missing UUID returns 404, and an invalid
UUID returns 422. The default threshold is three identical tool calls.
An empty list does **not** guarantee that the agent executed correctly: the
rule is only a heuristic for exact repeated tool names and JSON inputs.

Execute the HTTP regression suite with:

```bash
uv run pytest apps/api/tests/test_failure_api.py -v
```

To verify the route against real ClickHouse, run the API with
`NM5_TRACE_REPOSITORY=clickhouse`, create a trace containing at least three
identical tool calls using `POST /v1/traces`, request the new failures URL,
restart the API, and check that the same findings remain available. The
isolated tests use memory storage and do not replace this manual live check.
