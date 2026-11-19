import type { Span, SpanStatus, TraceSummary } from "./types.ts";

export interface DashboardStats {
  totalRuns: number;
  errorRuns: number;
  errorRate: number;
  avgLatencyMs: number;
  p95LatencyMs: number;
  totalTokens: number;
  totalCostUsd: number;
  estimatedCostUsd: number;
}

export type StatusFilter = "all" | SpanStatus;

export function percentile(values: number[], p: number): number {
  if (values.length === 0) return 0;
  const sorted = [...values].sort((a, b) => a - b);
  const index = Math.min(sorted.length - 1, Math.ceil((p / 100) * sorted.length) - 1);
  return sorted[Math.max(0, index)];
}

export function isMeasuredTokens(trace: Pick<TraceSummary, "token_status" | "total_tokens">): boolean {
  if (trace.token_status === "reported") return true;
  if (trace.token_status === "unavailable" || trace.token_status === "unknown") return false;
  return (trace.total_tokens ?? 0) > 0;
}

export function isMeasuredCost(trace: Pick<TraceSummary, "cost_status" | "total_cost_usd">): boolean {
  if (trace.cost_status === "reported") return true;
  if (trace.cost_status === "unavailable" || trace.cost_status === "unknown") return false;
  return (trace.total_cost_usd ?? 0) > 0;
}

export function isCompleteEstimate(
  trace: Pick<TraceSummary, "estimated_cost_status" | "estimated_cost_usd">,
): boolean {
  return trace.estimated_cost_status === "complete" && trace.estimated_cost_usd != null;
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
    totalTokens: traces.filter(isMeasuredTokens).reduce((total, trace) => total + trace.total_tokens, 0),
    totalCostUsd: traces.filter(isMeasuredCost).reduce((total, trace) => total + trace.total_cost_usd, 0),
    estimatedCostUsd: traces
      .filter(isCompleteEstimate)
      .reduce((total, trace) => total + (trace.estimated_cost_usd ?? 0), 0),
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

export function formatCostLabel(
  usd: number,
  costStatus?: string | null,
): string {
  if (costStatus === "reported") return formatCost(usd);
  if (!costStatus && usd > 0) return formatCost(usd);
  return "Not reported";
}

export function formatEstimatedCostLabel(
  usd?: number | null,
  status?: string | null,
): string {
  if (status === "complete" && usd != null) return formatCost(usd);
  if (status === "partial" && usd != null) return `${formatCost(usd)} (partial)`;
  return "Estimate unavailable";
}

export function formatBilledCostLabel(
  usd: number,
  costStatus?: string | null,
): string {
  if (costStatus === "reported") return formatCost(usd);
  if (!costStatus && usd > 0) return formatCost(usd);
  return "Not reported";
}

export function formatTokenLabel(
  tokens: number,
  tokenStatus?: string | null,
): string {
  if (tokenStatus === "reported") return tokens.toLocaleString();
  if (!tokenStatus && tokens > 0) return tokens.toLocaleString();
  return "Not reported";
}

export function spanUsageStatus(span: Span, kind: "token" | "cost", fallback?: string | null): string | null {
  const key = kind === "token" ? "nm5.token_status" : "nm5.cost_status";
  const value = span.attributes?.[key];
  if (typeof value === "string" && value) return value;
  return fallback ?? null;
}

export function formatCompact(value: number): string {
  return new Intl.NumberFormat("en", { notation: "compact", maximumFractionDigits: 1 }).format(value);
}
