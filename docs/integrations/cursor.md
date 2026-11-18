# Cursor agent monitoring

No More 500s observes Cursor Agent Chat through official `hooks.json` command
hooks. You do not wrap the agent or change tool permissions.

## Supported versions

Verified against Cursor's current hooks documentation (`sessionStart`,
`sessionEnd`, `preToolUse`, `postToolUse`, `postToolUseFailure`,
`subagentStart`, `subagentStop`, `stop`). Desktop and CLI both load
`hooks.json`. Cloud agents load **project** hooks only, not `~/.cursor/hooks.json`.

## Install

Start ClickHouse, the API, and the collector, then install user-level hooks:

```bash
make clickhouse
make api-dev
make collector
uv run nm5-cursor-install-hooks
```

Project-only install (needed for cloud agents):

```bash
uv run nm5-cursor-install-hooks --project /path/to/repo
```

Uninstall removes only No More 500s entries:

```bash
uv run nm5-cursor-uninstall-hooks
```

The installer backs up `hooks.json`, writes atomically, and skips duplicates.
Hook commands always print `{}` and exit `0`. They never return `permission` or
`followup_message`.

## Observed events

| Hook | Dashboard meaning |
| --- | --- |
| `sessionStart` | Creates the live run |
| `preToolUse` / `postToolUse` | Tool spans |
| `postToolUseFailure` | Tool error; user interrupt is not a model failure |
| `subagentStart` / `subagentStop` | Nested agent spans |
| `stop` | Turn heartbeat (not session end) |
| `sessionEnd` | Finalize |

## Session outcomes

| Cursor `reason` | Lifecycle | Outcome |
| --- | --- | --- |
| `completed` | `completed` | success |
| `error` | `failed` | failure |
| `aborted`, `user_close`, `window_close` | `cancelled` | cancelled |

Cancelled runs keep trace `status=unset` so they do not count as successful.

## Coverage

Hooks report lifecycle and tools. Model **identifiers** may appear on payloads.
Token usage and cost are `unavailable` unless later telemetry reports them.
Do not treat missing usage as zero.

## Remote and cloud

`CURSOR_CODE_REMOTE=true` means hooks run on the remote host. The laptop
collector at `127.0.0.1:8788` is not reachable unless you run the collector in
that environment. Cloud agents cannot load user-level hooks and cannot reach
your local collector. Those environments are **unsupported** in this milestone.

## Verify

1. Start a disposable Cursor agent session and run a harmless file-read task.
2. Confirm an Active Runs row with source `cursor`.
3. Confirm tool spans appear while the session is running.
4. End the session and confirm history after API/collector restart.

If you cannot drive a live Cursor session, fixture tests still cover mapping;
live verification remains pending.
