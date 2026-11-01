import type { Trace, TraceSummary } from "@/lib/types";

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

async function apiFetch<T>(path: string): Promise<T> {
  const response = await fetch(`${API_URL}${path}`, { cache: "no-store" });
  if (!response.ok) throw new Error(`API request failed: ${response.status}`);
  return response.json() as Promise<T>;
}

export function fetchTraces(): Promise<TraceSummary[]> {
  return apiFetch<TraceSummary[]>("/v1/traces");
}

export function getTrace(traceId: string): Promise<Trace> {
  return apiFetch<Trace>(`/v1/traces/${traceId}`);
}

const FULL_TRACE_LIMIT = 60;

// The API has no bulk endpoint yet, so fetch the most recent traces individually.
export async function fetchFullTraces(limit = FULL_TRACE_LIMIT): Promise<Trace[]> {
  const summaries = await fetchTraces();
  const recent = [...summaries]
    .sort((a, b) => Date.parse(b.started_at) - Date.parse(a.started_at))
    .slice(0, limit);
  const traces = await Promise.all(recent.map((summary) => getTrace(summary.trace_id).catch(() => null)));
  return traces.filter((trace): trace is Trace => trace !== null);
}
