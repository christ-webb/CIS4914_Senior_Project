"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { useCallback, useMemo, useState } from "react";

import { EmptyState, LiveStatus, PageHeader } from "@/components/shell/page-header";
import { usePolling } from "@/hooks/use-polling";
import { fetchDiagnosticSummary } from "@/lib/api";
import { sortFindings } from "@/lib/diagnostics";
import type { DiagnosticFinding, DiagnosticSummary } from "@/lib/types";

export function ErrorsView() {
  const params = useSearchParams();
  const load = useCallback(() => fetchDiagnosticSummary(25), []);
  const { data, online, loading, updatedAt, refresh } = usePolling(load, 15000);
  const [picked, setPicked] = useState<string | null>(params.get("finding"));
  const [query, setQuery] = useState("");
  const [severity, setSeverity] = useState<"all" | "critical" | "warning" | "info">("all");
  const [confidence, setConfidence] = useState<"all" | "confirmed" | "heuristic">("all");

  const summary = data;
  const findings = useMemo(() => sortFindings(summary?.recent_findings ?? []), [summary]);
  const shown = useMemo(() => {
    const needle = query.trim().toLowerCase();
    return findings.filter((finding) => {
      if (severity !== "all" && finding.severity !== severity) return false;
      if (confidence !== "all" && finding.confidence !== confidence) return false;
      if (!needle) return true;
      return `${finding.title} ${finding.explanation} ${finding.detection_type}`.toLowerCase().includes(needle);
    });
  }, [findings, query, severity, confidence]);
  const active = findings.find((finding) => finding.finding_id === picked) ?? shown[0] ?? null;

  return (
    <>
      <PageHeader
        eyebrow="ERRORS"
        title="What's breaking, and where"
        subtitle={
          summary
            ? `${summary.analysis_scope.analyzed_run_count} recent runs analyzed. ${summary.analysis_scope.note}`
            : "Loading diagnostic summary…"
        }
        actions={<LiveStatus online={online} updatedAt={updatedAt} onRefresh={refresh} />}
      />

      {summary && <SummaryStats summary={summary} />}

      {!loading && !summary ? (
        <EmptyState online={online} />
      ) : shown.length === 0 && !loading ? (
        <section className="empty-state">
          <h2>No diagnostic findings in this window</h2>
          <p>The bounded recent-run analysis did not detect critical or warning findings.</p>
        </section>
      ) : (
        <section className="explorer">
          <aside className="card run-list" aria-label="Diagnostic findings">
            <h3>Findings</h3>
            <input className="search" type="search" placeholder="Search findings…" aria-label="Search findings" value={query} onChange={(event) => setQuery(event.target.value)} />
            <div className="toolbar-actions" style={{ display: "flex", gap: 8, margin: "8px 0" }}>
              <select className="search" value={severity} onChange={(event) => setSeverity(event.target.value as typeof severity)} aria-label="Filter severity">
                <option value="all">All severities</option>
                <option value="critical">Critical</option>
                <option value="warning">Warning</option>
                <option value="info">Info</option>
              </select>
              <select className="search" value={confidence} onChange={(event) => setConfidence(event.target.value as typeof confidence)} aria-label="Filter confidence">
                <option value="all">All evidence</option>
                <option value="confirmed">Confirmed</option>
                <option value="heuristic">Heuristic</option>
              </select>
            </div>
            <div className="run-scroll">
              {shown.length === 0 && <p className="muted">No findings match.</p>}
              {shown.map((finding) => (
                <button key={finding.finding_id} className={active?.finding_id === finding.finding_id ? "run active" : "run"} onClick={() => setPicked(finding.finding_id)}>
                  <span className="run-title">
                    <span className={`pill ${finding.severity}`}>{finding.severity}</span>
                    <span>{finding.title}</span>
                  </span>
                  <small className="muted clamp">{finding.detection_type} · {finding.confidence}</small>
                </button>
              ))}
            </div>
          </aside>
          <div className="card trace-panel">{active ? <FindingOverview finding={active} /> : <p className="muted">Select a finding.</p>}</div>
        </section>
      )}
    </>
  );
}

function SummaryStats({ summary }: { summary: DiagnosticSummary }) {
  const types = Object.entries(summary.totals_by_type).sort((a, b) => b[1] - a[1]);
  return (
    <section className="kpis" aria-label="Diagnostic summary">
      <article className="card kpi"><div><span className="kpi-label">Findings (window)</span><strong>{Object.values(summary.totals_by_severity).reduce((a, b) => a + b, 0)}</strong></div></article>
      <article className="card kpi"><div><span className="kpi-label">Critical</span><strong>{summary.totals_by_severity.critical ?? 0}</strong></div></article>
      <article className="card kpi"><div><span className="kpi-label">Warning</span><strong>{summary.totals_by_severity.warning ?? 0}</strong></div></article>
      <article className="card kpi"><div><span className="kpi-label">Affected runs</span><strong>{summary.affected_trace_count}</strong></div></article>
      <article className="card kpi">
        <div>
          <span className="kpi-label">Most common types</span>
          <small className="muted">{types.length ? types.map(([name, count]) => `${name} (${count})`).join(" · ") : "None"}</small>
        </div>
      </article>
    </section>
  );
}

function FindingOverview({ finding }: { finding: DiagnosticFinding }) {
  return (
    <div className="error-detail">
      <p className="eyebrow">{finding.detection_type.replaceAll("_", " ").toUpperCase()}</p>
      <h2>{finding.title}</h2>
      <p>
        <span className={`pill ${finding.severity}`}>{finding.severity}</span>{" "}
        <span className={`pill ${finding.confidence}`}>{finding.confidence}</span>
        {finding.provisional && <span className="pill interrupted">provisional</span>}
      </p>
      <p className="error-message">{finding.explanation}</p>
      <h4>Evidence</h4>
      <ul>{finding.evidence.map((line) => <li key={line}>{line}</li>)}</ul>
      <h4>Suggested investigation</h4>
      <p>{finding.suggested_action}</p>
      <p>
        <Link className="row-link" href={`/traces/${finding.trace_id}?finding=${finding.finding_id}`}>
          Open execution trace →
        </Link>
      </p>
    </div>
  );
}
