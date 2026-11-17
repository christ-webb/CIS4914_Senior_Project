"use client";

import Link from "next/link";
import { useCallback, useMemo, useState } from "react";

import { TraceView } from "@/components/traces/trace-view";
import { LiveStatus, PageHeader } from "@/components/shell/page-header";
import { usePolling } from "@/hooks/use-polling";
import { getTrace } from "@/lib/api";
import { errorMessage, errorSignature } from "@/lib/analytics";
import { isActiveLifecycle, traceLifecycle } from "@/lib/types";

export function TraceDetailView({ traceId }: { traceId: string }) {
  const load = useCallback(() => getTrace(traceId), [traceId]);
  const [intervalMs, setIntervalMs] = useState(2000);
  const { data: trace, online, loading, updatedAt, refresh } = usePolling(load, intervalMs);
  const nextInterval = trace && !isActiveLifecycle(traceLifecycle(trace)) ? 0 : 2000;
  if (nextInterval !== intervalMs) setIntervalMs(nextInterval);
  const failures = useMemo(
    () =>
      (trace?.spans ?? [])
        .filter((span) => span.status === "error")
        .map((span) => {
          const message = errorMessage(span);
          return { span, message, signature: errorSignature(span, message) };
        }),
    [trace],
  );

  return (
    <>
      <nav className="crumbs" aria-label="Breadcrumb">
        <Link href="/">Dashboard</Link>
        <span>/</span>
        <span>{trace?.name ?? "Trace"}</span>
      </nav>

      {!trace ? (
        <>
          <PageHeader eyebrow="TRACE" title={loading ? "Loading trace…" : "Trace unavailable"} />
          {!loading && (
            <section className="empty-state">
              <h2>Could not load this trace</h2>
              <p>It may not exist, or the API is unreachable.</p>
              <Link href="/" className="btn">Back to dashboard</Link>
            </section>
          )}
        </>
      ) : (
        <>
          <PageHeader
            eyebrow={`TRACE · ${trace.project_id}`}
            title={trace.name}
            subtitle={<span className="mono">{trace.trace_id}</span>}
            actions={<LiveStatus online={online} updatedAt={updatedAt} onRefresh={refresh} />}
          />

          {failures.length > 0 && (
            <section className="callout bad" role="alert">
              <div>
                <strong>{failures.length} span{failures.length > 1 ? "s" : ""} failed in this run</strong>
                <ul>
                  {failures.map(({ span, message, signature }) => (
                    <li key={span.span_id}>
                      <span className="type-pill">{span.type}</span> <b>{span.name}</b> — {message}{" "}
                      <Link href={`/errors?group=${encodeURIComponent(signature)}`}>Investigate →</Link>
                    </li>
                  ))}
                </ul>
              </div>
            </section>
          )}

          <div className="card trace-panel">
            <TraceView key={trace.trace_id} trace={trace} />
          </div>
        </>
      )}
    </>
  );
}
