export type SpanStatus = "unset" | "ok" | "error";
export type SpanType = "agent" | "llm" | "tool" | "workflow";

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
}

export interface Trace extends TraceSummary {
  attributes: Record<string, unknown>;
  spans: Span[];
}

