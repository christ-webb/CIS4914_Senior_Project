"use client";

import Link from "next/link";
import { useMemo } from "react";

import { EmptyState, LiveStatus, PageHeader } from "@/components/shell/page-header";
import { usePolling } from "@/hooks/use-polling";
import { fetchFullTraces } from "@/lib/api";
import { projectStats, slowestSpans, spanTypeStats, timeBuckets } from "@/lib/analytics";
import { formatCompact, formatCost, formatMs, percentile } from "@/lib/stats";

export function AnalyticsView() {
  const { data, online, loading, updatedAt, refresh } = usePolling(fetchFullTraces, 15000);
  const traces = useMemo(() => data ?? [], [data]);

  const summary = useMemo(() => {
    const runs = traces.length;
    const failed = traces.filter((trace) => trace.status === "error").length;
    const durations = traces.map((trace) => trace.duration_ms);
    return {
      runs,
      successRate: runs ? ((runs - failed) / runs) * 100 : 0,
      p50: percentile(durations, 50),
      p95: percentile(durations, 95),
      costPerRun: runs ? traces.reduce((a, trace) => a + trace.total_cost_usd, 0) / runs : 0,
      tokensPerRun: runs ? traces.reduce((a, trace) => a + trace.total_tokens, 0) / runs : 0,
    };
  }, [traces]);
  const types = useMemo(() => spanTypeStats(traces), [traces]);
  const slow = useMemo(() => slowestSpans(traces, 6), [traces]);
  const projects = useMemo(() => projectStats(traces), [traces]);
  const buckets = useMemo(() => timeBuckets(traces, 16), [traces]);
  const maxRuns = Math.max(1, ...buckets.map((bucket) => bucket.runs));
  const maxTypeCount = Math.max(1, ...types.map((stat) => stat.count));

  return (
    <>
      <PageHeader
        eyebrow="ANALYTICS"
        title="Performance, cost and reliability"
        subtitle={`Based on the ${traces.length} most recent runs.`}
        actions={<LiveStatus online={online} updatedAt={updatedAt} onRefresh={refresh} />}
      />

      {!loading && traces.length === 0 ? (
        <EmptyState online={online} />
      ) : (
        <>
          <section className="kpis">
            <div className={`card kpi ${summary.successRate >= 95 ? "good" : summary.successRate >= 80 ? "" : "bad"}`}>
              <div><span className="kpi-label">Success rate</span><strong>{summary.successRate.toFixed(1)}%</strong><small className="muted">{summary.runs} runs</small></div>
            </div>
            <div className="card kpi">
              <div><span className="kpi-label">Median latency</span><strong>{formatMs(summary.p50)}</strong><small className="muted">p95 {formatMs(summary.p95)}</small></div>
            </div>
            <div className="card kpi">
              <div><span className="kpi-label">Cost / run</span><strong>{formatCost(summary.costPerRun)}</strong><small className="muted">average</small></div>
            </div>
            <div className="card kpi">
              <div><span className="kpi-label">Tokens / run</span><strong>{formatCompact(summary.tokensPerRun)}</strong><small className="muted">average</small></div>
            </div>
          </section>

          <section className="card chart-card">
            <div className="card-head">
              <h3>Runs over time</h3>
              <span className="legend muted"><i className="dot ok" /> succeeded <i className="dot error" /> failed</span>
            </div>
            <div className="stack-bars" role="img" aria-label="Runs over time, split by success and failure">
              {buckets.map((bucket) => (
                <div key={bucket.start} className="stack-col" title={`${new Date(bucket.start).toLocaleTimeString()} · ${bucket.runs} runs, ${bucket.errors} failed`}>
                  <div className="stack" style={{ height: `${(bucket.runs / maxRuns) * 100}%` }}>
                    <i className="seg error" style={{ flex: bucket.errors }} />
                    <i className="seg ok" style={{ flex: bucket.runs - bucket.errors }} />
                  </div>
                </div>
              ))}
            </div>
          </section>

          <section className="split even">
            <div className="card table-card">
              <div className="card-head"><h3>Span types</h3></div>
              <table className="table">
                <thead><tr><th>Type</th><th>Spans</th><th>Avg</th><th>p95</th><th>Errors</th><th>Cost</th></tr></thead>
                <tbody>
                  {types.map((stat) => (
                    <tr key={stat.type}>
                      <td><span className={`type-pill t-${stat.type}`}>{stat.type}</span></td>
                      <td className="num">{stat.count}<span className="run-bar inline"><i className="ok" style={{ width: `${(stat.count / maxTypeCount) * 100}%` }} /></span></td>
                      <td className="num">{formatMs(stat.avgMs)}</td>
                      <td className="num">{formatMs(stat.p95Ms)}</td>
                      <td className={`num ${stat.errors ? "bad-text" : ""}`}>{stat.errors}</td>
                      <td className="num">{formatCost(stat.cost)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            <div className="card table-card">
              <div className="card-head"><h3>Slowest spans</h3></div>
              <table className="table">
                <thead><tr><th>Span</th><th>Run</th><th>Duration</th></tr></thead>
                <tbody>
                  {slow.map(({ trace, span }) => (
                    <tr key={span.span_id}>
                      <td><span className={`type-pill t-${span.type}`}>{span.type}</span> {span.name}</td>
                      <td><Link href={`/traces/${trace.trace_id}`} className="row-link">{trace.name}</Link></td>
                      <td className="num">{formatMs(span.duration_ms)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>

          <section className="card table-card">
            <div className="card-head"><h3>Cost by project</h3></div>
            <table className="table">
              <thead><tr><th>Project</th><th>Runs</th><th>Failed</th><th>Tokens</th><th>Cost</th></tr></thead>
              <tbody>
                {projects.map((project) => (
                  <tr key={project.project}>
                    <td>{project.project}</td>
                    <td className="num">{project.runs}</td>
                    <td className={`num ${project.errors ? "bad-text" : ""}`}>{project.errors}</td>
                    <td className="num">{project.tokens.toLocaleString()}</td>
                    <td className="num">{formatCost(project.cost)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </section>
        </>
      )}
    </>
  );
}
