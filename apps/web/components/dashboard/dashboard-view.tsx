"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useMemo, useState } from "react";

import { Donut, LatencyBars, Sparkline } from "@/components/dashboard/charts";
import { EmptyState, LiveStatus, PageHeader } from "@/components/shell/page-header";
import { usePolling } from "@/hooks/use-polling";
import { fetchTraces } from "@/lib/api";
import { computeStats, formatCompact, formatCost, formatBilledCostLabel, formatEstimatedCostLabel, formatMs, formatTokenLabel, recentTraces, statusCounts } from "@/lib/stats";
import { isExecutingRun, isIdleOpenRun, needsLivePolling, traceLifecycle, type TraceSummary } from "@/lib/types";

function elapsedLabel(trace: TraceSummary, nowMs: number): string {
  if (isExecutingRun(trace)) {
    return formatMs(Math.max(0, nowMs - Date.parse(trace.started_at)));
  }
  return formatMs(trace.duration_ms);
}

export function DashboardView() {
  const router = useRouter();
  const [nowMs, setNowMs] = useState(() => Date.now());
  const [intervalMs, setIntervalMs] = useState(5000);
  const { data, online, loading, updatedAt, refresh } = usePolling(fetchTraces, intervalMs);
  const traces = useMemo(() => data ?? [], [data]);
  const stats = useMemo(() => computeStats(traces), [traces]);
  const counts = useMemo(() => statusCounts(traces), [traces]);
  const recent = useMemo(() => recentTraces(traces, 30), [traces]);
  const latest = useMemo(() => [...recent].reverse().slice(0, 8), [recent]);
  const failed = useMemo(() => latest.filter((trace) => trace.status === "error"), [latest]);
  const maxDuration = Math.max(1, ...latest.map((trace) => trace.duration_ms));
  const liveRuns = useMemo(
    () => traces.filter((trace) => isExecutingRun(trace)),
    [traces],
  );
  const idleRuns = useMemo(
    () => traces.filter((trace) => isIdleOpenRun(trace)),
    [traces],
  );

  const nextInterval = traces.some((trace) => needsLivePolling(trace)) ? 2000 : 5000;
  if (nextInterval !== intervalMs) setIntervalMs(nextInterval);

  useEffect(() => {
    if (liveRuns.length === 0) return undefined;
    const timer = setInterval(() => setNowMs(Date.now()), 1000);
    return () => clearInterval(timer);
  }, [liveRuns.length]);

  return (
    <>
      <PageHeader
        eyebrow="DASHBOARD"
        title="See exactly where your agents fail"
        subtitle="Live health of every agent run, from request to result."
        actions={<LiveStatus online={online} updatedAt={updatedAt} onRefresh={refresh} />}
      />

      {!loading && traces.length === 0 ? (
        <EmptyState online={online} />
      ) : (
        <>
          <section className="kpis" aria-label="Summary">
            <div className="card kpi">
              <div>
                <span className="kpi-label">Runs</span>
                <strong>{stats.totalRuns}</strong>
                <small className="muted">{counts.ok} ok · {counts.error} failed · {counts.unset} unknown</small>
              </div>
              <Donut counts={counts} />
            </div>
            <div className={`card kpi ${stats.errorRuns > 0 ? "bad" : "good"}`}>
              <div>
                <span className="kpi-label">Error rate</span>
                <strong>{(stats.errorRate * 100).toFixed(1)}%</strong>
                <small className="muted">{stats.errorRuns} failed runs</small>
              </div>
            </div>
            <div className="card kpi">
              <div>
                <span className="kpi-label">Avg latency</span>
                <strong>{formatMs(stats.avgLatencyMs)}</strong>
                <small className="muted">p95 {formatMs(stats.p95LatencyMs)}</small>
              </div>
              <Sparkline values={recent.map((trace) => trace.duration_ms)} />
            </div>
            <div className="card kpi">
              <div>
                <span className="kpi-label">Tokens</span>
                <strong>{stats.totalTokens > 0 ? formatCompact(stats.totalTokens) : "Not reported"}</strong>
                <small className="muted">measured totals only</small>
              </div>
              <Sparkline values={recent.map((trace) => trace.total_tokens)} color="var(--violet)" />
            </div>
            <div className="card kpi">
              <div>
                <span className="kpi-label">Actual billed cost</span>
                <strong className="billed-cost">{stats.totalCostUsd > 0 ? formatCost(stats.totalCostUsd) : "Not reported"}</strong>
                <small className="muted">provider-reported charges only</small>
              </div>
              <Sparkline values={recent.map((trace) => trace.total_cost_usd)} color="var(--ok)" />
            </div>
            <div className="card kpi">
              <div>
                <span className="kpi-label">Estimated model cost</span>
                <strong className="estimated-cost">{stats.estimatedCostUsd > 0 ? formatCost(stats.estimatedCostUsd) : "Estimate unavailable"}</strong>
                <small className="muted">complete estimates only; published rates</small>
              </div>
              <Sparkline
                values={recent.map((trace) => (trace.estimated_cost_status === "complete" ? trace.estimated_cost_usd ?? 0 : 0))}
                color="var(--violet)"
              />
            </div>
          </section>

          <section className="card active-runs" aria-label="Active runs">
            <div className="card-head">
              <h3>Active runs</h3>
              <span className="muted">{liveRuns.length} executing</span>
            </div>
            {liveRuns.length === 0 ? (
              <p className="muted">No agents are reporting telemetry right now.</p>
            ) : (
              <div className="active-runs-list">
                {liveRuns.map((trace) => {
                  const lifecycle = traceLifecycle(trace);
                  return (
                    <Link key={trace.trace_id} href={`/traces/${trace.trace_id}`} className="active-run-row">
                      <span>
                        <strong>{trace.name}</strong>
                        <small className="muted"> {trace.project_id}</small>
                        {trace.source && <span className="pill">{trace.source}</span>}
                        {trace.provider && <span className="pill">{trace.provider}</span>}
                        {trace.agent_framework && <span className="pill">{trace.agent_framework}</span>}
                        {trace.instrumentation_coverage && (
                          <small className="muted"> {trace.instrumentation_coverage}</small>
                        )}
                        {trace.workspace && <small className="muted"> {trace.workspace}</small>}
                      </span>
                      <span className="muted">
                        <span className={`pill ${lifecycle}`}>{lifecycle}</span>{" "}
                        {elapsedLabel(trace, nowMs)}
                        {trace.last_activity_at
                          ? ` · last ${new Date(trace.last_activity_at).toLocaleTimeString()}`
                          : ""}
                      </span>
                    </Link>
                  );
                })}
              </div>
            )}
          </section>

          <section className="card active-runs" aria-label="Idle or open sessions">
            <div className="card-head">
              <h3>Idle / Open</h3>
              <span className="muted">{idleRuns.length} sessions</span>
            </div>
            {idleRuns.length === 0 ? (
              <p className="muted">No open sessions waiting on the next turn.</p>
            ) : (
              <div className="active-runs-list">
                {idleRuns.map((trace) => {
                  const lifecycle = traceLifecycle(trace);
                  return (
                    <Link key={trace.trace_id} href={`/traces/${trace.trace_id}`} className="active-run-row">
                      <span>
                        <strong>{trace.name}</strong>
                        <small className="muted"> {trace.project_id}</small>
                        {trace.source && <span className="pill">{trace.source}</span>}
                      </span>
                      <span className="muted">
                        <span className={`pill ${lifecycle}`}>{lifecycle}</span>{" "}
                        <span className="pill idle">idle</span>
                      </span>
                    </Link>
                  );
                })}
              </div>
            )}
          </section>

          <section className="card chart-card">
            <div className="card-head">
              <h3>Latency by run</h3>
              <span className="legend muted">
                <i className="dot ok" /> ok <i className="dot error" /> error <i className="dot unset" /> unknown
              </span>
            </div>
            <LatencyBars traces={recent} selectedId={null} onSelect={(id) => router.push(`/traces/${id}`)} />
          </section>

          <section className="split">
            <div className="card table-card">
              <div className="card-head">
                <h3>Recent runs</h3>
                <Link href="/explorer" className="row-link">View all runs →</Link>
              </div>
              <table className="table">
                <thead>
                  <tr><th>Run</th><th>Project</th><th>Source</th><th>Status</th><th>Duration</th><th>Tokens</th><th>Estimated</th><th>Billed</th><th>Started</th></tr>
                </thead>
                <tbody>
                  {latest.map((trace) => {
                    const lifecycle = traceLifecycle(trace);
                    return (
                      <tr key={trace.trace_id}>
                        <td><Link href={`/traces/${trace.trace_id}`} className="row-link">{trace.name}</Link></td>
                        <td className="muted">{trace.project_id}</td>
                        <td className="muted">{trace.source ?? "sdk"}{trace.provider ? ` · ${trace.provider}` : ""}</td>
                        <td><span className={`pill ${lifecycle}`}>{lifecycle}</span></td>
                        <td className="num">
                          {elapsedLabel(trace, nowMs)}
                          <span className="run-bar inline"><i className={trace.status} style={{ width: `${(trace.duration_ms / maxDuration) * 100}%` }} /></span>
                        </td>
                        <td className="num">{formatTokenLabel(trace.total_tokens, trace.token_status)}</td>
                        <td className="num estimated-cost">{formatEstimatedCostLabel(trace.estimated_cost_usd, trace.estimated_cost_status)}</td>
                        <td className="num billed-cost">{formatBilledCostLabel(trace.total_cost_usd, trace.cost_status)}</td>
                        <td className="muted">{new Date(trace.started_at).toLocaleTimeString()}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>

            <div className="card attention">
              <div className="card-head"><h3>Needs attention</h3></div>
              {failed.length === 0 ? (
                <p className="muted">No failed runs among the latest. 🎉</p>
              ) : (
                failed.map((trace) => (
                  <Link key={trace.trace_id} href={`/traces/${trace.trace_id}`} className="attention-item">
                    <span className="dot error" />
                    <span>
                      <strong>{trace.name}</strong>
                      <small className="muted">{formatMs(trace.duration_ms)} · {new Date(trace.started_at).toLocaleTimeString()}</small>
                    </span>
                  </Link>
                ))
              )}
              <Link href="/errors" className="btn block">View all errors →</Link>
            </div>
          </section>
        </>
      )}
    </>
  );
}
