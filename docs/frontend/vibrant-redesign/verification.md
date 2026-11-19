# Vibrant editorial redesign: rendered verification

Screenshots in this folder were rendered from a production build (`next build`
plus `next start` on `127.0.0.1:3001`). The build ran against a read-only
second API instance on port 8001 that read the real local ClickHouse history.
Regenerate them with:

```sh
NM5_PREVIEW_URL=http://127.0.0.1:3001 NM5_TRACE_ID=<trace id> \
  node apps/web/scripts/capture-screens.mjs docs/frontend/vibrant-redesign
```

| Page | 1440 | 1280 | 768 | 390 |
| --- | --- | --- | --- | --- |
| Dashboard | `dashboard-1440.png` | `dashboard-1280.png` | `dashboard-768.png` | `dashboard-390.png` |
| Explorer | `explorer-1440.png` | `explorer-1280.png` | `explorer-768.png` | `explorer-390.png` |
| Analytics | `analytics-1440.png` | `analytics-1280.png` | `analytics-768.png` | `analytics-390.png` |
| Errors | `errors-1440.png` | `errors-1280.png` | `errors-768.png` | `errors-390.png` |
| Trace detail | `trace-1440.png` | `trace-1280.png` | `trace-768.png` | `trace-390.png` |

Interaction states are in `state-hover-1440.png` (row hover shows a gray fill,
an orange bar, and the arrow shifted), `state-collapsed-1440.png` (icon rail),
and `state-drawer-390.png` (mobile drawer).

## Checked on every render

- The single `h1` is the one-word page name, set in Barlow Condensed, with no
  subtitle.
- `document.scrollWidth - innerWidth` is 0 on all 20 renders, so there is no
  horizontal overflow.
- The trace detail sample (`nm5-codex-test`) shows the README case after the
  chat closed: session **Cancelled**, turn **Completed**, task **Unverified**.

## Issues found in screenshots and fixed

- Durations rendered as `2.5Ks` or `3534.19 s`. They now read `43m 18s` and
  `58m 54s`.
- At 1280px the five-column KPI band shrank values to about 22px. The
  dashboard now uses a two-row composition with a large blue Runs cell.
- Span-type pills wrapped mid-word (`to/ol`). Tables keep a minimum width and
  scroll horizontally inside their panel on narrow screens.
- Session and turn pills both read `Executing`. Turn labels now read
  `Turn executing`, `Turn completed`, and so on.
- A trace with no cost showed `Not available` overflowing its metric cell.
  Values are now sized to the cell's width.
- A live Cursor session showed 69 turns because each `afterAgentThought`
  step id opened a new turn. Step ids now resolve to their generation, and a
  regression test covers it.

## Automated checks

Playwright (`npx playwright test`, 13 tests) passes against this preview. It
covers:

- Dashboard geometry at all four widths.
- The panel-left-open and panel-left-close icon swap.
- The absence of a Data Management link.
- Turns versus Sessions in Recently completed.
- Turn completion with no 0% success rate.
- Heading font and word count, and no overflow on every page at every width.
- Hover motion disabled under `prefers-reduced-motion`.
- Exact disclosures.
- Reset-generation invalidation.

## Not automatable

Live Cursor and Codex GUI behavior still needs a manual check: a new chat, then
`stop`, then closing the tab. The provider fixtures and the stored history
above exercise the same event sequences.
