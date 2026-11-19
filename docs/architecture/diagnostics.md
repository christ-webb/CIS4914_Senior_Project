# Diagnostic findings

No More 500s detects observable agent failure patterns from reconstructed
traces. Detectors are pure functions. They never rewrite stored telemetry.

## Finding model

Each finding includes a stable `finding_id`, related `span_ids`,
`detection_type`, `severity` (`info` / `warning` / `critical`), `confidence`
(`confirmed` / `heuristic`), `provisional`, title, explanation, evidence,
suggested action, timestamps, and `detector_version`.

- **Confirmed** means structured telemetry recorded the condition (span
  `status=error`, exception type, nonzero exit code, HTTP status).
- **Heuristic** means a deterministic pattern or text marker, not a recorded
  status.
- **Provisional** is independent of confidence. It is true while the run is
  `running` or `interrupted`. A confirmed tool error can still be provisional.

Explanations describe observed telemetry. They do not claim hidden model
reasoning.

## Stable IDs

`finding_id` is UUID5 of detector id/version, trace id, an occurrence anchor
(first related span id, or `trace`), and a normalized signature. Extra spans
that join the same pattern keep the same id. Different signatures or anchors
are different findings. The growing set of span ids is never the fingerprint.

## Detection types

| Type | What it observes |
| --- | --- |
| `tool_failure` | Tool spans with recorded error status, timeouts, or nonzero exit codes |
| `repeated_tool_call` | Equivalent tool name + normalized arguments, threshold default 3. Successful repeats are `info`, not failures |
| `schema_mismatch` | Existing parameter-error markers or structured validation metadata |
| `ineffective_recovery` | Repeated failed attempts with unchanged arguments, or a failing A/B cycle |
| `interruption` | Lifecycle `interrupted`. Not classified as a failed agent |
| `stall` | Long unset spans or telemetry silence on a still-executing trace. Idle open Cursor sessions are not stalls |
| `provider_error` | LLM spans with recorded error status / provider codes |
| `latency_anomaly` | Absolute duration threshold or historical comparison |
| `token_anomaly` / `cost_anomaly` | Absolute or historical, only when usage is reported |

Absolute threshold findings set `evidence_data.basis=absolute_threshold`.
Historical findings set `historical_comparison` and require at least
`NM5_HISTORICAL_MIN_PEERS` comparable runs in the current analysis window.

## Coverage

`unsupported_detectors` means instrumentation cannot supply that evidence
(for example Codex 0.153.x without model hooks: no LLM spans, token/cost
`unavailable`). `insufficient_data_detectors` means the detector ran but
lacked peers or reported usage. Missing usage is never treated as zero.

An empty finding list is not a proof of correctness; it means no supported
detector had enough evidence.

## Thresholds

Configured with `NM5_` settings (see `.env.example`):

- `NM5_REPEAT_THRESHOLD` (3)
- `NM5_RECOVERY_THRESHOLD` (3)
- `NM5_PARAMETER_ERROR_MIN` (2)
- `NM5_SPAN_LATENCY_MS` (30000)
- `NM5_TOKEN_ANOMALY_THRESHOLD` (50000)
- `NM5_COST_ANOMALY_THRESHOLD` (1.0)
- `NM5_HISTORICAL_MIN_PEERS` (5)
- `NM5_HISTORICAL_ANOMALY_RATIO` (3.0)
- `NM5_DIAGNOSTICS_SUMMARY_LIMIT` (25, max 50)

## Adding a detector

1. Implement `analyze_*(trace, ...) -> list[DiagnosticFinding]` using
   `build_finding`.
2. Register it in `DETECTORS` in
   `apps/api/src/no_more_500s_api/services/diagnostics/engine.py`.
3. Add unit tests for positive, negative, and incomplete-telemetry cases.
4. Document the type and evidence rules here.

Do not mutate the `Trace`. Do not call another LLM to write explanations.

## Dashboard window

`GET /v1/diagnostics` analyzes only the newest listed runs up to the summary
limit. The payload reports `analyzed_run_count` and a note that counts are not
all historical data. Trace detail always re-analyzes that trace.

## Demo

With the API running:

```bash
make demo              # successful run; should not create false failure findings
make demo-diagnostics  # four identical missing-file tool errors
```

Open `/errors` and the printed trace URL. Findings should highlight the
`read_file` spans. Restart the API (leave ClickHouse up) and reopen the trace;
finding ids stay the same.

## Known limitations

- Repeated-call matching is exact JSON equality after key sort, not semantic
  similarity.
- Parameter errors without structured schema metadata are heuristic text
  matches.
- Codex CLI 0.153.x cannot produce model, token, or cost findings.
- Individual Cursor Pro IDE hooks do not report billed cost; token findings
  require optional hook token fields that this Cursor build may omit.
- Historical comparison uses only peers inside the bounded window.
- False negatives: failures that never appear in span status or attributes.
- False positives: legitimate repeated successful tools (shown as `info`).
