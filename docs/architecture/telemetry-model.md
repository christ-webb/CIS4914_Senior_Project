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
| `lifecycle` | `running`, `completed`, `failed`, `interrupted`, or `unknown` |
| `last_activity_at` | Last observed telemetry receipt |
| `ended_at` | Set when a run is finalized |
| `attributes` | Extensible application metadata |
| `spans` | Ordered observable execution units |

The API derives duration, total tokens, and total cost from reconstructed spans.
Batch traces with `status=unset` have `lifecycle=unknown` (outcome not
confirmed). Live inactivity beyond `NM5_RUN_INACTIVITY_SECONDS` (default 120)
is shown as `interrupted`, which is not a failure.

## Span

A span is an observable unit of execution. Initial types are `agent`, `llm`,
`tool`, and `workflow`. A nullable `parent_span_id` expresses hierarchy. Inputs
and outputs must be data the instrumented application is authorized to collect.

## Failure

A failure is a detector finding linked to a trace and optionally a span. It is
kept separate from raw spans so algorithms can evolve without rewriting source
telemetry.

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

