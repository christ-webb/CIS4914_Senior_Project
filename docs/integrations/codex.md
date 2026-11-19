# Codex integration (CLI, `codex exec`, IDE extension)

No More 500s monitors OpenAI Codex through Codex's native **lifecycle hooks**.
One user-level hook install covers three surfaces, because they all read
`~/.codex` (`CODEX_HOME`):

| Surface | Run source | Codex originator | How it is identified |
| --- | --- | --- | --- |
| Interactive `codex` (TUI) | `codex-cli` | `codex-tui` | rollout `session_meta.originator` |
| `codex exec` | `codex-exec` | `codex_exec` | rollout `session_meta.originator` |
| Codex IDE extension (Cursor / VS Code agent panel) | `codex-ide` | `codex_vscode` | rollout `session_meta.originator` |
| Surface not determinable | `codex` | — | rollout header missing or unrecognized |

The dashboard labels these as Codex CLI, Codex exec and Codex IDE. In the
Explorer, the "Codex (all surfaces)" filter includes all of them, plus legacy
runs recorded as `cli`.

The Codex IDE extension is not Cursor's native Agent, and it is not GitHub
Copilot. Its runs never use the `cursor` source.

## Verified versions

These were verified on 2026-11-19 against the binaries installed on the
development Mac:

| Component | Version | Hooks feature |
| --- | --- | --- |
| Codex CLI (Homebrew) | `codex-cli 0.153.4` | stable, enabled |
| Codex IDE extension `openai.chatgpt-26.1111.21308` (Cursor) | bundles `codex-cli 0.169.0-alpha.11.1` | stable, enabled |

Both binaries support the same hook events: `SessionStart`, `SessionEnd`,
`UserPromptSubmit`, `PreToolUse`, `PostToolUse`, `PermissionRequest`,
`PreCompact`, `PostCompact`, `SubagentStart`, `SubagentStop`, `Stop` and
`Interrupt`. **Neither binary has `PreModelRequest` or `PostModelRequest`**, so
model-call spans and token counts are not available through hooks.

A live `codex exec` probe on 0.153.4 observed this order:

`SessionStart(source=startup)` → `UserPromptSubmit` → `PreToolUse` →
`PostToolUse` → `Stop(stop_hook_active=false)` → `SessionEnd(reason=other)`

The hook payloads contain no field that names the client. The hook reads only
the first line of the rollout file named by `transcript_path`, and keeps only
`originator` and `cli_version`. Instructions, prompts and other content are
never read past that header or forwarded.

## Setup

1. Start ClickHouse, the API and the collector (all localhost):

   ```bash
   make clickhouse && make api-dev && make collector
   ```

2. Install the hooks:

   ```bash
   uv run nm5-codex-install-hooks
   ```

   This writes an observe-only wrapper to `~/.codex/hooks/nm5-codex-hook` and
   points `~/.codex/hooks.json` at that wrapper by **absolute path**. Codex, and
   especially the IDE extension's app-server, does not inherit the project
   virtualenv's `PATH`. Existing third-party hooks are kept, and a backup of
   `hooks.json` is written first.

   - `SessionStart`, `SessionEnd`, `Stop` and `Interrupt` run synchronously,
     with a 3s timeout and a 2s delivery budget, so lifecycle evidence is not
     lost when a one-shot `codex exec` process exits.
   - Other events run asynchronously.

3. **Trust the hooks (required).** Open `codex`, run `/hooks`, and trust the
   "No More 500s collector" hooks. Codex skips untrusted hooks:
   - Trust is stored per hook entry in `~/.codex/config.toml` under
     `[hooks.state]`.
   - It is shared by the CLI, `codex exec` and the IDE extension.
   - **Every reinstall changes the hooks and requires trusting them again.**
   - The installer never writes trust entries itself.

4. Verify the setup without spending model usage:

   ```bash
   uv run nm5-codex-doctor              # read-only
   uv run nm5-codex-doctor --hook-smoke # also runs the wrapper once (creates no run)
   ```

   The doctor reports two separate sections:
   - **Configuration** covers the binaries and versions, hooks feature,
     handlers, absolute and executable command, interpreter, trust entries,
     collector reachability and auth, API reachability, and history-reset
     readiness.
   - **Observed live** lists, per surface, the last event the collector actually
     accepted, plus bounded rejection counters.

   A `hooks.json` file alone is never reported as healthy. Trust entries are
   checked by key only, because the hash cannot be verified offline.

To uninstall, run `uv run nm5-codex-uninstall-hooks`. It removes only the No
More 500s handlers and the wrapper.

## Lifecycle semantics

Turn completion, session completion, tool outcomes and task success are tracked
separately.

| Event | Effect |
| --- | --- |
| `SessionStart` | Run starts. Source comes from the originator. |
| `UserPromptSubmit` | A turn (`turn_id`) is executing. |
| `PreToolUse` / `PostToolUse` | Tool span, keyed by `tool_use_id`. |
| `Stop` | The turn is **completed** (`codex.Stop`). The session stays open: **Idle / Open**. |
| `Interrupt` | The turn is **interrupted**. |
| `SessionEnd` | The session is finalized: `completed`, or `failed` when `reason` is `error`/`failed`. Evidence: `nm5.terminal_evidence=codex.SessionEnd`. |
| No events for 120s while a turn is executing | **Interrupted** (inactivity). This is never shown as completed. |

