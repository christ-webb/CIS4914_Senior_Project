# ADR 0004: Authoritative event log for live runs

## Status

Accepted

## Context

Batch `POST /v1/traces` persists a complete snapshot. Live monitoring needs
events while an agent is still running. ClickHouse MergeTree does not provide
unique keys or multi-row transactions, so a header-then-span write sequence
cannot be treated as atomic.

## Decision

Store live telemetry only in an append-only `run_events` table. Reads
reconstruct the current trace by sorting unique `event_id`s on
`(sequence, occurred_at, event_id)`. Batch traces remain in `traces`/`spans`
with `ingestion_id` visibility.

`lifecycle` is separate from OTEL `status`. Batch traces with `status=unset`
are `unknown`, not successful. Terminal `completed`/`failed` runs never return
to `running`. Inactivity is `interrupted` (unknown outcome), not `failed`.

Delayed `span_complete` events after finalize are reconciled onto existing
spans. New execution activity after finalize is rejected with HTTP 409.
Duplicate `event_id`s are idempotent.

## Consequences

- Partial write after an event insert is still visible after reconstruction.
- Retries with the same `event_id` repair remaining events without inflating
  tokens or spans.
- The API remains single-process for uniqueness, matching ADR 0002.
