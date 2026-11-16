# ADR 0002: Use ClickHouse for MVP persistence

## Status

Accepted

## Context

Agent traces are append-heavy event data queried by project, time, status,
latency, tokens, and cost. The proposal allowed ClickHouse or TimescaleDB.

## Decision

Use ClickHouse for the semester MVP and avoid maintaining two storage paths.
Local development defaults to ClickHouse. Isolated unit tests inject an
in-memory repository and never share the module-level FastAPI app.

Duplicate `trace_id` values are rejected after the first successful header
insert. Visibility of spans is tied to an `ingestion_id` on the committed
trace row so a failed header insert plus retry cannot inflate the graph the
API returns. Duplicate detection is serialized with a process-local lock; it
does not provide uniqueness across multiple API processes.

## Consequences

- The database matches analytical trace queries and the approved stack.
- The team has one database to learn and operate.
- Authentication or other transactional product data may need a different
  store later.
- ClickHouse MergeTree still allows duplicate physical rows if two processes
  race or a retry writes extra span rows; only committed `ingestion_id`
  groups are readable through the API.
