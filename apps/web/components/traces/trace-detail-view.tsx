"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { useCallback, useMemo, useState } from "react";

import { TraceView } from "@/components/traces/trace-view";
import { LiveStatus, PageHeader } from "@/components/shell/page-header";
import { usePolling } from "@/hooks/use-polling";
import { getTrace, getTraceDiagnostics } from "@/lib/api";
import { sortFindings } from "@/lib/diagnostics";
import { needsLivePolling, type DiagnosticFinding, type Trace, type TraceDiagnostics } from "@/lib/types";

export function TraceDetailView({ traceId }: { traceId: string }) {
  const params = useSearchParams();
  const load = useCallback(async () => {
    const trace = await getTrace(traceId);
    const diagnostics = await getTraceDiagnostics(traceId).catch(() => null);
    return { trace, diagnostics };
  }, [traceId]);
  const [intervalMs, setIntervalMs] = useState(2000);
  const { data, online, loading, updatedAt, refresh } = usePolling(load, intervalMs);
  const trace = data?.trace ?? null;
  const diagnostics = data?.diagnostics ?? null;
  const nextInterval = trace && !needsLivePolling(trace) ? 0 : 2000;
  if (nextInterval !== intervalMs) setIntervalMs(nextInterval);

  const findings = useMemo(() => sortFindings(diagnostics?.findings ?? []), [diagnostics]);
  const [pickedFinding, setPickedFinding] = useState<string | null>(params.get("finding"));
  const [activeId, setActiveId] = useState<string | null>(null);
  const selected = findings.find((finding) => finding.finding_id === pickedFinding) ?? null;

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

          {diagnostics && (
            <DiagnosticsPanel
              trace={trace}
              diagnostics={diagnostics}
              findings={findings}
              selectedId={pickedFinding}
              onSelect={(finding) => {
                setPickedFinding(finding.finding_id);
                setActiveId(finding.span_ids[0] ?? null);
              }}
            />
          )}

          <div className="card trace-panel">
            <TraceView
              key={trace.trace_id}
              trace={trace}
              findings={findings}
              selectedFindingId={selected?.finding_id ?? null}
              activeId={activeId}
              onSelectSpan={setActiveId}
            />
          </div>
        </>
      )}
    </>
  );
}

function DiagnosticsPanel({
  trace,
  diagnostics,
  findings,
  selectedId,
  onSelect,
}: {
  trace: Trace;
  diagnostics: TraceDiagnostics;
  findings: DiagnosticFinding[];
  selectedId: string | null;
  onSelect: (finding: DiagnosticFinding) => void;
}) {
  const selected = findings.find((finding) => finding.finding_id === selectedId) ?? findings[0] ?? null;
  const coverage = diagnostics.coverage;
  return (
    <section className="diagnostics-panel">
      <div className="card findings-list" aria-label="Diagnostic findings">
        <h3>Diagnostics</h3>
        <p className="muted">
          {diagnostics.analysis_status === "provisional" ? "Provisional (run still active). " : "Finalized analysis. "}
          {findings.length} finding{findings.length === 1 ? "" : "s"}.
        </p>
        {(coverage.unsupported_detectors.length > 0 || coverage.insufficient_data_detectors.length > 0) && (
          <p className="muted">
            {coverage.token_status === "unavailable" && "Token usage not reported. "}
            {coverage.cost_status === "unavailable" && "Cost not reported. "}
            {coverage.unsupported_detectors.length > 0 && `Unsupported: ${coverage.unsupported_detectors.join(", ")}. `}
            {coverage.insufficient_data_detectors.length > 0 &&
              `Insufficient comparable data: ${coverage.insufficient_data_detectors.join(", ")}.`}
          </p>
        )}
        {findings.length === 0 ? (
          <p className="muted">No diagnostic findings from available telemetry.</p>
        ) : (
          <div className="run-scroll">
            {findings.map((finding) => (
              <button
                key={finding.finding_id}
                className={selected?.finding_id === finding.finding_id ? "run active" : "run"}
                onClick={() => onSelect(finding)}
              >
                <span className="run-title">
                  <span className={`pill ${finding.severity}`}>{finding.severity}</span>
                  <span>{finding.title}</span>
                </span>
                <small className="muted">
                  {finding.detection_type} · {finding.confidence}
                  {finding.provisional ? " · provisional" : ""}
                </small>
              </button>
            ))}
          </div>
        )}
      </div>
      <div className="card finding-detail">
        {selected ? (
          <FindingDetail finding={selected} />
        ) : (
          <p className="muted">Select a finding to inspect evidence and related spans for {trace.name}.</p>
        )}
      </div>
    </section>
  );
}

function FindingDetail({ finding }: { finding: DiagnosticFinding }) {
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
      <ul>
        {finding.evidence.map((line) => (
          <li key={line}>{line}</li>
        ))}
      </ul>
      <h4>Related spans</h4>
      <p className="muted">
        {finding.span_ids.length
          ? `${finding.span_ids.length} supporting span${finding.span_ids.length === 1 ? "" : "s"} highlighted on the graph.`
          : "Run-level finding"}
      </p>
      {finding.span_ids.length > 0 && (
        <ul className="finding-list compact">
          {finding.span_ids.map((spanId) => (
            <li key={spanId}><code>{spanId}</code></li>
          ))}
        </ul>
      )}
      <h4>Suggested investigation</h4>
      <p>{finding.suggested_action}</p>
    </div>
  );
}
