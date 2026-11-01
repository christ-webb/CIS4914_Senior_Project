import type { SpanStatus, TraceSummary } from "./types.ts";

export interface DashboardStats {
  totalRuns: number;
  errorRuns: number;
  errorRate: number;
  avgLatencyMs: number;
  p95LatencyMs: number;
  totalTokens: number;
  totalCostUsd: number;
}

export type StatusFilter = "all" | SpanStatus;

export function percentile(values: number[], p: number): number {
  if (values.length === 0) return 0;
  const sorted = [...values].sort((a, b) => a - b);
  const index = Math.min(sorted.length - 1, Math.ceil((p / 100) * sorted.length) - 1);
  return sorted[Math.max(0, index)];
}

export function computeStats(traces: TraceSummary[]): DashboardStats {
  const totalRuns = traces.length;
  const errorRuns = traces.filter((trace) => trace.status === "error").length;
  const sum = (pick: (trace: TraceSummary) => number) =>
    traces.reduce((total, trace) => total + pick(trace), 0);
  return {
    totalRuns,
    errorRuns,
    errorRate: totalRuns ? errorRuns / totalRuns : 0,
    avgLatencyMs: totalRuns ? sum((trace) => trace.duration_ms) / totalRuns : 0,
    p95LatencyMs: percentile(
      traces.map((trace) => trace.duration_ms),
      95,
    ),
    totalTokens: sum((trace) => trace.total_tokens),
    totalCostUsd: sum((trace) => trace.total_cost_usd),
  };
}

export function statusCounts(traces: TraceSummary[]): Record<SpanStatus, number> {
  const counts: Record<SpanStatus, number> = { ok: 0, error: 0, unset: 0 };
  for (const trace of traces) counts[trace.status] += 1;
  return counts;
}

export function recentTraces(traces: TraceSummary[], limit: number): TraceSummary[] {
  return [...traces]
    .sort((a, b) => Date.parse(a.started_at) - Date.parse(b.started_at))
    .slice(-limit);
}

export function filterTraces(
  traces: TraceSummary[],
  status: StatusFilter,
  query: string,
): TraceSummary[] {
  const needle = query.trim().toLowerCase();
  return traces.filter(
    (trace) =>
      (status === "all" || trace.status === status) &&
      (!needle ||
        trace.name.toLowerCase().includes(needle) ||
        trace.project_id.toLowerCase().includes(needle)),
  );
}

export function formatMs(ms: number): string {
  return ms >= 1000 ? `${(ms / 1000).toFixed(2)} s` : `${ms.toFixed(0)} ms`;
}

export function formatCost(usd: number): string {
  return `$${usd.toFixed(usd >= 1 ? 2 : 4)}`;
}

export function formatCompact(value: number): string {
  return new Intl.NumberFormat("en", { notation: "compact", maximumFractionDigits: 1 }).format(value);
}
