"use client";

import Link from "next/link";
import { useMemo, useState } from "react";
import { ArrowIcon, EmptyState, LiveStatus, PageHeader, SectionHead } from "@/components/shell/page-header";
import { Disclosure } from "@/components/ui/disclosure";
import { usePolling } from "@/hooks/use-polling";
import { fetchTraces } from "@/lib/api";
import { aggregateEstimatedCost, compactCost, ordinaryRuns } from "@/lib/cost-aggregation";
import { recentCompletedTurns, sessionLabel, sessionOutcomes, TASK_OUTCOME_LABELS, turnCompletion, turnLabel, turnSummary } from "@/lib/outcomes";
import { computeStats, formatCompact, formatDuration, isMeasuredTokens, isMeasuredCost } from "@/lib/stats";
import { isExecutingRun, isIdleOpenRun, type TraceSummary } from "@/lib/types";

const duration = formatDuration;

function pillClass(label: string): string {
  return `pill ${label.toLowerCase().replace(/[^a-z]+/g, "-").replace(/^-|-$/g, "")}`;
}

function when(trace: TraceSummary, kind: "turn" | "session" | "activity"): string {
  const turns = turnSummary(trace);
  if (kind === "turn" && turns.latestCompletedAt) return `Turn completed ${new Date(turns.latestCompletedAt).toLocaleString()}`;
  if (kind === "session" && trace.ended_at) return `Ended ${new Date(trace.ended_at).toLocaleString()}`;
  return trace.last_activity_at ? `Last activity ${new Date(trace.last_activity_at).toLocaleString()}` : "Activity time not reported";
}

function RunRow({ trace, kind }: { trace: TraceSummary; kind: "active" | "turn" | "session" | "idle" }) {
  const turns = turnSummary(trace);
  const primary = kind === "session" ? sessionLabel(trace) : kind === "active" ? "Executing" : kind === "idle" ? "Idle / Open" : "Turn completed";
  const secondary = kind === "turn" ? sessionLabel(trace) : kind === "active" ? null : turns.latest ? turnLabel(trace) : null;
  return <Link href={`/traces/${trace.trace_id}`} className="active-run-row">
    <span className="run-main"><strong>{trace.name}</strong><small className="muted">{trace.source ?? trace.agent_framework ?? "Source not reported"}{trace.is_fixture ? " · fixture" : ""}{turns.total > 1 ? ` · ${turns.completed}/${turns.total} turns completed` : ""}</small></span>
    <span className="run-side">
      <span className="pill-row">
        <span className={pillClass(primary)}>{primary}</span>
        {secondary && secondary !== primary && <span className={`${pillClass(secondary)} quiet`}>{secondary}</span>}
        {turns.taskOutcome !== "unverified" && <span className={`pill task ${turns.taskOutcome}`}>{TASK_OUTCOME_LABELS[turns.taskOutcome]}</span>}
      </span>
      <small className="muted">{duration(trace.duration_ms)} · {when(trace, kind === "turn" ? "turn" : kind === "session" ? "session" : "activity")}{trace.failure_count ? ` · ${trace.failure_count} failures` : ""}</small>
    </span>
    <ArrowIcon />
  </Link>;
}

function RunSection({ index, title, runs, kind, empty, href, limit, count, children }: { index: string; title: string; runs: TraceSummary[]; kind: "active" | "turn" | "session" | "idle"; empty: string; href: string; limit: number; count: string; children?: React.ReactNode }) {
  return <section className={`run-section run-section-${kind}`} aria-label={title}>
    <SectionHead index={index} title={title} count={count}>
      {children}
      <Link className="arrow-link" href={href}>View all <ArrowIcon /></Link>
    </SectionHead>
    {!runs.length ? <p className="section-empty">{empty}</p> : <div className="active-runs-list">{runs.slice(0, limit).map(trace => <RunRow key={trace.trace_id} trace={trace} kind={kind} />)}</div>}
  </section>;
}

