import { currency, numeric } from "./presentation.ts";
import type { TraceSummary } from "./types.ts";

export function compactCost(value: number): string {
  if (value > 0 && value < 0.01) return `$${value.toExponential(1)}`;
  if (value >= 10000) return new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", notation: "compact", maximumFractionDigits: 2 }).format(value);
  return currency(value);
}

/** The window is deduplicated before counting either usage or coverage. Unknown provenance stays. */
export function ordinaryRuns<T extends TraceSummary>(rows: T[]): T[] {
  const unique = new Map<string, T>();
  const time = (row: T) => Date.parse(row.last_activity_at ?? row.ended_at ?? row.started_at) || 0;
  for (const row of rows) {
    if (row.is_fixture === true) continue;
    const key = row.logical_run_id ? `logical:${row.logical_run_id}` : `trace:${row.trace_id}`;
    const prior = unique.get(key);
    if (!prior || time(row) > time(prior)) unique.set(key, row);
  }
  return [...unique.values()];
}

export function aggregateEstimatedCost(rows: TraceSummary[]) {
  const runs = ordinaryRuns(rows);
  let minimum = 0, maximum = 0, priced = 0, complete = 0, scenario = false;
  const contributions: { traceId: string; minimum: number; maximum: number; basis: string; scope: string; source: string | null; complete: boolean }[] = [];
  for (const run of runs) {
    const point = numeric(run.estimated_cost_usd);
    const standard = numeric(run.scenario_standard_usd), fast = numeric(run.scenario_fast_usd);
    const partial = numeric(run.estimated_subtotal_usd) ?? (run.estimated_cost_status === "partial" ? point : null);
    let low: number | null = null, high: number | null = null;
    let covered = false;
    if (run.estimated_cost_status === "complete" && point !== null) {
      low = high = point; covered = true;
    } else if (standard !== null && fast !== null) {
      low = Math.min(standard, fast); high = Math.max(standard, fast); scenario = true;
      covered = run.total_generations != null && run.total_generations > 0 && run.covered_generations === run.total_generations;
    } else if (partial !== null) {
      low = high = partial;
    } else if (standard !== null || fast !== null) {
      low = high = standard ?? fast; scenario = true;
    }
    if (low === null || high === null) continue;
    minimum += low; maximum += high; priced += 1;
    if (covered) complete += 1;
    contributions.push({ traceId: run.trace_id, minimum: low, maximum: high, basis: run.estimate_basis ?? "Published model pricing", scope: run.usage_scope ?? "Scope not reported", source: run.pricing_source ?? null, complete: covered });
  }
  const fullCoverage = runs.length > 0 && complete === runs.length;
  return {
    minimum, maximum, priced, total: runs.length, complete, fullCoverage, scenario, currency: "USD" as const, contributions,
    label: !priced ? "Not available" : minimum === maximum ? compactCost(minimum) : `${compactCost(minimum)}–${compactCost(maximum)}`,
    coverage: !priced ? "No supported estimate" : fullCoverage ? "Complete window coverage" : `Priced subset · ${priced}/${runs.length} runs`,
  };
}
