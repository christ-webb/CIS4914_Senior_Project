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
packages/collector/    External hook collector (Codex, Cursor, VS Code Copilot)
packages/contracts/    Language-neutral JSON Schemas
examples/simple-agent/ Deterministic end-to-end demo
infra/clickhouse/      ClickHouse schema
docs/                  Architecture, API contract, and ADRs
```

## Prerequisites

- Python 3.12+
- [uv](https://docs.astral.sh/uv/)
- Node.js 22+ and npm
- Docker with Compose (required for the default ClickHouse store)

## Start the development stack

Copy the environment template and install dependencies:

```bash
cp .env.example .env
make bootstrap
```

Start ClickHouse, then the API and web app in separate **OS** terminals
(not Cursor's integrated terminals; those stop when the IDE quits):

```bash
make clickhouse
make api-dev
make web-dev
```

Then send the deterministic live example (it sleeps between steps so the
dashboard can show the run while it is still executing). Run it more than once;
earlier runs stay in history:

```bash
make demo
make demo-diagnostics
```

Open `http://localhost:3000` while the demo is running: an **Active runs** row
should appear before the process exits. After it finishes, the same trace stays
in Recent runs and Trace Explorer. `make demo-diagnostics` emits identical
failed `read_file` calls; the Errors page and trace graph should show
evidence-backed findings linked to those spans. `make demo` should not create
false failure findings. API documentation is at `http://localhost:8000/docs`.
Diagnostic types and thresholds are in
[docs/architecture/diagnostics.md](docs/architecture/diagnostics.md).

With Gemini credentials, `uv run --package no-more-500s-example python examples/simple-agent/gemini_agent.py`
uses the same live SDK against the real model.

To observe a real Codex CLI session, start `make collector`, run
`uv run nm5-codex-install-hooks`, then use `codex` as usual. See
[docs/integrations/codex-cli.md](docs/integrations/codex-cli.md).
Cursor: `uv run nm5-cursor-install-hooks`. VS Code Copilot:
`uv run nm5-vscode-install-hooks` (add `--enable-otel` for model/token spans).

`NM5_TRACE_REPOSITORY` defaults to `clickhouse`. Isolated unit tests inject the
in-memory repository and do not use Docker. Set
`NM5_TRACE_REPOSITORY=memory` only when you intentionally want a disposable
process-local store. See the [development guide](docs/development.md) for
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

### Local history management

`uv run nm5-history` is an OS-authenticated local CLI for clearing development history.
See [the local-history guide](docs/local-history.md) for opt-in setup, stopped-writer
requirements, confirmation, recovery, and post-reset session-creation limitations.
The web application has no destructive reset endpoint.
