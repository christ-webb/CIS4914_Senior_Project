import { buildTraceListPath, type TraceListParams } from "@/lib/history";
import type { Trace, TraceSummary } from "@/lib/types";

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

async function apiFetch<T>(path: string): Promise<T> {
  const response = await fetch(`${API_URL}${path}`, { cache: "no-store" });
  if (!response.ok) throw new Error(`API request failed: ${response.status}`);
  return response.json() as Promise<T>;
}

export function fetchTraces(params: TraceListParams = {}): Promise<TraceSummary[]> {
  return apiFetch<TraceSummary[]>(buildTraceListPath(params));
}

export function fetchProjects(): Promise<string[]> {
  return apiFetch<string[]>("/v1/projects");
}

export function getTrace(traceId: string): Promise<Trace> {
  return apiFetch<Trace>(`/v1/traces/${traceId}`);
}

const FULL_TRACE_LIMIT = 60;

// The API has no bulk endpoint yet, so fetch the most recent traces individually.
export async function fetchFullTraces(limit = FULL_TRACE_LIMIT): Promise<Trace[]> {
  const summaries = await fetchTraces({ limit });
  const traces = await Promise.all(summaries.map((summary) => getTrace(summary.trace_id).catch(() => null)));
  return traces.filter((trace): trace is Trace => trace !== null);
}
