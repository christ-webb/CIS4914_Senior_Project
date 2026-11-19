import type { RunLifecycle, SpanStatus } from "./types.ts";

export interface TraceListParams {
  fixtures?: "exclude" | "only" | "include";
  activity?: "active" | "idle" | "terminal" | "completed_turns";
  project_id?: string;
  status?: SpanStatus;
  lifecycle?: RunLifecycle;
  source?: string;
  provider?: string;
  agent_framework?: string;
  started_after?: string;
  started_before?: string;
  limit?: number;
  offset?: number;
}

export function buildTraceListPath(params: TraceListParams = {}): string {
  const search = new URLSearchParams();
  search.set("fixtures", params.fixtures ?? "exclude");
  if (params.activity) search.set("activity", params.activity);
  if (params.project_id) search.set("project_id", params.project_id);
  if (params.status) search.set("status", params.status);
  if (params.lifecycle) search.set("lifecycle", params.lifecycle);
  if (params.source) search.set("source", params.source);
  if (params.provider) search.set("provider", params.provider);
  if (params.agent_framework) search.set("agent_framework", params.agent_framework);
  if (params.started_after) search.set("started_after", params.started_after);
  if (params.started_before) search.set("started_before", params.started_before);
  if (params.limit != null) search.set("limit", String(params.limit));
  if (params.offset) search.set("offset", String(params.offset));
  const query = search.toString();
  return query ? `/v1/traces?${query}` : "/v1/traces";
}

export function paginateWindow<T>(rows: T[], pageSize: number): { items: T[]; hasMore: boolean } {
  return { items: rows.slice(0, pageSize), hasMore: rows.length > pageSize };
}

export type HistoryEmptyKind = "unavailable" | "empty" | "no-match" | null;

export function historyEmptyKind(options: {
  online: boolean;
  loading?: boolean;
  itemCount: number;
  hasFilters: boolean;
}): HistoryEmptyKind {
  if (options.loading) return null;
  if (!options.online && options.itemCount === 0) return "unavailable";
  if (options.itemCount > 0) return null;
  return options.hasFilters ? "no-match" : "empty";
}

export function hasActiveFilters(params: TraceListParams): boolean {
  return Boolean(
    params.project_id
    || params.status
    || params.lifecycle
    || params.source
    || params.provider
    || params.agent_framework
    || params.started_after
    || params.started_before,
  );
}
