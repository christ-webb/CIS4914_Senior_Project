import { buildTraceListPath, type TraceListParams } from "@/lib/history";
import type { DiagnosticSummary, Trace, TraceDiagnostics, TraceSummary } from "@/lib/types";

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

let historyGeneration: string | null = null;
async function apiFetch<T>(path: string): Promise<T> {
  const response = await fetch(`${API_URL}${path}`, { cache: "no-store" });
  const generation = response.headers.get("X-NM5-History-Generation");
  if (generation !== null) {
    if (historyGeneration !== null && Number(generation) < Number(historyGeneration)) throw new Error("Stale pre-reset response");
    if (historyGeneration !== null && historyGeneration !== generation && typeof window !== "undefined") {
      window.dispatchEvent(new Event("nm5-history-reset"));
      const channel = typeof BroadcastChannel !== "undefined" ? new BroadcastChannel("nm5-history") : null;
      channel?.postMessage(generation); channel?.close();
    }
    historyGeneration = generation;
  }
  if (!response.ok) throw new Error(`API request failed: ${response.status}`);
  return response.json() as Promise<T>;
}

export function fetchTraces(params: TraceListParams = {}): Promise<TraceSummary[]> {
  return apiFetch<TraceSummary[]>(buildTraceListPath(params));
}

export async function checkHistoryGeneration(): Promise<void> {
  await apiFetch<{ status: string }>("/health");
}

export function fetchProjects(): Promise<string[]> {
  return apiFetch<string[]>("/v1/projects");
}

export function getTrace(traceId: string): Promise<Trace> {
  return apiFetch<Trace>(`/v1/traces/${traceId}`);
}

export function getTraceDiagnostics(traceId: string): Promise<TraceDiagnostics> {
  return apiFetch<TraceDiagnostics>(`/v1/traces/${traceId}/diagnostics`);
}

export function fetchDiagnosticSummary(limit = 25): Promise<DiagnosticSummary> {
  return apiFetch<DiagnosticSummary>(`/v1/diagnostics?fixtures=exclude&limit=${limit}`);
}

const FULL_TRACE_LIMIT = 60;

// The API has no bulk endpoint yet, so fetch the most recent traces individually.
export async function fetchFullTraces(limit = FULL_TRACE_LIMIT): Promise<Trace[]> {
  const summaries = await fetchTraces({ limit });
  const traces = await Promise.all(summaries.map((summary) => getTrace(summary.trace_id).then(trace => ({ ...summary, ...trace })).catch(() => null)));
  return traces.filter((trace): trace is Trace => trace !== null);
}
