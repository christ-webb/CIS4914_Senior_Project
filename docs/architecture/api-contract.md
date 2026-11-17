# API contract

Base path: `/v1`

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/health` | Liveness check (does not probe ClickHouse) |
| `POST` | `/v1/traces` | Validate and store one complete trace |
| `POST` | `/v1/runs` | Start a live agent run (persisted immediately) |
| `POST` | `/v1/runs/{trace_id}/events` | Append span_start, span_complete, or heartbeat events |
| `POST` | `/v1/runs/{trace_id}/finalize` | Mark a live run completed or failed |
| `GET` | `/v1/traces` | List trace summaries, newest first |
| `GET` | `/v1/projects` | Distinct project IDs, sorted |
| `GET` | `/v1/traces/{trace_id}` | Fetch a batch trace or reconstructed live run |
| `GET` | `/v1/traces/{trace_id}/failures` | Repeated-tool-call findings for a stored trace (`LoopFinding`, unchanged) |
| `GET` | `/v1/traces/{trace_id}/diagnostics` | Unified diagnostic findings and coverage for one trace |
| `GET` | `/v1/diagnostics` | Bounded recent-run diagnostic summary (not all history) |

`GET /v1/traces` summaries include optional `source`, `agent_framework`,
`provider`, `workspace`, `cost_status`, `token_status`, and
`instrumentation_coverage` copied from run attributes. Missing fields mean the
run was not marked as an external CLI session. `cost_status=unavailable` is not
the same as a reported zero cost.

`POST /v1/traces` remains first-write-wins and unchanged. Live runs use the
append-only event log described in ADR 0004. Each live event carries a stable
`event_id` (idempotency), client `sequence` (apply order), `occurred_at`
(execution time), and server `received_at` (liveness). Duplicate `event_id`s
return the current reconstructed trace without inflating spans or totals.

## List query parameters

`GET /v1/traces` still returns a JSON **array** of summaries. Optional filters
are applied **before** pagination:

| Parameter | Meaning |
| --- | --- |
| `project_id` | Exact project match |
| `status` | `ok`, `error`, or `unset` |
| `lifecycle` | `running`, `completed`, `failed`, `interrupted`, or `unknown` |
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

`GET /v1/traces/{trace_id}/diagnostics` accepts optional `severity`,
`detection_type`, `confidence`, and `span_id` filters. Missing traces are
**404**; storage errors are **503**. Findings on `running` / `interrupted`
traces set `provisional=true` and `analysis_status=provisional`.

`GET /v1/diagnostics` analyzes at most `limit` recent listed traces (default
`NM5_DIAGNOSTICS_SUMMARY_LIMIT`, maximum 50). The `analysis_scope` object
reports `analyzed_run_count` and an explicit note that counts are not all
historical data.

The JSON Schemas in `packages/contracts/schemas` define transport payloads.
OpenAPI documentation is generated at `/docs` when the API runs.
