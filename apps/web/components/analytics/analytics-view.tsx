"use client";

import { aggregateEstimatedCost, ordinaryRuns } from "@/lib/cost-aggregation";
import { costPresentation, spanTiming } from "@/lib/presentation";
import Link from "next/link";
import { useMemo } from "react";

import { EmptyState, LiveStatus, PageHeader, SectionHead } from "@/components/shell/page-header";
import { Disclosure } from "@/components/ui/disclosure";
import { usePolling } from "@/hooks/use-polling";
import { fetchFullTraces } from "@/lib/api";
import { projectStats, slowestSpans, spanTypeStats, timeBuckets } from "@/lib/analytics";
import { formatRate, sessionOutcomes, taskOutcomes, turnCompletion } from "@/lib/outcomes";
import { formatCompact, formatCost, formatDuration, formatMs, isMeasuredCost, isMeasuredTokens, percentile, spanUsageStatus } from "@/lib/stats";

const SESSION_SEGMENTS = [
  ["finalized", "Finalized"],
  ["open", "Open"],
  ["cancelled", "Cancelled"],
  ["interrupted", "Interrupted"],
  ["failed", "Failed"],
  ["unknown", "Unknown"],
] as const;

export function AnalyticsView() {
  const { data, online, loading, updatedAt, refresh } = usePolling(fetchFullTraces, 15000);
  const traces = useMemo(() => ordinaryRuns(data ?? []), [data]);

  const summary = useMemo(() => {
    const durations = traces.map((trace) => trace.duration_ms);
    const measuredCost = traces.filter(isMeasuredCost);
    const measuredTokens = traces.filter(isMeasuredTokens);
    return {
      runs: traces.length,
      turns: turnCompletion(traces),
      sessions: sessionOutcomes(traces),
      tasks: taskOutcomes(traces),
      p50: percentile(durations, 50),
      p95: percentile(durations, 95),
      costPerRun: measuredCost.length ? measuredCost.reduce((a, trace) => a + trace.total_cost_usd, 0) / measuredCost.length : 0,
      tokensPerRun: measuredTokens.length ? measuredTokens.reduce((a, trace) => a + trace.total_tokens, 0) / measuredTokens.length : 0,
      costMeasured: measuredCost.length > 0,
      tokensMeasured: measuredTokens.length > 0,
      estimated: aggregateEstimatedCost(traces),
    };
  }, [traces]);
  const types = useMemo(() => spanTypeStats(traces), [traces]);
  const slow = useMemo(() => slowestSpans(traces, 6), [traces]);
  const projects = useMemo(() => projectStats(traces), [traces]);
  const buckets = useMemo(() => timeBuckets(traces, 16), [traces]);
  const maxRuns = Math.max(1, ...buckets.map((bucket) => bucket.runs));
  const maxTypeCount = Math.max(1, ...types.map((stat) => stat.count));
  const { turns, sessions, tasks } = summary;
  const verified = tasks.verifiedSuccess + tasks.verifiedFailure;
  const reported = tasks.reportedSuccess + tasks.reportedFailure;

  return (
    <>
      <PageHeader
        title="Analytics"
        actions={<>
          <Disclosure label={`Latest ${traces.length} runs`}><p>Analytics covers the {traces.length} most recent ordinary runs. Explicitly marked fixtures are excluded.</p></Disclosure>
          <LiveStatus online={online} updatedAt={updatedAt} onRefresh={refresh} />
        </>}
      />

      {!loading && traces.length === 0 ? (
        <EmptyState online={online} />
      ) : (
        <>
          <section className="analytics-hero" aria-label="Outcomes">
            <div className="hero-panel feature">
              <Disclosure label="Turn completion" value={<span className="hero-value">{formatRate(turns.rate)}</span>}>
                <p>Completed turns ÷ turns with terminal evidence (completed + failed + interrupted).</p>
                <p>Executing turns ({turns.executing}) and provisional responses without a final stop event ({turns.provisional}) are excluded. Completion means the agent finished responding, not that the task succeeded.</p>
              </Disclosure>
              <div className="meter" role="img" aria-label={`${turns.completed} of ${turns.terminal} terminal turns completed`}><i style={{ width: `${(turns.rate ?? 0) * 100}%` }} /></div>
              <p className="hero-detail"><b>{turns.completed}</b> of <b>{turns.terminal}</b> terminal turns · {turns.failed} failed · {turns.interrupted} interrupted</p>
            </div>

            <div className="hero-panel surface">
              <Disclosure label="Session outcomes" value={<span className="hero-value">{sessions.total}</span>}>
                <p>Conversation lifecycle, independent of turn completion. Open and unknown sessions are not failures. Closing a chat can cancel a session without changing its completed turns.</p>
              </Disclosure>
              <div className="outcome-bar" role="img" aria-label={SESSION_SEGMENTS.map(([key, label]) => `${sessions[key]} ${label.toLowerCase()}`).join(", ")}>
                {SESSION_SEGMENTS.map(([key]) => sessions[key] > 0 && <i key={key} className={`seg-${key}`} style={{ flex: sessions[key] }} />)}
              </div>
              <dl className="outcome-legend">
                {SESSION_SEGMENTS.map(([key, label]) => <div key={key}><dt><i className={`swatch seg-${key}`} />{label}</dt><dd>{sessions[key]}</dd></div>)}
              </dl>
            </div>

            <div className="hero-panel accent">
              <Disclosure label="Verified task success" value={<span className={`hero-value${verified ? "" : " is-muted"}`}>{verified ? formatRate(tasks.verifiedRate) : "Unverified"}</span>}>
                <p>Only independent verification (for example a test run or evaluator recorded with <code>nm5.task_verification</code>) counts here.</p>
                <p>Agent self-reports are listed separately, and a completed response is never treated as proof of success.</p>
              </Disclosure>
              <p className="hero-detail">{verified ? <><b>{tasks.verifiedSuccess}</b> of <b>{verified}</b> verified outcomes succeeded</> : <>0 verified outcomes · {tasks.unverified} unverified</>}</p>
              {reported > 0 && <p className="hero-detail">Agent-reported: {tasks.reportedSuccess} success · {tasks.reportedFailure} failure</p>}
            </div>
          </section>

          <section className="metric-band four" aria-label="Usage">
            <div className="metric"><span className="kpi-label">Median session</span><strong className="metric-value">{formatDuration(summary.p50)}</strong><small className="metric-detail">p95 {formatDuration(summary.p95)}</small></div>
            <div className="metric surface"><span className="kpi-label">Tokens / run</span><strong className={`metric-value${summary.tokensMeasured ? "" : " is-muted"}`}>{summary.tokensMeasured ? formatCompact(summary.tokensPerRun) : "Not reported"}</strong><small className="metric-detail">Measured average</small></div>
            <div className="metric"><span className="kpi-label">Estimated cost</span><strong className={`metric-value estimated-cost${summary.estimated.priced ? "" : " is-muted"}`}>{summary.estimated.label}</strong><small className="metric-detail">{summary.estimated.coverage}</small></div>
            <div className="metric"><span className="kpi-label">Actual billed / run</span><strong className={`metric-value billed-cost${summary.costMeasured ? "" : " is-muted"}`}>{summary.costMeasured ? formatCost(summary.costPerRun) : "Not reported"}</strong><small className="metric-detail">Provider-reported</small></div>
          </section>

          <section className="panel chart-card">
            <SectionHead index="01" title="Runs over time" count={<span className="legend"><i className="dot error" /> observed failure <i className="dot ok" /> no observed failure</span>} />
            <div className="chart-axis"><span>Run count</span><span>{maxRuns}</span></div>
            <div className="stack-bars" role="img" aria-label="Runs over time, split by observed failure">
              {buckets.map((bucket) => (
                <div key={bucket.start} className="stack-col" title={`${new Date(bucket.start).toLocaleTimeString()} · ${bucket.runs} runs, ${bucket.errors} with observed failure`}>
                  <div className="stack" style={{ height: `${(bucket.runs / maxRuns) * 100}%` }}>
                    <i className="seg error" style={{ flex: bucket.errors }} />
                    <i className="seg ok" style={{ flex: bucket.runs - bucket.errors }} />
                  </div>
                </div>
              ))}
            </div>
            <div className="chart-axis"><span>{buckets[0] ? new Date(buckets[0].start).toLocaleString() : ""}</span><span>{buckets.length ? new Date(buckets[buckets.length - 1].start).toLocaleString() : ""}</span></div>
            {traces.length < 3 && <p className="muted">{traces.length} recorded runs. More executions will reveal trends.</p>}
            <details className="chart-values"><summary>View chart data</summary>{buckets.map(b => <p key={b.start}>{new Date(b.start).toLocaleString()} · {b.runs} runs · {b.errors} with observed failure</p>)}</details>
          </section>

          <section className="split even">
            <div className="panel table-card">
              <SectionHead index="02" title="Span types" />
              <div className="table-scroll"><table className="table">
                <thead><tr><th>Type</th><th>Spans</th><th>Recorded avg</th><th>Recorded p95</th><th>Errors</th><th>Billed cost</th></tr></thead>
                <tbody>
                  {types.map((stat) => (
                    <tr key={stat.type}>
                      <td><span className={`type-pill t-${stat.type}`}>{stat.type}</span></td>
                      <td className="num">{stat.count}<span className="run-bar inline"><i className="ok" style={{ width: `${(stat.count / maxTypeCount) * 100}%` }} /></span></td>
                      <td className="num">{stat.timedCount ? formatMs(stat.avgMs) : "Not reported"}</td>
                      <td className="num">{stat.timedCount ? formatMs(stat.p95Ms) : "Not reported"}</td>
                      <td className={`num ${stat.errors ? "bad-text" : ""}`}>{stat.errors}</td>
                      <td className="num">{traces.some(t => t.spans.some(s => s.type === stat.type && (spanUsageStatus(s, "cost") === "reported" || (!spanUsageStatus(s, "cost") && s.cost_usd > 0)))) ? formatCost(stat.cost) : "Not reported"}</td>
                    </tr>
                  ))}
                </tbody>
              </table></div>
            </div>

            <div className="panel table-card">
              <SectionHead index="03" title="Longest spans" />
              <div className="table-scroll"><table className="table">
                <thead><tr><th>Span</th><th>Run</th><th>Duration</th></tr></thead>
                <tbody>
                  {slow.map(({ trace, span }) => (
                    <tr key={span.span_id}>
                      <td><span className={`type-pill t-${span.type}`}>{span.type}</span> {span.name}</td>
                      <td><Link href={`/traces/${trace.trace_id}`} className="row-link">{trace.name}</Link></td>
                      <td className="num">{spanTiming(span).label}</td>
                    </tr>
                  ))}
                </tbody>
              </table></div>
            </div>
          </section>

          {traces.some(trace => ["range", "partial"].includes(costPresentation(trace).kind)) && <section className="panel table-card">
            <SectionHead index="04" title="Scenario estimates" count="Excluded from complete-estimate totals" />
            <div className="table-scroll"><table className="table"><thead><tr><th>Run</th><th>Estimated cost</th><th>Basis</th></tr></thead><tbody>{traces.filter(trace => ["range", "partial"].includes(costPresentation(trace).kind)).map(trace => { const cost = costPresentation(trace); return <tr key={trace.trace_id}><td><Link className="row-link" href={`/traces/${trace.trace_id}`}>{trace.name}</Link></td><td>{cost.label}</td><td>{cost.kind === "range" ? "Unverified variant scenarios" : "Partial coverage or single scenario"}</td></tr>; })}</tbody></table></div>
            <p className="muted small">Supported scenarios are not actual billed charges or bounds on your bill. Open a run for its coverage and pricing details.</p>
          </section>}

          <section className="panel table-card">
            <SectionHead index={traces.some(trace => ["range", "partial"].includes(costPresentation(trace).kind)) ? "05" : "04"} title="Cost by project" count="Complete estimates and reported billing only" />
            <div className="table-scroll"><table className="table">
              <thead><tr><th>Project</th><th>Runs</th><th>Failed</th><th>Tokens</th><th>Estimated</th><th>Billed</th></tr></thead>
              <tbody>
                {projects.map((project) => (
                  <tr key={project.project}>
                    <td>{project.project}</td>
                    <td className="num">{project.runs}</td>
                    <td className={`num ${project.errors ? "bad-text" : ""}`}>{project.errors}</td>
                    <td className="num">{traces.some(t => t.project_id === project.project && isMeasuredTokens(t)) ? project.tokens.toLocaleString() : "Not reported"}</td>
                    <td className="num estimated-cost">{project.estimate?.label ?? "Not available"}</td>
                    <td className="num billed-cost">{traces.some(t => t.project_id === project.project && isMeasuredCost(t)) ? formatCost(project.cost) : "Not reported"}</td>
                  </tr>
                ))}
              </tbody>
            </table></div>
          </section>
        </>
      )}
    </>
  );
}