**Interactive (`codex-cli`, `codex-ide`).**
- Each prompt/`Stop` pair is one completed turn, and the conversation stays
  Idle / Open between turns.
- Exiting the TUI or closing the IDE thread emits `SessionEnd` and finalizes the
  session.
- Events that arrive after finalization are refused by the API. The collector
  counts them as `api_conflict`, and they never reopen the run.

**`codex exec` (`codex-exec`).**
- Codex itself emits `SessionEnd` when the process exits, so a successful
  one-shot run is **Completed** natively.
- It appears in Recently Completed.
- No wrapper is required.

**Optional launcher for exit status:** `nm5-codex-exec [codex exec args…]`.
- `SessionEnd` carries `reason=other` even when `codex exec` exits nonzero.
- For runs started through the launcher, the session is finalized from the
  **process exit status** instead:
  - exit 0 → completed
  - nonzero → failed (`nonzero_exit`)
  - signal → cancelled
- Evidence: `nm5.terminal_evidence=nm5-codex-exec.exit`. Launched runs carry
  `nm5.launcher=nm5-codex-exec`.
- Native, unwrapped `codex exec` runs do not get exit-status evidence.
- The launcher passes stdio through unchanged. It learns the session id from
  the hook through a private temporary file.

**Tool outcomes.** Codex's `PostToolUse.tool_response` is free text with no
structured exit status. Most commands run inside a "code mode" `exec` script,
and exit codes appear only if the model prints them.
- Codex tool spans are therefore `ok` only in the sense that "the call
  returned", and they carry `nm5.tool_outcome=unreported`.
- A failing test followed by a passing rerun never marks the run failed.
  Neither tool output nor its pass/fail state is inferred from text.

**Task success.** Codex does not report task success, so tasks stay
**Unverified**.

## After a local history reset

Codex does not send `session_created_at`. Instead, the hook stamps
`_nm5_creation_observed_at` when it observes `SessionStart`.

Post-reset admission requires all of the following:
- `SessionStart` with `source` `startup` or `new`
- that observation no more than 120s old and after the reset boundary
- an identity that is not in the deny filter

Consequences:
- A new conversation on any surface is admitted.
- `resume`, inferred starts, continuation events for unknown sessions, cleared
  session ids and stale evidence are rejected. Reasons:
  `untrusted_session_source`, `not_session_start`, `denied_identity`,
  `stale_creation_evidence`.
- A resumed pre-reset thread is not recreated. Start a new conversation instead.

Rejections appear in `nm5-codex-doctor` and in `GET /v1/collector/diagnostics`.
That endpoint requires the collector bearer token and returns counts and
reasons only, with no ids or content.

## Diagnostics reference

| Symptom (doctor / hook.log) | Meaning |
| --- | --- |
| `hooks.json … missing` | Hooks were not installed. Codex never calls No More 500s. |
| `hook command … relative` | Legacy install. Reinstall to get an absolute wrapper. |
| `hook trust … untrusted` | Run `/hooks` in `codex` and trust the hooks. |
| No Codex `python_hook` lines in `hook.log` while debugging (`--debug` install or `NM5_HOOK_DEBUG=1`) | The hook was not invoked: either untrusted, or the hooks feature is off. |
| `rejected_reset_boundary reason=…` | The history guard refused the session. See above. |
| `invalid_payload` | Missing session id or event. |
| `collector_overflow` | The pending queue is full. |
| `api_conflict events_dropped` | Late events after finalization (expected, harmless). |
| `collector delivery … last flush error` | The API is unreachable; events are queued in SQLite and replayed. |

## Not captured (unsupported, never fabricated)

- Model-call spans, token usage and cost. The hooks do not expose them.
- Tool exit status and pass/fail.
- Prompts, tool arguments, tool output, assistant text and transcripts. These
  are redacted before SQLite.
- Exit status of unwrapped `codex exec` runs.
- `codex exec resume` of an already-finalized session. The completed run is not
  reopened.

### Upgrade path: tokens through Codex OpenTelemetry

Codex can export OpenTelemetry natively through `[otel]` in
`~/.codex/config.toml` (`exporter = { otlp-http = { … } }`). That export
includes `codex.turn.token_usage.*` metrics and logs carrying an `originator`
attribute.

Ingesting it would add real per-turn token counts for all surfaces without
fabrication. It is deliberately **not** enabled yet: it changes Codex config,
and it needs an OTel log/metric mapper in the collector.

## Tests

- **Fixture tests:**
  - `packages/collector/tests/test_codex_lifecycle.py`
  - `test_codex_surface.py`
  - `test_install.py`
  - `test_doctor.py`
  - `apps/api/tests/test_codex_surfaces.py`
  - `apps/web/tests/source-label.test.mjs`
- **Live:** `NM5_TEST_CODEX=1 uv run pytest packages/collector/tests/test_live_codex.py`
  runs a real `codex exec` against a temporary `CODEX_HOME`, using the trust
  bypass. It asserts `codex-exec`, Completed and at least one tool span.
- **Acceptance fixture:** `examples/codex-order-fixture/` has three files and
  two failing tests. Copy it to a scratch directory before each run.

**Acceptance prompt (CLI, exec and IDE panel):**

> Run `python3 -m unittest -q`, fix the two failing tests with the smallest
> change to the source (not the tests), rerun until they pass, then add one line
> to README.md describing the fix. Be brief.
