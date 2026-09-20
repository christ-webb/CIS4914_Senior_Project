# API contract

Base path: `/v1`

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/health` | Liveness check |
| `POST` | `/v1/traces` | Validate and store one complete trace |
| `GET` | `/v1/traces` | List trace summaries, newest first |
| `GET` | `/v1/traces/{trace_id}` | Fetch a trace and all spans |

The initial API accepts whole traces to keep the first vertical slice small.
Streaming spans and OTLP ingestion should be added only after ordering,
idempotency, partial failure, and retry behavior are specified.

Errors use FastAPI's JSON error shape:

```json
{ "detail": "Trace not found" }
```

The JSON Schemas in `packages/contracts/schemas` define transport payloads.
OpenAPI documentation is generated at `/docs` when the API runs.

