# VS Code GitHub Copilot monitoring

This integration observes **GitHub Copilot** agent sessions in VS Code (shared
Copilot harness). It does **not** automatically monitor Claude, Codex, or other
VS Code agent providers.

## Install (hooks only, default)

```bash
make clickhouse
make api-dev
make collector
uv run nm5-vscode-install-hooks
```

This writes `~/.copilot/hooks/nm5.json` and does not enable OpenTelemetry.
Uninstall:

```bash
uv run nm5-vscode-uninstall-hooks
```

Workspace install is opt-in and refused if the user-level NM5 file already
exists (Copilot runs hooks from every source):

```bash
uv run nm5-vscode-install-hooks --project /path/to/repo
```

## Optional OpenTelemetry

`--enable-otel` captures model/tool spans and token usage that hooks do not
provide. Content capture stays off unless you already authorized it.

```bash
uv run nm5-vscode-install-hooks --enable-otel
```

This merges JSON-with-comments VS Code user `settings.json` keys:

- `github.copilot.chat.otel.enabled`
- `github.copilot.chat.otel.exporterType` = `otlp-http`
- `github.copilot.chat.otel.protocol` = `http/json`
- `github.copilot.chat.otel.otlpEndpoint` = `http://127.0.0.1:8788`
- `github.copilot.chat.otel.headers.Authorization` = `Bearer <collector token>`

The collector exposes OTLP/HTTP at `POST /v1/traces` (JSON, and protobuf when
`opentelemetry-proto` is installed). Copilot appends `/v1/traces` to the
endpoint. Uninstall restores the previous values of those keys.

### Path-token fallback (opt-in)

GUI VS Code usually cannot send `OTEL_EXPORTER_OTLP_HEADERS` from the Dock.
Header settings are the supported auth path. If headers cannot be applied,
`--otlp-path-token` plus collector `NM5_OTLP_PATH_TOKEN=true` accepts
`http://127.0.0.1:8788/otlp/<token>/v1/traces`. Restore files are mode `0600`.
Tokens are not printed in `/health`.

## Correlation

Hook `sessionId` and OTel `gen_ai.conversation.id` are joined only when they
are equal or an explicit mapping is stored. Tool spans join only on exact
`tool_use_id` / `gen_ai.tool.call.id`. Hook-only tools are kept if OTel is
late or missing. Name/time matching is never used.

## Outcomes

| Copilot `reason` | Lifecycle |
| --- | --- |
| `complete` | `completed` |
| `error` | `failed` |
| `timeout` | `failed` (outcome timeout) |
| `abort`, `user_exit` | `cancelled` |

`agentStop` is a turn heartbeat, not session completion.

## Remote / containers / WSL

Hooks and the Copilot exporter run where the agent runtime runs. A collector on
the local laptop is not reachable from a remote container. Unsupported unless
the collector is installed in that same environment.

## Verify

1. Open a disposable folder in VS Code, select Copilot as the session target.
2. Start an agent task that uses a tool.
3. Confirm source `vscode` on Active Runs.
4. With `--enable-otel`, confirm LLM spans and reported tokens when Copilot
   emits them, and a single run (not duplicates).
5. Restart API and collector; the trace remains.

Live GUI verification is marked pending when Copilot cannot be driven from this
environment.
