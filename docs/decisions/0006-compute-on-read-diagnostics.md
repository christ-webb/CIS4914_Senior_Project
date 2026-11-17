# ADR 0006: Compute-on-read diagnostic findings

## Status

Accepted

## Context

The platform already stores raw traces and live `run_events`. Detectors for
repeated tool calls and parameter errors existed as pure functions, but the
dashboard only grouped `status=error` spans. Findings need stable identities
across refreshes and API restarts, without rewriting telemetry or adding
workers.

## Decision

Derive diagnostics on read from reconstructed traces.

- Register independent detectors that return a unified `DiagnosticFinding`.
- Identify findings with UUID5(`detector`, `version`, `trace_id`, first-span
  anchor, normalized signature). Do not hash the changing span-id set.
- Keep `GET /v1/traces/{id}/failures` as the original `LoopFinding` list.
- Bound dashboard summaries to `NM5_DIAGNOSTICS_SUMMARY_LIMIT` recent runs and
  state that scope in the response.
- Separate `confidence` (evidence quality) from `provisional` (run still
  active) and from `severity`.
- Treat missing token/cost telemetry as unknown, never zero.
- Do not introduce Redis, extra ClickHouse tables, or LLM-generated
  explanations in this milestone.

## Consequences

- Reanalysis is free: changing detector logic immediately applies on the next
  GET. Historical findings after a restart match as long as telemetry and
  detector version are unchanged.
- Large fleets only analyze a recent window on the Errors page; full history
  is not implied.
- Caching can be added later if the bounded window is still too expensive.