export function DashboardView() {
  const { data, online, loading, updatedAt, refresh } = usePolling(fetchTraces, 5000);
  const traces = useMemo(() => ordinaryRuns(data ?? []), [data]);
  const [completedView, setCompletedView] = useState<"turns" | "sessions">("turns");
  const stats = computeStats(traces), estimated = aggregateEstimatedCost(traces);
  const sessions = sessionOutcomes(traces);
  const turns = turnCompletion(traces);
  const unknownCount = sessions.open + sessions.interrupted + sessions.unknown;
  const active = online ? traces.filter(isExecutingRun) : [], idle = traces.filter(isIdleOpenRun);
  const completedTurns = recentCompletedTurns(traces);
  const finalized = traces.filter(t => t.ended_at && ["completed", "failed", "cancelled"].includes(t.lifecycle ?? "")).sort((a, b) => Date.parse(b.ended_at!) - Date.parse(a.ended_at!));
  const tokensMeasured = traces.some(isMeasuredTokens);
  const metrics = [
    { label: "Runs", tone: "feature", value: formatCompact(stats.totalRuns), detail: `${sessions.open} open · ${sessions.finalized} finalized · ${sessions.failed} failed · ${sessions.cancelled} cancelled`, exact: <><p>{stats.totalRuns} distinct sessions. {turns.completed} completed agent turns across them.</p><p>Open and unknown sessions are not failures, and a completed turn is not proof that the task succeeded.</p></> },
    { label: "Observed failed runs", tone: "plain", value: `${(stats.errorRate * 100).toFixed(1)}%`, detail: `${stats.errorRuns} failed · ${unknownCount} open/unknown`, exact: <p>{stats.errorRuns} failed / {stats.totalRuns} total runs. Open and unknown outcomes stay in the denominator; this is not a success rate.</p> },
    { label: "Avg session duration", tone: "surface", value: duration(stats.avgLatencyMs), detail: `p95 ${duration(stats.p95LatencyMs)}`, exact: <p>{stats.avgLatencyMs} ms average; {stats.p95LatencyMs} ms p95. Session elapsed time includes idle periods.</p> },
    { label: "Tokens", tone: "plain", value: tokensMeasured ? formatCompact(stats.totalTokens) : "Not reported", muted: !tokensMeasured, detail: "Reported usage only", exact: <p>{stats.totalTokens.toLocaleString()} reported tokens. Missing usage is excluded, not measured as zero.</p> },
  ];
  return <>
    <PageHeader title="Dashboard" actions={<>
      <Disclosure label="All-time · fixtures excluded"><p>Every recorded run is included. Runs explicitly marked as fixtures are excluded; unmarked history is included.</p><p><Link href="/explorer?fixtures=only">Inspect marked fixtures</Link></p></Disclosure>
      <LiveStatus online={online} updatedAt={updatedAt} onRefresh={refresh} />
    </>} />
    {loading && !data ? <p role="status">Loading run history…</p> : !traces.length ? <EmptyState online={online}><Link className="arrow-link" href="/explorer?fixtures=include">Explore all records, including fixtures <ArrowIcon /></Link></EmptyState> : <>
      <section className="metric-band dashboard-kpis" aria-label="Summary">
        {metrics.map(metric => <div className={`card kpi metric ${metric.tone}`} key={metric.label}><Disclosure label={metric.label} value={<span className={`metric-value${metric.muted ? " is-muted" : ""}`}>{metric.value}</span>}>{metric.exact}</Disclosure><small className="metric-detail">{metric.detail}</small></div>)}
        <div className="card kpi metric accent"><Disclosure label="Estimated cost" value={<span className={`metric-value estimated-cost${estimated.priced ? "" : " is-muted"}`}>{estimated.label}</span>}>
          <p>{estimated.coverage}. {estimated.complete} runs have complete usage coverage; {estimated.total - estimated.complete} have partial or unavailable coverage.</p><p>Exact subtotal: {estimated.minimum}–{estimated.maximum} USD.</p>
          <p>Actual billed cost: {traces.some(isMeasuredCost) ? `${stats.totalCostUsd} USD` : "Not reported"}. Provider-reported charges only; separate from estimates.</p>
          <p>Published pricing scenarios are not actual charges or guaranteed invoice bounds. Partial totals exclude unpriced usage. Actual billed charges are separate.</p>
          <ul>{estimated.contributions.map(item => <li key={item.traceId}>{item.basis}{item.source ? ` (${item.source})` : ""} · {item.scope}: {item.minimum}–{item.maximum} USD ({item.complete ? "complete" : "partial or unverified coverage"})</li>)}</ul>
        </Disclosure><small className="metric-detail">{estimated.coverage}</small><small className="metric-detail">Actual billed: <span className="billed-cost">{traces.some(isMeasuredCost) ? compactCost(stats.totalCostUsd) : "Not reported"}</span></small></div>
      </section>

      <RunSection index="01" title="Active runs" runs={active} kind="active" limit={5} count={`${active.length} executing`} href="/explorer?activity=active" empty="Nothing executing." />

      <div className="dashboard-split">
        <RunSection index="02" title="Recently completed" runs={completedView === "turns" ? completedTurns : finalized} kind={completedView === "turns" ? "turn" : "session"} limit={5}
          count={completedView === "turns" ? `${completedTurns.length} with completed turns` : `${finalized.length} finalized sessions`}
          href={completedView === "turns" ? "/explorer?activity=completed_turns" : "/explorer?activity=terminal"}
          empty={completedView === "turns" ? "No completed turns yet." : "No finalized sessions."}>
          <div className="segmented" role="group" aria-label="Completed work view">
            <button type="button" aria-pressed={completedView === "turns"} onClick={() => setCompletedView("turns")}>Turns</button>
            <button type="button" aria-pressed={completedView === "sessions"} onClick={() => setCompletedView("sessions")}>Sessions</button>
          </div>
        </RunSection>
        <RunSection index="03" title="Idle / Open" runs={idle} kind="idle" limit={3} count={`${idle.length} open`} href="/explorer?activity=idle" empty="No open sessions." />
      </div>
      <p className="footnote-links"><Link className="arrow-link" href="/explorer?lifecycle=interrupted">Interrupted runs <ArrowIcon /></Link><Link className="arrow-link" href="/explorer?fixtures=only">Marked fixtures <ArrowIcon /></Link></p>
    </>}
  </>;
}
