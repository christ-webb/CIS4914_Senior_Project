# Development guide

## Branches and pull requests

Create a short-lived branch for each issue. Keep PRs scoped to one outcome and
include tests or explain why a test is not applicable. Do not commit directly
to `main` after branch protection is enabled.

## Commands

Run `make bootstrap` once, then `make check` before requesting review. Use
`make clickhouse`, `make api-dev`, `make web-dev`, `make collector`, and
`make demo` for the first vertical slice. Codex CLI setup is in
[integrations/codex-cli.md](integrations/codex-cli.md).

## Persistent local traces

Copy `.env.example` to `.env`. The default `NM5_TRACE_REPOSITORY=clickhouse`
is the normal development store. Then run:

```bash
make clickhouse
make api-dev
make web-dev
```

The API reads `NM5_CLICKHOUSE_HOST`, `NM5_CLICKHOUSE_PORT`,
`NM5_CLICKHOUSE_DATABASE`, `NM5_CLICKHOUSE_USER`, and
`NM5_CLICKHOUSE_PASSWORD`. Compose uses `CLICKHOUSE_DATABASE`,
`CLICKHOUSE_USER`, and `CLICKHOUSE_PASSWORD`; keep corresponding values equal.
The example credentials are only for local development. The mounted
`infra/clickhouse/init.sql` creates the database and tables on first startup
with `IF NOT EXISTS`. Existing Compose volumes retain data. The API adds the
`ingestion_id` column with `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` on first
use and creates `run_events` if missing; it does not drop volumes or rewrite
historical rows. `NM5_RUN_INACTIVITY_SECONDS` (default 120) controls when a
running live trace is displayed as `interrupted`.

### Verify history survives restarts

1. Run `make demo` while the dashboard is open. Confirm the run appears under
   **Active runs** before the process exits, then remains after it completes.
2. Run `make demo` again. Confirm both runs appear on `/` and `/explorer`.
3. Stop the API process. Leave ClickHouse running. Do **not** run
   `docker compose down -v`.
4. Start `make api-dev` again and confirm both trace IDs still list and open.
5. Submit a third demo run and confirm all three remain.

Live ClickHouse tests:

```bash
NM5_TEST_CLICKHOUSE=1 uv run pytest apps/api/tests/test_clickhouse_repository.py
```

Unit tests always call `create_app(InMemoryTraceRepository())`. They do not
depend on Docker, `.env` import order, or the module-level `app` object.

### Duplicate IDs and partial writes

Re-posting the same `trace_id` returns HTTP 409 and does not add spans. The
check plus write is covered by a process-local lock (one API process only).
Each ingest stamps spans and the trace header with an `ingestion_id`. The API
returns only spans that match the committed header, so a failed header insert
followed by a retry does not double the visible graph.

Live events use a stable `event_id`. Replaying the same event does not add
spans or tokens. After finalize, new execution activity is HTTP 409; delayed
`span_complete` for spans already on the run is reconciled. The event log is
authoritative: a crash after inserting an event still leaves that event visible.

The tests use unique project IDs and leave their trace rows in the local
database. The API should not be exposed to untrusted telemetry without
authorization and input redaction; callers must not send credentials,
authorization headers, or other secrets in inputs or attributes.

### Missing run history

| Symptom | Likely cause |
| --- | --- |
| Dashboard shows "Can't reach the API" or explorer says storage is unavailable | API down, ClickHouse down, or wrong `NM5_CLICKHOUSE_*` credentials (HTTP 503) |
| History vanishes after `make api-dev` | `NM5_TRACE_REPOSITORY=memory` in `.env` |
| API returns 503 with a healthy Compose container | Missing `.env`, or `NM5_CLICKHOUSE_PASSWORD` does not match `CLICKHOUSE_PASSWORD` (Compose defaults to `nomore500s`; the API default is empty). Copy `.env.example` to `.env`. |
| Cursor in-app browser shows `ERR_CONNECTION_REFUSED` for localhost | The embedded browser cannot always reach the host machine. Open `http://localhost:3000` in a system browser. |
| Dashboard table only shows eight rows | Use **View all runs** / Trace Explorer |
| Explorer says no traces match | Filters are active; clear project, status, or time range |

## Contract changes

Changing a trace or span field requires coordinated updates to:

1. JSON Schema in `packages/contracts`
2. SDK model and serialization test
3. API model and endpoint test
4. Frontend TypeScript type
5. ClickHouse schema or migration, once persistence is enabled

## Definition of done

- Behavior is covered by an automated check.
- Public contracts and setup instructions are current.
- No secrets or customer telemetry are committed.
- The end-to-end demo still works.


## Windows local development and integration testing

The Makefile commands are the normal development workflow. On Windows, use the
commands below if `make api-dev` cannot find `uv` or `make web-dev` cannot find
`next`.

### One-time setup

From the repository root, copy the environment template and install Python
dependencies:

```powershell
Copy-Item .env.example .env
$env:Path = "C:\Users\<your-user>\.local\bin;$env:Path"
uv sync --all-packages --dev
```

Install frontend dependencies:

```powershell
cd .\apps\web
npm install
```

### Run the local stack

Start ClickHouse, then open three terminals.

```powershell
docker compose up -d --wait clickhouse
```

Terminal 1 - API:

```powershell
$env:Path = "C:\Users\<your-user>\.local\bin;$env:Path"
uv run --package no-more-500s-api uvicorn no_more_500s_api.main:app --reload --port 8000
```

Terminal 2 - web dashboard:

```powershell
cd .\apps\web
npm run dev
```

Terminal 3 - deterministic demo:

```powershell
$env:Path = "C:\Users\<your-user>\.local\bin;$env:Path"
uv run --package no-more-500s python .\examples\simple-agent\main.py
```

### Manual integration test checklist

1. Open `http://localhost:8000/docs` and confirm the API documentation loads.
2. Open `http://localhost:3000` and confirm the dashboard loads.
3. Run the deterministic demo and refresh the dashboard.
4. Confirm that a new run appears in the dashboard.
5. Select the run and confirm that its trace details load correctly.
6. If a trace does not load, save the API or browser error details and report the issue before merging changes.
