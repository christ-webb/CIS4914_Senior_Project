# Telemetry contracts

These JSON Schemas are the language-neutral boundary between SDKs, ingestion,
storage, and the dashboard. The Python API models are the executable MVP
implementation; changes to either representation should update contract tests.

- `trace.schema.json`: one complete agent run and its spans
- `span.schema.json`: a single observable execution unit
- `failure.schema.json`: a detector finding attached to a trace or span

