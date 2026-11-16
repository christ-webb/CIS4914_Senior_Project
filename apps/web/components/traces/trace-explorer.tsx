"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useCallback, useMemo, useState } from "react";

import { EmptyState, LiveStatus, PageHeader } from "@/components/shell/page-header";
import { TraceGraph } from "@/components/traces/trace-graph";
import { usePolling } from "@/hooks/use-polling";
import { fetchProjects, fetchTraces, getTrace } from "@/lib/api";
import {
  hasActiveFilters,
  historyEmptyKind,
  paginateWindow,
  type TraceListParams,
} from "@/lib/history";
import { formatMs } from "@/lib/stats";
import type { SpanStatus, TraceSummary } from "@/lib/types";

const PAGE_SIZE = 20;
const STATUSES: Array<"all" | SpanStatus> = ["all", "ok", "error", "unset"];

interface HistoryPage {
  projects: string[];
  traces: TraceSummary[];
  hasMore: boolean;
}

function readFilters(params: URLSearchParams): TraceListParams {
  const status = params.get("status");
  return {
    project_id: params.get("project") || undefined,
    status: status === "ok" || status === "error" || status === "unset" ? status : undefined,
    started_after: params.get("from") || undefined,
    started_before: params.get("to") || undefined,
  };
}

function datetimeLocalValue(iso: string | undefined): string {
  if (!iso) return "";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "";
  const pad = (value: number) => String(value).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

function toIso(local: string): string | undefined {
  if (!local) return undefined;
  const date = new Date(local);
  return Number.isNaN(date.getTime()) ? undefined : date.toISOString();
}

export function TraceExplorer() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const filters = useMemo(() => readFilters(searchParams), [searchParams]);
  const page = Math.max(1, Number(searchParams.get("page") || "1") || 1);
  const [pickedId, setPickedId] = useState<string | null>(null);

  const loadHistory = useCallback(async (): Promise<HistoryPage> => {
    const [projectNames, rows] = await Promise.all([
      fetchProjects(),
      fetchTraces({
        ...filters,
        limit: PAGE_SIZE + 1,
        offset: (page - 1) * PAGE_SIZE,
      }),
    ]);
    const windowed = paginateWindow(rows, PAGE_SIZE);
    return { projects: projectNames, traces: windowed.items, hasMore: windowed.hasMore };
  }, [filters, page]);

  const { data, online, loading, updatedAt, refresh } = usePolling(loadHistory, 10000);
  const traces = data?.traces ?? [];
  const projects = data?.projects ?? [];
  const hasMore = data?.hasMore ?? false;
  const activeId = traces.some((trace) => trace.trace_id === pickedId)
    ? pickedId
    : (traces[0]?.trace_id ?? null);

  const loadDetail = useCallback(async () => {
    if (!activeId) return null;
    return getTrace(activeId);
  }, [activeId]);
  const detail = usePolling(loadDetail, 10000);

  const replaceParams = useCallback(
    (patch: Record<string, string | undefined>) => {
      const next = new URLSearchParams(searchParams.toString());
      for (const [key, value] of Object.entries(patch)) {
        if (!value) next.delete(key);
        else next.set(key, value);
      }
      const query = next.toString();
      router.replace(query ? `/explorer?${query}` : "/explorer");
    },
    [router, searchParams],
  );

  const emptyKind = historyEmptyKind({
    online,
    loading,
    itemCount: traces.length,
    hasFilters: hasActiveFilters(filters),
  });

  return (
    <div className="classic-explorer">
      <PageHeader
        eyebrow="TRACE EXPLORER"
        title="Historical agent runs"
        subtitle="Browse every ingested execution, then open its full trace graph."
        actions={<LiveStatus online={online} updatedAt={updatedAt} onRefresh={refresh} />}
      />

      <section className="card history-toolbar" aria-label="History filters">
        <label className="filter-field">
          <span className="muted">Project</span>
          <select
            className="search"
            aria-label="Filter by project"
            value={filters.project_id ?? ""}
            onChange={(event) => replaceParams({ project: event.target.value || undefined, page: undefined })}
          >
            <option value="">All projects</option>
            {projects.map((project) => (
              <option key={project} value={project}>
                {project}
              </option>
            ))}
          </select>
        </label>
        <div>
          <span className="muted">Status</span>
          <div className="filters" role="group" aria-label="Filter by status">
            {STATUSES.map((status) => (
              <button
                key={status}
                className={`chip ${(!filters.status && status === "all") || filters.status === status ? "active" : ""}`}
                onClick={() => replaceParams({ status: status === "all" ? undefined : status, page: undefined })}
                type="button"
              >
                {status}
              </button>
            ))}
          </div>
        </div>
        <label className="filter-field">
          <span className="muted">From</span>
          <input
            className="search"
            type="datetime-local"
            aria-label="Started after"
            value={datetimeLocalValue(filters.started_after)}
            onChange={(event) => replaceParams({ from: toIso(event.target.value), page: undefined })}
          />
        </label>
        <label className="filter-field">
          <span className="muted">To</span>
          <input
            className="search"
            type="datetime-local"
            aria-label="Started before"
            value={datetimeLocalValue(filters.started_before)}
            onChange={(event) => replaceParams({ to: toIso(event.target.value), page: undefined })}
          />
        </label>
      </section>

      {emptyKind === "unavailable" ? (
        <EmptyState online={false}>
          <p>The API or ClickHouse store did not return run history. Confirm both are running.</p>
        </EmptyState>
      ) : emptyKind === "empty" ? (
        <EmptyState online>
          <p>No traces have been recorded yet. Run <code>make demo</code>, then refresh.</p>
        </EmptyState>
      ) : emptyKind === "no-match" ? (
        <section className="empty-state">
          <h2>No traces match the current filters</h2>
          <p>Clear a project, status, or time range to see other historical runs.</p>
        </section>
      ) : (
        <section className="explorer">
          <aside className="card run-list" aria-label="Agent runs">
            <h2>Runs</h2>
            <div className="run-scroll">
              {traces.map((trace) => (
                <button
                  className={activeId === trace.trace_id ? "run active" : "run"}
                  key={trace.trace_id}
                  onClick={() => setPickedId(trace.trace_id)}
                  type="button"
                >
                  <span>{trace.name}</span>
                  <small>
                    {trace.project_id} · {trace.status} · {formatMs(trace.duration_ms)}
                  </small>
                </button>
              ))}
            </div>
            <div className="pager">
              <button
                className="btn"
                type="button"
                disabled={page <= 1}
                onClick={() => replaceParams({ page: page <= 2 ? undefined : String(page - 1) })}
              >
                Previous
              </button>
              <span className="muted">Page {page}</span>
              <button className="btn" type="button" disabled={!hasMore} onClick={() => replaceParams({ page: String(page + 1) })}>
                Next
              </button>
            </div>
          </aside>
          <div className="card trace-panel">
            {!detail.online && traces.length > 0 && <p role="alert">Could not load trace.</p>}
            {detail.data ? (
              <>
                <p>
                  <Link className="row-link" href={`/traces/${detail.data.trace_id}`}>
                    Open full trace detail
                  </Link>
                </p>
                <TraceGraph trace={detail.data} />
              </>
            ) : (
              <p>Loading trace…</p>
            )}
          </div>
        </section>
      )}
    </div>
  );
}
