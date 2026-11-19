# Telemetry model

## Trace

A trace represents one top-level agent or workflow run.

| Field | Purpose |
| --- | --- |
| `trace_id` | Stable UUID for the run |
| `project_id` | Customer-defined project namespace |
| `name` | Human-readable run name |
| `started_at` | UTC start timestamp |
| `status` | `unset`, `ok`, or `error` (outcome; not a live-run lifecycle) |
| `lifecycle` | `running`, `completed`, `failed`, `cancelled`, `interrupted`, or `unknown` |
| `execution_state` | `executing`, `idle`, or `unknown` (turn activity, not session outcome) |
| `last_activity_at` | Last observed telemetry receipt |
| `ended_at` | Set when a run is finalized |
| `attributes` | Extensible application metadata |
| `spans` | Ordered observable execution units |

The API derives duration, total tokens, and total cost from reconstructed spans.
Batch traces with `status=unset` have `lifecycle=unknown` (outcome not
confirmed). Live inactivity beyond `NM5_RUN_INACTIVITY_SECONDS` (default 120)
is shown as `interrupted` for in-progress executions, which is not a failure.
Cursor hook sessions whose turn ended with `stop` stay `running` with
`execution_state=idle` while the conversation remains open. Codex and VS Code
keep the previous inactivity rule. User abort or editor close is `cancelled`.
`nm5.outcome` records `success`, `failure`, `cancelled`, or `timeout`.

## Session, turn, and task are separate

| Concept | Values | Answers |
| --- | --- | --- |
| Session lifecycle (`lifecycle`) | open (`running`), finalized (`completed`), `cancelled`, `failed`, `interrupted`, `unknown` | Is the conversation still open, and how did it end? |
| Agent turn (`latest_turn_status`, `turns_*`) | `executing`, `response_observed` (provisional), `completed`, `failed`, `interrupted` | Did the agent finish responding to a prompt? |
| Task outcome (`task_outcome`) | `unverified`, `reported_success`, `reported_failure`, `verified_success`, `verified_failure` | Did the work actually achieve the goal? |

Turns are a projection over the append-only event log, rebuilt on every read;
no stored data is rewritten.

- A turn becomes terminal only from authoritative evidence: Cursor `stop`,
  Codex `Stop` / `Interrupt`, or an SDK `finalize`. Once terminal, a turn is
  never changed by later tool activity, a later failed turn, inactivity, or
  closing or cancelling the session.
- Turn identity comes from `nm5.turn_id` (Codex `turn_id`) or
  `nm5.generation_id` (Cursor). Cursor step-suffixed thought ids
  (`<generation uuid>-<n>-<suffix>`) resolve to their generation. Events
  without an id attach to the latest open turn and never create or close a
  turn on their own (`nm5.turn_identity=ordinal`).
- A session that finalizes while a turn is still executing marks that turn
  `interrupted` with provenance `inferred`. Inactivity does the same.
- Each turn records `evidence` (for example `cursor.stop`, `codex.Stop`,
  `session.finalize`) and `provenance`: `observed`, `provisional`, `inferred`,
  or `reported`. The `nm5.turns` attribute lists up to 200 turns.
- Batch traces without turn events fall back to one turn derived from
  `status` (evidence `batch.status`).

Task outcome is never inferred from turn completion. Agent self-reports use
`nm5.task_outcome=success|failure` and become `reported_*`. Independent checks
use `nm5.task_verification=passed|failed` plus `nm5.task_verifier` and become
`verified_*`, which outranks a report. The Python SDK accepts
`finalize(..., task_outcome=..., task_verification=..., task_verifier=...)`.
Cursor and Codex hooks emit neither, so their tasks remain `unverified`.

### Analytics denominators

- **Turn completion** = completed turns ÷ terminal turns (completed + failed +
  interrupted). Executing and provisional turns are excluded and shown
  separately. Shown as `—` when no turn is terminal.
- **Verified task success** = `verified_success` ÷ verified outcomes. Shown as
  **Unverified** when no verified outcome exists; reported outcomes are listed
  separately and never counted.
- **Session outcomes** are counts by lifecycle, not a success rate.

## Span

A span is an observable unit of execution. Initial types are `agent`, `llm`,
`tool`, and `workflow`. A nullable `parent_span_id` expresses hierarchy. Inputs
and outputs must be data the instrumented application is authorized to collect.

## Failure / diagnostic finding

A diagnostic finding is derived from a reconstructed trace and optionally one
or more spans. It is kept separate from raw spans so algorithms can evolve
without rewriting source telemetry. See
[diagnostics.md](diagnostics.md).

List summaries copy `nm5.source`, `agent.framework`, `gen_ai.provider`,
`nm5.workspace`, `nm5.cost_status`, `nm5.token_status`, and `nm5.coverage`.
`cost_status=unavailable` or `token_status=unavailable` is not a reported
zero. Detectors must not treat missing usage as zero consumption.

## Attribute conventions

Use namespaced keys for non-core attributes:

```text
gen_ai.provider
gen_ai.request.model
gen_ai.usage.input_tokens
gen_ai.usage.output_tokens
tool.name
tool.arguments_schema
agent.framework
app.version
```

Never store secrets, authorization headers, or raw credentials. Redaction and
retention policies are required before production use.

