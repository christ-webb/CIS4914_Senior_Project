# Dashboard refinement verification

## Confirmed causes and changes

The dashboard rendered every active/idle row before its metrics. CSS allowed
numeric values to wrap anywhere, while decorative charts competed for space.
Metrics now precede five-row Active and Recently Completed previews and a
three-row Idle/Open preview. Six cards use compact, single-line tabular values
and accessible disclosures. Loading is distinct from a measured zero.

The cost card and statistics helper previously accepted only complete point
estimates. Historical batch summaries also discarded attributes, losing pricing
and source metadata. Summaries now preserve that metadata; shared aggregation
selects one contribution per distinct run (or latest snapshot of an explicitly
identified source/project/conversation), with point estimates taking precedence
above scenarios and partial subtotals. Actual billed charges are never included.
USD subtotals, exact amounts, source, scope, and incomplete coverage are disclosed.

The read-only stored-data inspection found 190 records, no explicit fixture
markers, and six supported pricing scenarios. Their combined subtotal is
$15.368278–$30.736556, displayed as **$15.37–$30.74 · priced subset, 6/190 runs**.
Matching names or equal prices are not evidence of duplicate logical runs.
Historical records lacking provenance remain included. Future demo telemetry
and the integration payload helper have explicit boolean fixture markers.

The frontend previously considered non-Cursor interrupted runs active. Activity
now requires backend `running` plus `executing`; reconstruction applies the
configured inactivity cutoff and does not refresh stale execution merely because
an old event was replayed. Late results belonging to completed generations do not
reopen execution. Explicit turn completion remains idle without session
finalization. Recently Completed uses terminal lifecycles and completion times,
with cancelled, failed, and completed kept distinct. No lifecycle history was
rewritten. The observed failure-rate denominator is unchanged and disclosed;
cancelled outcomes are no longer counted as unknown in the run card.

Sidebar branding/footer indicators were removed. The hamburger switches between
an expanded desktop sidebar and icon rail, persists the preference for the browser
session, and controls a focus-managed dismissible mobile drawer. All destinations
remain, with Data Management appended. The original design colors are retained.

## Local history management

The Data Management page provides the authenticated CLI procedure, not a
browser-triggered destructive endpoint. Authentication is the local OS account
and its private state files, plus existing ClickHouse authentication. No admin
bearer credential is created for or exposed to the browser.

The opt-in CLI requires stopped registered writers, lifetime locks, store locks,
confirmation, and a durable reset generation. SQLite and ClickHouse are separate
recoverable phases; failure keeps maintenance active. Deletion is limited to
known run tables and registered collector telemetry tables. Verification precedes
success. A bounded operation journal and cumulative fixed-size deny filters
survive cleanup. The frontend observes the nonsecret generation and invalidates
stale responses and tabs after API restart.

See [the operational guide](../../local-history.md) for setup, recovery,
authorization boundaries and restrictions. Native hooks without original
session-creation evidence deliberately fail closed after reset; simply opening a
chat is not sufficient when that integration cannot prove it is new. External
writers to a shared database are outside this local lock protocol, so the feature
must not be enabled in shared/production deployments.

No real history was cleared during this implementation. Live destructive tests
used temporary `nm5_test_...` databases and temporary collector stores only.

## Validation

- `make check`: lint and both typechecks passed; Python 209 passed, 14 opt-in tests skipped.
- Frontend: 56 Node tests and 13 React tests passed, including the existing read-only
  regression against a stored 5,249,214-token Cursor trace.
- `make test-clickhouse`: 19 passed in disposable ClickHouse storage, covering
  persistence, replay, fixture/pricing summaries, deletion and recovery.
- The optional external live-Codex smoke test was not run. The ClickHouse skips
  from the default suite were exercised separately by `make test-clickhouse`.
- Production build passed.
- Playwright: 7 passed; verifies metric geometry at 1440, 1280, 768 and 390 px; navigation,
  fixture and lifecycle classification, exact disclosures, and reset invalidation.
- Rendered screenshots were inspected separately from automated assertions.

Screenshots: [1440](dashboard-1440.png), [1280](dashboard-1280.png),
[768](dashboard-768.png), [390](dashboard-390.png).

## Files changed for this milestone

Existing uncommitted work was preserved. The implementation touches these groups:

- API: `api/routes/traces.py`, `domain/models.py`, `main.py`,
  `services/run_reconstruction.py`, `services/trace_service.py`,
  `services/clickhouse_trace_repository.py`, new `history.py`, and API package metadata.
- Collector: `history_guard.py`, `pipeline.py`, `server.py`, and `hook.py`.
- Web: dashboard, analytics, Explorer, shell, page-header, Settings, global styles,
  polling, API/history/types/analytics helpers, and new cost aggregation utility.
- Tests: dashboard summary, lifecycle reconstruction, local reset, live ClickHouse,
  cost aggregation, existing graph/history expectations, and Playwright config/browser tests.
- Tooling/docs: `Makefile`, `scripts/test_clickhouse.py`, package lockfiles,
  demo provenance markers, `README.md`, `docs/local-history.md`, and this evidence.

Existing pricing-engine changes, graph redesign work, and their tests were retained;
this milestone does not introduce new rates or pricing assumptions.
