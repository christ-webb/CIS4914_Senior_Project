# No More 500s

End-to-end observability and debugging for autonomous, multi-step AI agents.

No More 500s captures observable agent execution data—agent runs, model calls,
tool calls, latency, tokens, cost, inputs, outputs, and errors—and reconstructs
each run as an interactive trace graph. The MVP deliberately avoids depending
on access to a model's private chain of thought.

## Repository layout

```text
apps/api/              FastAPI ingestion and query API
apps/web/              Next.js dashboard and React Flow trace graph
packages/python-sdk/   Installable Python instrumentation SDK
packages/contracts/    Language-neutral JSON Schemas
examples/simple-agent/ Deterministic end-to-end demo
infra/clickhouse/      ClickHouse schema
docs/                  Architecture, API contract, and ADRs
```

## Prerequisites

- Python 3.12+
- [uv](https://docs.astral.sh/uv/)
- Node.js 22+ and npm
- Docker with Compose (optional, for ClickHouse)

## Start the development stack

Copy the environment template and install dependencies:

```bash
cp .env.example .env
make bootstrap
```

Run the API and web app in separate terminals:

```bash
make api-dev
make web-dev
```

Then send the deterministic example trace:

```bash
make demo
```

Open `http://localhost:3000` to view runs and select the generated trace. API
documentation is available at `http://localhost:8000/docs`.

To persist traces, set `NM5_TRACE_REPOSITORY=clickhouse` in `.env` and start ClickHouse
before starting the API:

```bash
docker compose up -d clickhouse
docker compose ps
```

The API uses memory by default for lightweight development. ClickHouse retains
traces across API restarts. See the [development guide](docs/development.md) for
configuration, verification, and persistence tests.

## Quality checks

```bash
make check
```

This runs Python linting and tests plus frontend linting, type checking, and
tests. CI runs the same areas independently.

## First milestone

The scaffold establishes one narrow path:

```text
Example agent -> Python SDK -> FastAPI -> trace query -> Next.js -> trace DAG
```

Authentication, billing, generalized hallucination detection, Redis, and
production deployment are intentionally outside this initial scaffold.

## Documentation

- [Architecture overview](docs/architecture/overview.md)
- [Telemetry model](docs/architecture/telemetry-model.md)
- [API contract](docs/architecture/api-contract.md)
- [Development guide](docs/development.md)
- [Architecture decisions](docs/decisions/)
