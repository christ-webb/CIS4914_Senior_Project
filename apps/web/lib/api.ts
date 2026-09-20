import type { Trace, TraceSummary } from "@/lib/types";

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

async function apiFetch<T>(path: string): Promise<T> {
  const response = await fetch(`${API_URL}${path}`, { cache: "no-store" });
  if (!response.ok) throw new Error(`API request failed: ${response.status}`);
  return response.json() as Promise<T>;
}

export async function listTraces(): Promise<TraceSummary[]> {
  try {
    return await apiFetch<TraceSummary[]>("/v1/traces");
  } catch {
    return [];
  }
}

export function getTrace(traceId: string): Promise<Trace> {
  return apiFetch<Trace>(`/v1/traces/${traceId}`);
}

