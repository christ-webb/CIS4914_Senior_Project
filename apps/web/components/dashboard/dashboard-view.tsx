"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useMemo } from "react";

import { Donut, LatencyBars, Sparkline } from "@/components/dashboard/charts";
import { EmptyState, LiveStatus, PageHeader } from "@/components/shell/page-header";
import { usePolling } from "@/hooks/use-polling";
import { fetchTraces } from "@/lib/api";
import { computeStats, formatCompact, formatCost, formatMs, recentTraces, statusCounts } from "@/lib/stats";

export function DashboardView() {
  const router = useRouter();
  const { data, online, loading, updatedAt, refresh } = usePolling(fetchTraces);
  const traces = useMemo(() => data ?? [], [data]);
  const stats = useMemo(() => computeStats(traces), [traces]);
  const counts = useMemo(() => statusCounts(traces), [traces]);
  const recent = useMemo(() => recentTraces(traces, 30), [traces]);
  const latest = useMemo(() => [...recent].reverse().slice(0, 8), [recent]);
  const failed = useMemo(() => latest.filter((trace) => trace.status === "error"), [latest]);
  const maxDuration = Math.max(1, ...latest.map((trace) => trace.duration_ms));

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
                <small className="muted">{counts.ok} ok · {counts.error} failed</small>
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
                <strong>{formatCompact(stats.totalTokens)}</strong>
                <small className="muted">{stats.totalTokens.toLocaleString()} total</small>
              </div>
              <Sparkline values={recent.map((trace) => trace.total_tokens)} color="var(--violet)" />
            </div>
            <div className="card kpi">
              <div>
                <span className="kpi-label">Cost</span>
                <strong>{formatCost(stats.totalCostUsd)}</strong>
                <small className="muted">across all runs</small>
              </div>
              <Sparkline values={recent.map((trace) => trace.total_cost_usd)} color="var(--ok)" />
            </div>
          </section>

          <section className="card chart-card">
            <div className="card-head">
              <h3>Latency by run</h3>
              <span className="legend muted">
                <i className="dot ok" /> ok <i className="dot error" /> error <i className="dot unset" /> unset
              </span>
            </div>
            <LatencyBars traces={recent} selectedId={null} onSelect={(id) => router.push(`/traces/${id}`)} />
          </section>

          <section className="split">
            <div className="card table-card">
              <div className="card-head"><h3>Recent runs</h3></div>
              <table className="table">
                <thead>
                  <tr><th>Run</th><th>Project</th><th>Status</th><th>Duration</th><th>Tokens</th><th>Cost</th><th>Started</th></tr>
                </thead>
                <tbody>
                  {latest.map((trace) => (
                    <tr key={trace.trace_id}>
                      <td><Link href={`/traces/${trace.trace_id}`} className="row-link">{trace.name}</Link></td>
                      <td className="muted">{trace.project_id}</td>
                      <td><span className={`pill ${trace.status}`}>{trace.status}</span></td>
                      <td className="num">
                        {formatMs(trace.duration_ms)}
                        <span className="run-bar inline"><i className={trace.status} style={{ width: `${(trace.duration_ms / maxDuration) * 100}%` }} /></span>
                      </td>
                      <td className="num">{trace.total_tokens.toLocaleString()}</td>
                      <td className="num">{formatCost(trace.total_cost_usd)}</td>
                      <td className="muted">{new Date(trace.started_at).toLocaleTimeString()}</td>
                    </tr>
                  ))}
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
