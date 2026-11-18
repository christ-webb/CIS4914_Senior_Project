# External collectors

External collectors translate supported coding-agent lifecycle hooks into the
existing live-run HTTP contract (`POST /v1/runs`, `/events`, `/finalize`).
ClickHouse `run_events` remains the authoritative store. The collector SQLite
file is only a local delivery and identity buffer.

```text
Codex CLI / Cursor / Copilot hooks  -->  nm5-*-hook  -->  nm5-collector
Copilot OTLP (optional)             -->  POST /v1/traces
                                                    -->  SQLite pending
                                                    -->  FastAPI live-run API
                                                    -->  ClickHouse
```

## Adapter contract

Each adapter maps one redacted hook payload plus a `delivery_id` to zero or more
live-run events. Unknown events are ignored. Missing session start infers a
start from the first observed event. Turn `stop` / `agentStop` is never a
session terminal event. Cursor/Copilot abort and window close finalize as
`cancelled`, not `completed`.

## Identity

- `delivery_id` is created once per hook process and reused on HTTP retries.
- Stable occurrence keys exist for `SessionStart`/`SessionEnd` and for tool
  `tool_use_id` / subagent `agent_id` pairs.
- `Stop`, `Interrupt`, `UserPromptSubmit`, permission, and compact events have
  no stable Codex occurrence id. Those keys are the delivery id so repeated
  legitimate fires are not collapsed.

## Overflow

Pending rows are capped (`NM5_MAX_PENDING`, default 5000). Heartbeats are dropped
first. If only critical events remain, ingest returns HTTP 503.
