# Local run-history management

`nm5-history` is an **OS-authenticated local CLI**, not an HTTP administration API.
The web app has no history-management page and no reset endpoint; it cannot
delete data and never receives a secret. The CLI requires the current OS account to own a private
0700 management directory and its 0600 state file, explicit development opt-in,
loopback API/ClickHouse configuration, and an interactive confirmation phrase.
ClickHouse authentication uses the existing local configuration; credentials are
never printed, returned to a browser, embedded in JavaScript, or copied into reset
state. This does not defend against a process already running as your OS account.

## Supported deployment

Use only the single-account local-development deployment with **all writers on
this machine using this checkout/version**. Do not enable it for production,
shared ClickHouse, remote collectors, or other direct database writers. File locks
coordinate the registered local API and collectors; they are not a distributed
lock for external SQL clients. Stop direct SDK producers/agent jobs as well.
The normal application is unchanged until management is explicitly initialized.

All processes must use the same `NM5_HISTORY_HOME` (default
`~/.nomore500s/history`). Once initialized, omitting the enable flag does not
bypass runtime protection. Do not delete, roll back, or relocate this directory:
its generation and deny identities are safety metadata, not disposable history.

## One-time registration

Stop the API and collectors. Keep your existing ClickHouse connection settings
in `.env`; do not pass passwords in command arguments. Run from the repository:

```sh
export NM5_LOCAL_HISTORY_ENABLED=1
export NM5_API_HOST=127.0.0.1
uv run nm5-history init --collector-store "$HOME/.nomore500s/collector/collector.sqlite"
```

Supply `--collector-store` once for **each** local collector SQLite file targeting
this database. The CLI accepts only existing, account-owned files with the known
collector schema, and never discovers or deletes unknown files automatically.
Review these paths yourself; do not register a store belonging to another target.
The database and collector registration cannot be overwritten by running init again.
Adding stores to an initialized reset domain is intentionally unsupported.

Restart the API on `127.0.0.1` and each registered collector once with the same
history directory. Their durable activation records prove this version's writer
locks are installed. An unregistered collector refuses to start in this domain.
The collector's `NM5_API_URL` must match the registered
`http://127.0.0.1:<NM5_API_PORT>` exactly. Then stop them again before clearing.

## Clear

```sh
uv run nm5-history status
uv run nm5-history clear
```

Review the database, store paths and record counts. Type exactly
`CLEAR LOCAL RUN HISTORY` at the prompt. There is no CLI `--yes` shortcut.
A failed confirmation deletes nothing. The enable flag and loopback settings are
required for every clear/recovery invocation.

The CLI refuses while any managed API/collector holds its lifetime lock, while
a collector store is locked, or while the configured API port is listening.
These lifetime locks cover in-flight ingestion and delivery, not merely health
checks. Collector shutdown waits for the delivery worker to finish.

Only `traces`, `spans`, and `run_events` in the explicitly registered database are
truncated. Diagnostics are derived from these records, not a separate persistent
history table. SQLite pending payloads, sessions, deliveries, occurrences, open
spans and identity mappings are cleared. Schemas, SQLite version metadata,
unknown tables, hooks, credentials, app configuration, integrations and unrelated
databases remain. No Docker volumes are removed. SQLite uses secure deletion and
a WAL checkpoint; this is not a promise of forensic erasure from filesystem backups.

## Recovery and audit

There is **no transaction spanning ClickHouse and SQLite**. The private state file
is atomically replaced and fsynced before destructive work. It records a durable
monotonic generation, reset boundary, operation ID, counts, and phase:

1. `prepared`: deny identities captured; maintenance is active.
2. `collectors_cleared`: registered SQLite commits completed and were verified.
3. `ready`: all ClickHouse tables verified empty; completion recorded.

A crash or failure leaves the operation recoverable. API and collector startup
refuse while the phase is not ready. Fix the storage problem, keep writers
stopped, then run `uv run nm5-history clear` again and confirm. Recovery reuses the
same generation and repeats idempotent deletion and verification. Never manually
set the phase to ready or erase state to bypass recovery.

The latest 20 successful operation summaries are retained; the in-progress
operation is always retained. Payloads and credentials are not in the journal.
Restart the API/collectors after success. API responses expose only the nonsecret
history generation; open web pages invalidate their state on the next response,
notify other tabs, and reject older in-flight results. With a stopped API, the UI
cannot learn of completion until it restarts; the normal polling interval applies.

## New-session requirement and retention

The reset keeps cumulative **namespace-aware deny filters** for cleared session,
conversation, and trace identities. A fixed 128 KiB filter per domain/store is
retained indefinitely. It can produce conservative false positives; it never
forgets a previously denied identity. Repeated resets merge into the same filter.
Journal cleanup cannot reauthorize old conversations. Saturation rejects more
new identities rather than allowing old ones.

After reset, an already-cleared conversation is rejected even if it sends another
session-start event. Unknown identities must supply an explicit provider
session-creation event (`sessionStart` / `SessionStart`) and one of:

1. An original, timezone-aware `session_created_at` later than the reset
   boundary and no later than now (clock skew of 5 seconds is allowed).
2. A hook-stamped `_nm5_creation_observed_at` recorded when the local
   observe-only hook actually ran for that creation event, not older than 120
   seconds, and later than the boundary.

If both are present, `session_created_at` is preferred so a delayed hook cannot
relabel an old conversation as new. Codex `SessionStart` is admitted only when
`source` is `startup` or `new`; `resume` and `inferred` fail closed. Missing
identity, missing creation evidence, stale or delayed starts, ordinary
continuation events, inferred starts, and OTLP-only sessions are rejected.
Accepted fresh sessions can subsequently deliver normal events and survive
collector restart.

**Integration limitation:** Cursor and Codex do not document an original
conversation-creation timestamp. Freshness therefore rests on observing their
documented session-creation hook locally. It is undocumented whether Cursor
re-fires `sessionStart` on window reload; a pre-boundary conversation that was
never collected could be admitted with a start time equal to its
re-observation. A conversation whose identity is already in the deny filter
can never resume collection. Do not fabricate `session_created_at` or delete
the reset boundary. Collector hook logs record a `reason=` code
(`denied_identity`, `not_session_start`, `missing_creation_evidence`,
`stale_creation_evidence`, `untrusted_session_source`, `missing_identity`,
`filter_unavailable`) so rejection is distinguishable from delivery failure.

## Testing without touching real history

Run unit tests with `make check`. Run `make test-clickhouse` for live tests; the
runner creates a separate temporary database from `infra/clickhouse/init.sql`,
sets `NM5_TEST_CLICKHOUSE=1`, and removes only that test database afterward. The destructive live
reset test asserts that its database name starts with `nm5_test_`. Never run it
against your ordinary database. Browser QA can use a separate API/preview and
`npm --prefix apps/web run test:browser`; see the Playwright configuration for
`NM5_PREVIEW_URL`, `NM5_CHROME_PATH`, and `NM5_BROWSER_OUTPUT`.
