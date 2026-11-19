export type SpanStatus = "unset" | "ok" | "error";
export type SpanType = "agent" | "llm" | "tool" | "workflow";
export type RunLifecycle = "running" | "completed" | "failed" | "cancelled" | "interrupted" | "unknown";
export type ExecutionState = "executing" | "idle" | "unknown";

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
  execution_state?: ExecutionState;
  last_activity_at?: string | null;
  ended_at?: string | null;
  source?: string | null;
  agent_framework?: string | null;
  provider?: string | null;
  workspace?: string | null;
  cost_status?: string | null;
  token_status?: string | null;
  usage_scope?: string | null;
  instrumentation_coverage?: string | null;
  estimated_cost_usd?: number | null;
  estimated_cost_status?: string | null;
  estimated_subtotal_usd?: number | null;
  model?: string | null;
  model_variant?: string | null;
  estimate_basis?: string | null;
  scenario_standard_usd?: number | null;
  scenario_fast_usd?: number | null;
  covered_generations?: number | null;
  total_generations?: number | null;
}

export interface Trace extends TraceSummary {
  attributes: Record<string, unknown>;
  spans: Span[];
}

export type FindingSeverity = "info" | "warning" | "critical";
export type FindingConfidence = "confirmed" | "heuristic";
export type DetectionType =
  | "tool_failure"
  | "repeated_tool_call"
  | "schema_mismatch"
  | "ineffective_recovery"
  | "stall"
  | "interruption"
  | "provider_error"
  | "latency_anomaly"
  | "token_anomaly"
  | "cost_anomaly";

export interface DiagnosticFinding {
  finding_id: string;
  trace_id: string;
  span_ids: string[];
  detector_id: string;
  detection_type: DetectionType;
  severity: FindingSeverity;
  confidence: FindingConfidence;
  title: string;
  explanation: string;
  evidence: string[];
  evidence_data: Record<string, unknown>;
  suggested_action: string;
  first_observed_at: string | null;
  last_observed_at: string | null;
  detector_version: string;
  provisional: boolean;
}

export interface InstrumentationCoverage {
  token_status: string;
  cost_status: string;
  instrumentation_coverage: string | null;
  unsupported_detectors: string[];
  insufficient_data_detectors: string[];
}

export interface TraceDiagnostics {
  trace_id: string;
  analysis_status: "finalized" | "provisional";
  coverage: InstrumentationCoverage;
  findings: DiagnosticFinding[];
}

export interface DiagnosticSummary {
  analysis_scope: {
    limit: number;
    offset: number;
    project_id: string | null;
    analyzed_run_count: number;
    newest_started_at: string | null;
    oldest_started_at: string | null;
    note: string;
  };
  totals_by_severity: Record<string, number>;
  totals_by_type: Record<string, number>;
  affected_trace_count: number;
  recent_findings: DiagnosticFinding[];
  coverage_notes: string[];
}

export function traceLifecycle(trace: Pick<TraceSummary, "lifecycle" | "status">): RunLifecycle {
  if (trace.lifecycle) return trace.lifecycle;
  if (trace.status === "ok") return "completed";
  if (trace.status === "error") return "failed";
  return "unknown";
}

export function executionState(
  trace: Pick<TraceSummary, "execution_state"> | ExecutionState | null | undefined,
): ExecutionState {
  if (typeof trace === "string") return trace;
  return trace?.execution_state ?? "unknown";
}

type CursorHint = Pick<TraceSummary, "source" | "agent_framework"> & {
  attributes?: Record<string, unknown>;
};

export function isCursorTrace(trace: CursorHint): boolean {
  if (trace.source === "cursor" || trace.agent_framework === "cursor") return true;
  const attrs = trace.attributes;
  if (!attrs) return false;
  return (
    attrs["nm5.source"] === "cursor" ||
    attrs["agent.framework"] === "cursor" ||
    attrs["nm5.agent"] === "cursor"
  );
}

export function isExecutingRun(
  trace: Pick<TraceSummary, "lifecycle" | "status" | "execution_state" | "source" | "agent_framework"> & {
    attributes?: Record<string, unknown>;
  },
): boolean {
  if (executionState(trace) === "idle") return false;
  const lifecycle = traceLifecycle(trace);
  if (isCursorTrace(trace)) {
    return lifecycle === "running" && executionState(trace) === "executing";
  }
  return lifecycle === "running" || lifecycle === "interrupted";
}

export function isIdleOpenRun(
  trace: Pick<TraceSummary, "lifecycle" | "status" | "execution_state" | "source" | "agent_framework"> & {
    attributes?: Record<string, unknown>;
  },
): boolean {
  return isCursorTrace(trace) && traceLifecycle(trace) === "running" && executionState(trace) === "idle";
}

export function isActiveLifecycle(lifecycle: RunLifecycle): boolean {
  return lifecycle === "running" || lifecycle === "interrupted";
}

export function needsLivePolling(trace: Pick<TraceSummary, "lifecycle" | "status" | "execution_state" | "source" | "agent_framework">): boolean {
  return isExecutingRun(trace);
}
