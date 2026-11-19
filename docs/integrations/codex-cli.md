# Codex CLI integration

Supported Codex CLI: **0.153.0–0.163.x** (verified against installed
`codex-cli 0.153.4`). Model request hooks (`PreModelRequest`,
`PostModelRequest`) exist only on **Codex ≥ 0.164** and are optional.

## What is captured automatically

After user-level hook install, a normal `codex` or `codex exec` session is
discovered. No wrapper is required.

Captured on 0.153.4:

- Session start and `SessionEnd` finalize
- Tool `PreToolUse` / `PostToolUse` spans
- Subagent start/stop when ids are present
- Heartbeats for `Stop`, `Interrupt`, prompt submit, compact, permission

Not captured on 0.153.4 (unsupported, not fabricated):

- Per-call model spans and token usage
- Monetary cost
- Prompts, tool arguments, tool results, transcripts, reasoning

`Stop` is a per-turn heartbeat. It does **not** complete the run.

## Turn evidence

| Codex event | Turn status | Evidence |
| --- | --- | --- |
| `UserPromptSubmit` | `executing` | observed |
| `PreToolUse` / `PostToolUse` with `turn_id` | `executing` (resumes the turn) | observed |
| `Stop` | `completed` | `codex.Stop` |
| `Interrupt` | `interrupted` | `codex.Interrupt` |
| `SessionEnd` while a turn is executing | `interrupted` | inferred (`session.finalize`) |

Turns are keyed by the hook payload's `turn_id`. Events recorded before the
collector forwarded `turn_id` fall back to ordinal turns: each prompt opens a
turn and the next `Stop` closes it. `SessionEnd` finalizes the session
(`completed`, or `failed` on error) and never rewrites a completed turn. Codex
does not report task success, so tasks stay **Unverified**.

## Setup

1. Start ClickHouse, the API, and the collector (localhost only):

```bash
make clickhouse
make api-dev
make collector
```

2. Install hooks (merges into existing `~/.codex/hooks.json` and writes a backup):

```bash
uv run nm5-codex-install-hooks
```

On Codex ≥ 0.164 you may add `--include-model-hooks`.

3. Trust the hooks in Codex (`/hooks`) unless you pass
   `--dangerously-bypass-hook-trust` for automation.
4. Run `codex` as usual. The dashboard Active Runs list should show `cli` /
   `codex`.

Uninstall:

```bash
uv run nm5-codex-uninstall-hooks
```

## Environment

| Variable | Purpose |
| --- | --- |
| `NM5_API_URL` | Live-run API (default `http://127.0.0.1:8000`) |
| `NM5_COLLECTOR_HOME` | SQLite + token directory |
| `NM5_COLLECTOR_TOKEN` | Shared localhost token |
| `NM5_COLLECTOR_URL` | Hook target (default `http://127.0.0.1:8788`) |
| `NM5_PROJECT_ID` | Optional project override |
| `NM5_COLLECT_TOOL_IO` | Default off. Do not enable unless you accept tool payloads. |
| `NM5_MAX_PENDING` | Pending-row cap (default 5000) |
| `CODEX_HOME` | Codex config directory for install/tests |

## Privacy

The hook is observe-only: it always exits 0 and prints `{}`. The collector binds
`127.0.0.1`, requires a bearer token, rejects oversized bodies, and redacts
prompts, tool I/O, assistant text, and transcript paths before SQLite or logs.
Set `NM5_HOOK_DEBUG=1` for bounded delivery logs that include collector
`result=` and `reason=` codes.

### After a local history reset

Codex CLI does not send `session_created_at`. The hook stamps
`_nm5_creation_observed_at` on `SessionStart`. Post-reset admission requires
`source` of `startup` or `new`. `resume` and inferred starts are rejected
(`reason=untrusted_session_source`). Start a new Codex session after reset; do
not expect a resumed thread to recreate cleared history. See
[local-history.md](../local-history.md).

## Manual acceptance test

1. Install hooks and start API + collector + dashboard.
2. Start a real `codex` session in a scratch directory.
3. Confirm the run appears on Active Runs while it is executing.
4. Trigger a tool (for example, ask Codex to list files). Confirm a tool span
   appears before the session ends.
5. Exit the session. Confirm lifecycle becomes `completed` (or `interrupted` if
   `SessionEnd` did not fire).
6. Restart the API and the collector. Confirm `GET /v1/traces/{id}` still
   returns the run (ClickHouse must remain up).

## Test labels

- **Fixture-tested:** mapping, redaction, duplicate delivery, Stop ≠ finalize,
  install merge, API outage replay, auth.
- **Verified live:** only when `NM5_TEST_CODEX=1` and `codex` is installed.
- **Unsupported on 0.153.4:** model hooks and cost.
