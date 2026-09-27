# Architecture overview

## Goal

Prove that No More 500s can ingest observable AI-agent execution events and
reconstruct a useful execution graph before implementing advanced detection or
analytics.

## Initial data path

```mermaid
flowchart LR
    A[Example agent] --> B[Python SDK]
    B --> C[FastAPI]
    C --> D[Trace repository]
    D --> E[Query API]
    E --> F[Next.js dashboard]
    F --> G[React Flow DAG]
```

The API selects a repository at startup. Memory supports lightweight development;
ClickHouse persists each trace summary and its spans in separate MergeTree tables.
Queries reconstruct ordered spans and parent IDs through the repository boundary.

## Boundaries

- **SDK:** Creates and exports vendor-neutral trace payloads. It does not know
  about the dashboard or database.
- **API:** Validates payloads and coordinates storage. Routes do not contain
  storage queries.
- **Contracts:** Define stable, language-neutral event shapes.
- **Dashboard:** Reads only the public API and maps spans into nodes and edges.
- **Detectors:** Future services consume stored traces and emit `Failure`
  records; they do not mutate raw telemetry.

## Privacy boundary

The platform records explicit application inputs, outputs, tool activity, and
metadata supplied by the customer. It does not claim to capture or reconstruct
a model provider's private chain-of-thought reasoning.

## Deferred work

- API keys and multi-tenant authorization
- Schema migrations for future telemetry contract changes
- Redis until caching or asynchronous ingestion is justified
- OTLP exporter and framework integrations
- Loop, schema mismatch, and tool hallucination detectors
- Cost/latency comparison and regression views
- Production deployment and operational controls
