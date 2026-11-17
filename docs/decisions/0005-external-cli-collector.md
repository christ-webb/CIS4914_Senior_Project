# ADR 0005: External CLI collector for Codex hooks

## Status

Accepted

## Context

No More 500s previously only observed in-process Python SDK runs. External
coding agents expose official lifecycle hooks. OTLP ingest remains deferred
(ADR 0003). A second telemetry database would split the source of truth.

Installed Codex CLI 0.153.4 documents 12 hook events and does not include
model-request hooks.

## Decision

Add a localhost collector that maps official Codex command hooks into the
existing live-run API. Persist collector coordination in SQLite. Keep ClickHouse
`run_events` authoritative. Pin Codex 0.153.x as the MVP target and treat newer
model hooks as optional.

## Consequences

- External sessions appear in Active Runs without wrapping `codex`.
- Token/cost fields must carry explicit availability metadata.
- Users must trust hooks (or bypass trust only in automation).
- Additional CLIs can share the adapter/pipeline without new storage.
