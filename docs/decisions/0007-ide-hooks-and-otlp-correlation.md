# ADR 0007: IDE hooks and Copilot OTLP correlation

## Status

Accepted

## Context

Codex already maps official CLI hooks into the live-run API (ADR 0005). Cursor
and VS Code GitHub Copilot expose different hook contracts. Copilot can also
export OTLP. ADR 0003 deferred a general OTLP backend; this decision adds a
**local, Copilot-only** OTLP HTTP receiver on the existing collector.

Hook `sessionId` and `gen_ai.conversation.id` are documented as conversation
ids but must not be assumed identical without evidence. Tool hooks may arrive
before or after OTel `execute_tool` spans.

## Decision

- Add `CursorAdapter` and `VSCodeCopilotAdapter` to the existing collector.
- Namespace SQLite session keys as `{agent}:{external_id}` and migrate Codex
  rows without resetting sequence or dropping pending events.
- Extend finalize/lifecycle with `cancelled` so abort/close is not success.
- Receive Copilot OTLP at collector `POST /v1/traces` with Bearer headers by
  default. Path-token auth is opt-in and localhost-only.
- Join sessions and tool spans only on exact identifiers or persisted
  mappings. Keep hook-only activity when OTel is absent. Never merge on name
  or timestamp.
- VS Code install is hook-only unless `--enable-otel`.

## Consequences

- Cursor and Copilot sessions appear beside Codex and SDK runs.
- Partial hook coverage is explicit in `nm5.coverage`.
- Remote SSH/containers/cloud agents remain unsupported without a collector in
  that environment.
- A general multi-vendor OTLP mesh is still out of scope.
