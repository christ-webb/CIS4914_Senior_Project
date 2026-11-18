# Cursor agent monitoring

No More 500s observes Cursor Agent Chat through official `hooks.json` command
hooks. You do not wrap the agent or change tool permissions.

## Supported versions

Verified against Cursor's current hooks documentation. Desktop and CLI both
load `hooks.json`. Cloud agents load **project** hooks only, not
`~/.cursor/hooks.json`.

User-level hooks run with working directory `~/.cursor/`. Project-level hooks
run from the project root. The installer writes a CWD-safe wrapper so Cursor's
GUI PATH is not required.

## Install

Start ClickHouse, the API, and the collector, then install user-level hooks:

```bash
make clickhouse
make api-dev
make collector
uv run nm5-cursor-install-hooks
```

That writes an executable wrapper at `~/.cursor/hooks/nm5-cursor-hook` and
registers `./hooks/nm5-cursor-hook` in `~/.cursor/hooks.json`. Re-running the
installer repairs older bare `nm5-cursor-hook` entries.

Project-only install (needed for cloud agents):

```bash
uv run nm5-cursor-install-hooks --project /path/to/repo
```

Project installs use `.cursor/hooks/nm5-cursor-hook` relative to the repo root.

Uninstall removes only No More 500s entries:

```bash
uv run nm5-cursor-uninstall-hooks
```

The installer backs up `hooks.json` and writes atomically. Hook commands always
print `{}` and exit `0`. They never return `permission`, `updated_input`, or
`followup_message`.

Permission-sensitive hooks (`preToolUse`, `subagentStart`,
`beforeShellExecution`, `beforeMCPExecution`, `beforeReadFile`) are **not**
registered, so Cursor keeps its own allow/deny flow. Tool spans come from
`postToolUse` / `postToolUseFailure`; a missing `sessionStart` is inferred.

## Observed events

| Hook | Dashboard meaning |
| --- | --- |
| `sessionStart` | Creates the live run |
| `postToolUse` | Tool span completion |
| `postToolUseFailure` | Tool error; user interrupt is not a model failure |
| `subagentStop` | Nested agent span completion |
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

## Diagnostics

Diagnostics are off by default. Set `NM5_HOOK_DEBUG=1` to append a bounded log
at `~/.nomore500s/collector/hook.log` (or `$NM5_COLLECTOR_HOME/hook.log`).

- Wrapper lines record invocation and whether the Python interpreter launched.
- Python lines record sanitized event name, session-id presence, delivery
  status, and collector host.

The log never includes tokens, prompts, tool inputs, tool outputs, or workspace
paths, and it is truncated at 256 KiB.

## Remote and cloud

`CURSOR_CODE_REMOTE=true` means hooks run on the remote host. The laptop
collector at `127.0.0.1:8788` is not reachable unless you run the collector in
that environment. Cloud agents cannot load user-level hooks and cannot reach
your local collector. Those environments are **unsupported** in this milestone.

## Verify

1. Start ClickHouse, the API, the collector, and the dashboard.
2. Run `uv run nm5-cursor-install-hooks` so the user-level wrapper is current.
3. Start a disposable Cursor Agent session (Composer) in another project and
   run a harmless file-edit or terminal task.
4. Confirm collector logs show `POST /v1/collector/events`.
5. Confirm an Active Runs row with source `cursor`, then history after restart.

If you cannot drive a live Cursor session, fixture tests still cover mapping
and wrapper execution; live GUI verification remains pending until a real hook
event is observed.
