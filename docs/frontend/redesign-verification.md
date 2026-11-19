# Frontend redesign and verification

## Implemented design

The existing Next.js routes now share white cards, a light-gray `#F3F5F8`
background, structural blue `#0021A5`, restrained orange `#FA4616`, and charcoal
`#243044`. Semantic red, green, and amber remain distinct. Bold blue sentence-case
headings, thin borders, small corner radii, whitespace, and blue selected navigation
adapt the supplied Patch references to a developer dashboard. Marketing page
compositions, logos, illustrations, gradients, and giant uppercase display text
were not copied.

Dashboard, Explorer, Errors, Analytics, and trace detail use the shared palette and
controls. Dashboard leads with executing and idle sessions. Explorer preserves
server filters and paging and shares the full interactive trace viewer. Errors
keeps investigation links and expandable evidence. Analytics retains separate
complete-estimate and billed aggregates and lists scenario/partial estimates per
run without aggregating them as verified costs.

## Trace information hierarchy

Four primary metrics: Duration, Total tokens, Estimated cost, Failed operations.
Token accounting, scope, context occupancy, pricing details, actual billed cost,
and run identifiers are available through intentional disclosures. There is no
permanent token-breakdown section. Expanded diagnostics follow the execution graph;
findings/evidence and Failure focus remain reachable above it.

Cost presentation uses existing data in this order:

1. Complete verified estimate, including a valid zero.
2. Both supported scenario endpoints, displayed as a range.
3. A reliable partial subtotal with an explicit partial qualifier.
4. A single available scenario, explicitly labeled as a scenario; no missing
   endpoint is fabricated.
5. Not available when no defensible amount exists.

Scenarios never imply bounds on actual bills. Partial subtotals do not use a plus
sign or claim to be lower bounds. Currency is USD, with additional precision for
small values. Optional `nm5.pricing_details` span attributes expose existing
estimator rates, source/date, scenario amounts, and exclusions. This is additive
presentation metadata: calculation formulas, verification status, token accounting,
collector behavior, and storage schemas are unchanged.

Session elapsed windows are distinguished from active operation duration. Zero
hook/parent placeholders show “Not reported”; explicit inferred provenance is
labeled. The inspector explains timing limitations. Missing hook timing does not
dilute recorded-duration averages. Group timing remains a sum, not wall-clock time.

## Accessibility and interaction

- Hover, focus, click/tap, Escape, outside dismissal, and focus restoration for
  metric disclosures; placement is constrained to the available viewport.
- Visible focus, keyboard graph-mode navigation, accessible group expansion,
  reduced-motion handling, and responsive metric/navigation layouts.
- Wrapping and expandable JSON/error inspection; wide tables scroll locally.
- Refresh progress/success/failure feedback and concise Start monitoring guidance.
- Chart units, endpoint labels, accessible value lists, and sparse-data messaging.
- Graph identities, relationship semantics, modes, selection, group expansion,
  and camera stability during polling are retained.

## Automated and stored-data verification

Verified on 2026-11-19:

- `make check`: passed; 198 Python tests passed, 12 opt-in tests skipped;
  50 frontend Node tests passed; 12 component tests passed, one stored-data test
  skipped by default. Includes lint and Python/TypeScript type checks.
- Live ClickHouse, trace integration regression, and pricing suites: 25 passed
  with `NM5_TEST_CLICKHOUSE=1`.
- Stored-data frontend run: 50 Node tests and 13 component tests passed, including
  the opt-in stored Cursor trace check.
- Production `npm --prefix apps/web run build`: passed for all routes.
- All five application routes returned HTTP 200 from the existing local server.
- `git diff --check`: passed.

The existing ClickHouse container was confirmed healthy and the running API and
frontend reused. Read-only API retrieval supplied an actual stored Cursor trace
with 5,249,214 tokens, 102 spans, four failed operations, parent-only usage,
collapsible tool groups, and unverified Standard/Fast pricing scenarios of
$3.301884 and $6.603768. Component validation confirmed the primary metric displays
$3.30–$6.60, exact token details remain accessible, group expansion survives a poll,
and Failure focus works. Updated API responses also exposed scenario rates for
the trace's two measured model generations. Private trace payloads remain outside
the repository.

To repeat that opt-in check, fetch a suitable trace into a local temporary file and
run `NM5_VALIDATION_TRACE=/absolute/path/to/trace.json npm --prefix apps/web test`.
The default suite does not require access to private telemetry.

## Outstanding visual acceptance

Actual rendered-screen inspection is **not completed**. Computer-use browser
selection returned “No browser is available”; creating an in-app browser also
returned “Browser is not available: iab.” HTTP checks and jsdom tests do not verify
rendered layout, contrast, camera appearance, or screenshot fidelity.

A browser-connected session must still inspect 1440px, 1280px, 768px, and 390px
viewports, compare the rendered pages against the supplied Patch/Mobbin references,
and capture evidence. In particular, verify the graph appears in the initial
laptop viewport, node/group legibility, floating disclosure placement, mobile
overflow, and blue/white/orange balance. The redesign should not be considered
visually accepted until those checks pass.

Existing non-failing tool warnings remain: Python dependency deprecations, Node
module-type detection, and Vitest's future configuration-loader warning.
