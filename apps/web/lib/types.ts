export type SpanStatus = "unset" | "ok" | "error";
export type SpanType = "agent" | "llm" | "tool" | "workflow";
export type RunLifecycle = "running" | "completed" | "failed" | "interrupted" | "unknown";

export interface Span {
  span_id: string;
  parent_span_id: string | null;
  type: SpanType;
  name: string;
  started_at: string;
  duration_ms: number;
  status: SpanStatus;
  input: Record<string, unknown> | null;
  output: Record<string, unknown> | null;
  attributes: Record<string, unknown>;
  token_count: number;
  cost_usd: number;
}

export interface TraceSummary {
  trace_id: string;
  project_id: string;
  name: string;
  started_at: string;
  duration_ms: number;
  total_tokens: number;
  total_cost_usd: number;
  status: SpanStatus;
  lifecycle?: RunLifecycle;
  last_activity_at?: string | null;
  ended_at?: string | null;
}

export interface Trace extends TraceSummary {
  attributes: Record<string, unknown>;
  spans: Span[];
}

export function traceLifecycle(trace: Pick<TraceSummary, "lifecycle" | "status">): RunLifecycle {
  if (trace.lifecycle) return trace.lifecycle;
  if (trace.status === "ok") return "completed";
  if (trace.status === "error") return "failed";
  return "unknown";
}

export function isActiveLifecycle(lifecycle: RunLifecycle): boolean {
  return lifecycle === "running" || lifecycle === "interrupted";
}
