# Cursor agent monitoring

No More 500s observes Cursor Agent Chat through official `hooks.json` command
hooks. You do not wrap the agent or change tool permissions.

## Supported versions

Verified against Cursor **3.30.14** and Cursor's current hooks documentation.
Desktop and CLI both load `hooks.json`. Cloud agents load **project** hooks
only, not `~/.cursor/hooks.json`.

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
| `stop` | Turn heartbeat (not session end); optional parent-agent token fields |
| `afterAgentResponse` | Turn completion heartbeat; optional parent-agent token fields |
| `afterAgentThought` | Executing heartbeat for the current generation; response text is redacted |
| `preCompact` | Context-window occupancy, never billable token usage |
| `sessionEnd` | Finalize |

## Session outcomes

| Cursor `reason` | Lifecycle | Outcome |
| --- | --- | --- |
| `completed` | `completed` | success |
| `error` | `failed` | failure |
| `aborted`, `user_close`, `window_close` | `cancelled` | cancelled |

Cancelled runs keep trace `status=unset` so they do not count as successful.

## Coverage

Hooks report lifecycle and tools. The public Cursor schema for `stop` is
`status` plus `loop_count`; `afterAgentResponse` documents `text`. Every agent
hook also receives the common fields `conversation_id`, `generation_id`,
`model`, `model_id`, and `cursor_version`. Token counts are **not** in the
published `stop` / `afterAgentResponse` schema. If a build still sends
`input_tokens`, `output_tokens`, `cache_read_tokens`, or `cache_write_tokens`,
those are parent-agent turn measurements, not context-window occupancy and not
subagent usage. If they are absent, tokens stay **Not reported**. Cost is not
present on official IDE hooks for individual Cursor Pro accounts and stays
**Not reported**. Do not treat missing usage as zero.

`preCompact` occupancy (`context_tokens` / `context_window_size`) is stored as
`nm5.context_*` and shown as a separate Context metric.

An idle open Composer conversation remains `running` with execution state
`idle`. It is listed under **Idle / Open**, not Active Runs. Active Runs is
executing turns only. Inactivity becomes `interrupted` only while a turn is
still `executing`. `stop` never finalizes the session.

Team Admin API billed-cost enrichment is not implemented for individual Pro
accounts. Dashboard CSV exports cannot be joined without a conversation id.

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

1. Start ClickHouse, the API, the collector, and the dashboard. Restart the
   collector after adapter changes; it does not auto-reload.
2. Run `uv run nm5-cursor-install-hooks` so `afterAgentResponse` and
   `afterAgentThought` are registered.
3. Start a disposable Cursor Agent session (Composer) in another project and
   run a harmless file-edit or terminal task.
4. Confirm collector logs show `POST /v1/collector/events` and
   `cursor hook event=... fields=...` for `stop` / `afterAgentResponse` /
   `preCompact`. `fields=` lists present allowlisted keys only, never values.
5. While the agent is working, the run is in **Active runs**. After the turn
   finishes, it moves to **Idle / Open** with lifecycle `running` and
   execution `idle`, not interrupted + live.
6. Tokens stay **Not reported** unless those logs include `input_tokens` or
   `output_tokens`. Cost stays **Not reported**.
7. Closing the Composer tab should emit `sessionEnd` and finalize the run.

Fixture tests cover mapping, usage precedence, and wrapper execution. Remaining
manual GUI checks: a new Composer 2.5 chat after collector restart, tab close
→ `sessionEnd`, and whether this Cursor build actually emits token fields.
