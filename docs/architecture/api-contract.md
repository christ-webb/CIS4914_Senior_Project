# API contract

Base path: `/v1`

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/health` | Liveness check (does not probe ClickHouse) |
| `POST` | `/v1/traces` | Validate and store one complete trace |
| `GET` | `/v1/traces` | List trace summaries, newest first |
| `GET` | `/v1/projects` | Distinct project IDs, sorted |
| `GET` | `/v1/traces/{trace_id}` | Fetch a committed trace and its visible spans |
| `GET` | `/v1/traces/{trace_id}/failures` | Repeated-tool-call findings for a stored trace |

The initial API accepts whole traces to keep the first vertical slice small.
Streaming spans and OTLP ingestion should be added only after ordering,
idempotency, partial failure, and retry behavior are specified.

## List query parameters

`GET /v1/traces` still returns a JSON **array** of summaries. Optional filters
are applied **before** pagination:

| Parameter | Meaning |
| --- | --- |
| `project_id` | Exact project match |
| `status` | `ok`, `error`, or `unset` |
| `started_after` | Inclusive lower bound on `started_at` |
| `started_before` | Inclusive upper bound on `started_at` |
| `limit` | 1–200; omitted returns all matching rows |
| `offset` | Rows to skip after filtering and ordering |

Ordering is `started_at DESC`, then `trace_id DESC`. Clients detect another
page by requesting `limit = page_size + 1` and keeping `page_size` items.

## Duplicate trace IDs

`POST /v1/traces` is first-write-wins. A second body with an existing
`trace_id` returns **409** `{ "detail": "Trace already exists" }` and does not
insert spans or change totals. The existence check and write share a
process-local lock. That is **not** a cross-process uniqueness guarantee.

## Storage errors

ClickHouse connectivity or write failures return **503**
`{ "detail": "Trace storage is unavailable" }`. They are not encoded as an
empty list. Missing traces remain **404**.

Errors use FastAPI's JSON error shape:

```json
{ "detail": "Trace not found" }
```

The JSON Schemas in `packages/contracts/schemas` define transport payloads.
OpenAPI documentation is generated at `/docs` when the API runs.
