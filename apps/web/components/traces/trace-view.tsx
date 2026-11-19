"use client";

import { TraceMetrics } from "./trace-metrics";
import { Disclosure } from "@/components/ui/disclosure";
import { costPresentation, spanTiming } from "@/lib/presentation";
import { useEffect, useMemo, useRef, useState, type KeyboardEvent } from "react";

import { ExecutionGraphCanvas } from "@/components/traces/execution-graph-canvas";
import { useVisibleGraph } from "@/hooks/use-visible-graph";
import { findingsForSpan } from "@/lib/diagnostics";
import { groupsToExpandForFinding, type GraphMode, type SpanGroup } from "@/lib/execution-graph";
import { computeTimeline } from "@/lib/layout";
import {
  formatCostLabel,
  formatMs,
  formatTokenLabel,
  spanUsageStatus,
} from "@/lib/stats";
import { sessionLabel, TASK_OUTCOME_LABELS, turnLabel, turnSummary } from "@/lib/outcomes";
import {
  type DiagnosticFinding,
  type Span,
  type Trace,
} from "@/lib/types";

const GRAPH_MODES: { id: GraphMode; label: string }[] = [
  { id: "overview", label: "Overview" },
  { id: "full", label: "Full trace" },
  { id: "failure", label: "Failure focus" },
];

type Tab = "graph" | "timeline" | "spans";
const TAB_LABELS: Record<Tab, string> = { graph: "Execution graph", timeline: "Timeline", spans: "Spans" };

export function TraceView({
  trace,
  findings = [],
  selectedFindingId = null,
  activeId: controlledActiveId,
  onSelectSpan,
}: {
  trace: Trace;
  findings?: DiagnosticFinding[];
  selectedFindingId?: string | null;
  activeId?: string | null;
  onSelectSpan?: (spanId: string | null) => void;
}) {
  const [tab, setTab] = useState<Tab>("graph");
  const [mode, setMode] = useState<GraphMode>("overview");
  const [internalActiveId, setInternalActiveId] = useState<string | null>(null);
  const activeId = controlledActiveId ?? internalActiveId;
  const setActiveId = onSelectSpan ?? setInternalActiveId;
  const [fitNonce, setFitNonce] = useState(0);
  const [fitNodeIds, setFitNodeIds] = useState<string[] | null>(null);
  const appliedFindingRef = useRef<string | null>(null);
  const { graph, expanded, toggleGroup, expandGroups } = useVisibleGraph({
    spans: trace.spans,
    mode,
    findings,
    selectedFindingId,
    activeId,
    onSelect: setActiveId,
  });

  useEffect(() => {
    if (!selectedFindingId || appliedFindingRef.current === selectedFindingId) return;
    appliedFindingRef.current = selectedFindingId;
    const finding = findings.find((item) => item.finding_id === selectedFindingId) ?? null;
    const groupIds = groupsToExpandForFinding(graph.groups, finding);
    setMode("failure");
    expandGroups(groupIds);
    setFitNodeIds([...(finding?.span_ids ?? []), ...groupIds]);
    setFitNonce((value) => value + 1);
  }, [expandGroups, findings, graph.groups, selectedFindingId]);

  const timeline = useMemo(() => computeTimeline(trace.spans), [trace.spans]);
  const activeSpan = trace.spans.find((span) => span.span_id === activeId) ?? null;
  const activeGroup = graph.groups.find((group) => group.id === activeId) ?? null;
  const turns = turnSummary(trace);
  const turnDetails = Array.isArray(trace.attributes?.["nm5.turns"]) ? (trace.attributes["nm5.turns"] as TurnDetail[]) : [];
  const tokenStatus =
    typeof trace.attributes?.["nm5.token_status"] === "string"
      ? String(trace.attributes["nm5.token_status"])
      : trace.token_status;
  const costStatus =
    typeof trace.attributes?.["nm5.cost_status"] === "string"
      ? String(trace.attributes["nm5.cost_status"])
      : trace.cost_status;

  return (
    <div className="trace-view">
      <div className="trace-identity">
        <h2 className="trace-name" title={trace.name}>{trace.name}</h2>
        <div className="trace-context">
          <span className={`pill ${slug(sessionLabel(trace))}`} title="Session lifecycle">{sessionLabel(trace)}</span>
          <span className={`pill ${slug(turnLabel(trace))}`} title="Latest agent turn">{turnLabel(trace)}</span>
          <span className={`pill task ${turns.taskOutcome}`} title="Task outcome">Task · {TASK_OUTCOME_LABELS[turns.taskOutcome]}</span>
          <span className="muted">{trace.project_id} · {new Date(trace.started_at).toLocaleString()} · {trace.spans.length} operations · <span className="mono" title={trace.trace_id}>{trace.trace_id.slice(0, 8)}</span></span>
          <Disclosure label={`Turns · ${turns.completed}/${turns.total} completed`}>
            <p>Agent turn status is separate from the session lifecycle. Closing a conversation never rewrites a completed turn. Task outcome stays unverified unless an explicit report or independent verification is recorded{turns.taskOutcomeSource ? ` (source: ${turns.taskOutcomeSource})` : ""}.</p>
            {turnDetails.length ? <dl className="detail-list">{turnDetails.map((turn, index) => <div key={turn.turn_key}><dt>Turn {index + 1}</dt><dd>{turn.status.replaceAll("_", " ")} · {turn.evidence} · {turn.provenance}</dd></div>)}</dl> : <p>{turns.latestEvidence ? `Evidence: ${turns.latestEvidence}.` : "No turn evidence recorded."}</p>}
          </Disclosure>
          <Disclosure label="Run details"><dl className="detail-list"><div><dt>Trace ID</dt><dd>{trace.trace_id}</dd></div><div><dt>Project</dt><dd>{trace.project_id}</dd></div></dl><pre>{JSON.stringify(trace.attributes, null, 2)}</pre></Disclosure>
        </div>
      </div>
      <TraceMetrics trace={trace} onFailure={() => { setTab("graph"); setMode("failure"); setFitNodeIds(null); setFitNonce(v => v + 1); }} />
      {findings.length > 0 && <div className="findings-summary"><a href="#diagnostic-evidence">{findings.length} diagnostic findings · View evidence</a><button className="text-button" onClick={() => { setTab("graph"); setMode("failure"); setFitNodeIds(null); setFitNonce(v => v + 1); }}>Failure focus</button></div>}

      <div className="tabs" role="tablist" onKeyDown={navigateTabs}>
        {(Object.keys(TAB_LABELS) as Tab[]).map((value) => (
          <button key={value} role="tab" aria-selected={tab === value} className={tab === value ? "tab active" : "tab"} onClick={() => setTab(value)}>
            {TAB_LABELS[value]}
          </button>
        ))}
      </div>
      {tab === "graph" && (
        <div className="graph-toolbar">
          <div className="tabs nested" role="tablist" onKeyDown={navigateTabs} aria-label="Graph view mode">
            {GRAPH_MODES.map((item) => (
              <button
                key={item.id}
                role="tab"
                data-testid={`graph-mode-${item.id}`}
                aria-selected={mode === item.id}
                className={mode === item.id ? "tab active" : "tab"}
                onClick={() => {
                  if (item.id === mode) return;
                  setMode(item.id);
                  setFitNodeIds(null);
                  setFitNonce((value) => value + 1);
                }}
              >
                {item.label}
              </button>
            ))}
          </div>
          <div className="graph-legend muted">
            Solid: parent–child · Dashed: observed order
            <Disclosure label="Graph relationships"><p>Solid arrows are verified parent-child links. Dashed arrows show observed chronological order, not causality. Groups sum recorded member durations, not wall-clock elapsed time; missing timing is not a zero-duration measurement.</p></Disclosure>
          </div>
        </div>
      )}

      <div className={`view-body${activeSpan || activeGroup ? " has-inspector" : ""}`}>
        <div className="view-main">
          {tab === "graph" ? (
            <ExecutionGraphCanvas
              graph={graph}
              activeId={activeId}
              findings={findings}
              selectedFindingId={selectedFindingId}
              expandedGroupIds={expanded}
              onSelect={setActiveId}
              onToggleGroup={toggleGroup}
              fitNonce={fitNonce}
              fitNodeIds={fitNodeIds}
              ariaLabel={`Execution graph for ${trace.name}`}
              mode={mode}
            />
          ) : tab === "spans" ? (
            <div className="table-scroll">
              <table className="table">
                <thead><tr><th>Span</th><th>Status</th><th>Duration</th><th>Tokens</th><th>Estimated</th><th>Billed</th></tr></thead>
                <tbody>
                  {trace.spans.map((span) => (
                    <tr key={span.span_id} className={`clickable${span.span_id === activeId ? " selected" : ""}`} onClick={() => setActiveId(span.span_id)}>
                      <td><button className="text-button" onClick={() => setActiveId(span.span_id)}><span className={`type-pill t-${span.type}`}>{span.type}</span> {span.name}</button></td>
                      <td><span className={`pill ${span.status}`}>{span.status}</span></td>
                      <td className="num">{spanTiming(span).label}</td>
                      <td className="num">{formatTokenLabel(span.token_count, spanUsageStatus(span, "token", tokenStatus))}</td>
                      <td className="num estimated-cost">{spanEstimateLabel(span)}</td>
                      <td className="num billed-cost">{formatCostLabel(span.cost_usd, spanUsageStatus(span, "cost", costStatus))}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <div className="timeline" role="list">
              <div className="timeline-scale muted"><span>0</span><span>{formatMs(timeline.totalMs)}</span></div>
              {timeline.rows.map(({ span, offsetPct, widthPct }) => (
                <button
                  key={span.span_id}
                  role="listitem"
                  className={`tl-row${span.span_id === activeId ? " active" : ""}`}
                  onClick={() => setActiveId(span.span_id)}
                >
                  <span className="tl-name"><i className={`dot type-${span.type}`} />{span.name}</span>
                  <span className="tl-track">
                    <span
                      className={`tl-bar type-${span.type}${span.status === "error" ? " is-error" : ""}`}
                      style={{ left: `${offsetPct}%`, width: `${Math.min(widthPct, 100 - offsetPct)}%` }}
                    />
                  </span>
                  <span className="tl-dur muted">{spanTiming(span).label}</span>
                </button>
              ))}
            </div>
          )}
        </div>
        {(activeSpan || activeGroup) && <aside className="span-detail" aria-label="Span details">
          {activeSpan ? (
            <SpanDetail span={activeSpan} findings={findingsForSpan(findings, activeSpan.span_id)} />
          ) : activeGroup ? (
            <GroupDetail
              group={activeGroup}
              spans={trace.spans}
              findings={findings}
              onSelectSpan={setActiveId}
            />
          ) : (
            <p className="muted">Select a span or group to inspect its input, output and attributes.</p>
          )}
        </aside>}
      </div>
    </div>
  );
}

function GroupDetail({
  group,
  spans,
  findings,
  onSelectSpan,
}: {
  group: SpanGroup;
  spans: Span[];
  findings: DiagnosticFinding[];
  onSelectSpan: (spanId: string | null) => void;
}) {
  const byId = new Map(spans.map((span) => [span.span_id, span]));
  const members = group.memberIds.map((id) => byId.get(id)).filter((span): span is Span => Boolean(span));
  const ordered = [...members].sort((a, b) => {
    const aFail = a.status === "error" ? 0 : 1;
    const bFail = b.status === "error" ? 0 : 1;
    if (aFail !== bFail) return aFail - bFail;
    return a.span_id.localeCompare(b.span_id);
  });
  return (
    <>
      <p className="kicker">Group</p>
      <h3>{group.name}</h3>
      <p>
        <span className="pill">{group.memberIds.length} calls</span>{" "}
        {group.failedCount > 0 && <span className="pill error">{group.failedCount} failed</span>}{" "}
        {group.findingCount > 0 && (
          <span className={`pill ${group.worstFindingSeverity ?? "info"}`}>{group.findingCount} findings</span>
        )}
      </p>
      <p className="muted">
        Recorded duration sum {formatMs(group.durationMs)} is not wall-clock elapsed time. Missing member timing is excluded from interpretation; inspect each member for provenance.
      </p>
      <h4>Members</h4>
      <ul className="finding-list compact">
        {ordered.map((span) => (
          <li key={span.span_id}>
            <button type="button" className="member-link" onClick={() => onSelectSpan(span.span_id)}>
              <span className={`pill ${span.status}`}>{span.status}</span> {span.name}
            </button>
          </li>
        ))}
      </ul>
      {group.failedCount > 0 && (
        <>
          <h4>Failed members</h4>
          <ul className="finding-list compact">
            {ordered
              .filter((span) => span.status === "error")
              .map((span) => (
                <li key={`fail-${span.span_id}`}>
                  <button type="button" className="member-link" data-testid={`failed-member-${span.span_id}`} onClick={() => onSelectSpan(span.span_id)}>
                    {span.name} · {span.span_id.slice(0, 8)}
                  </button>
                </li>
              ))}
          </ul>
        </>
      )}
      <p className="muted">Diagnostic severity and evidence stay on the original spans.</p>
      {members.flatMap((span) => findingsForSpan(findings, span.span_id)).length > 0 && (
        <>
          <h4>Findings on members</h4>
          <ul className="finding-list compact">
            {members.flatMap((span) =>
              findingsForSpan(findings, span.span_id).map((finding) => (
                <li key={`${span.span_id}-${finding.finding_id}`}>
                  <span className={`pill ${finding.severity}`}>{finding.severity}</span> {finding.title}
                </li>
              )),
            )}
          </ul>
        </>
      )}
    </>
  );
}

function SpanDetail({ span, findings }: { span: Span; findings: DiagnosticFinding[] }) {
  return (
    <>
      <p className="kicker">{span.type}</p>
      <h3>{span.name}</h3>
      <p><span className={`pill ${span.status}`}>{span.status}</span></p>
      {findings.length > 0 && (
        <>
          <h4>Findings</h4>
          <ul className="finding-list compact">
            {findings.map((finding) => (
              <li key={finding.finding_id}>
                <span className={`pill ${finding.severity}`}>{finding.severity}</span> {finding.title}
              </li>
            ))}
          </ul>
        </>
      )}
      <p className="muted">{spanTiming(span).explanation}</p>
      <dl className="kv">
        <div><dt>Duration</dt><dd>{spanTiming(span).label}</dd></div>
        <div><dt>Tokens</dt><dd>{formatTokenLabel(span.token_count, spanUsageStatus(span, "token"))}</dd></div>
        {typeof span.attributes?.["gen_ai.usage.input_tokens"] === "number" && (
          <div><dt>Input</dt><dd>{Number(span.attributes["gen_ai.usage.input_tokens"]).toLocaleString()}</dd></div>
        )}
        {typeof span.attributes?.["gen_ai.usage.output_tokens"] === "number" && (
          <div><dt>Output</dt><dd>{Number(span.attributes["gen_ai.usage.output_tokens"]).toLocaleString()}</dd></div>
        )}
        {typeof span.attributes?.["gen_ai.usage.cache_read_tokens"] === "number" && (
          <div><dt>Cache read</dt><dd>{Number(span.attributes["gen_ai.usage.cache_read_tokens"]).toLocaleString()}</dd></div>
        )}
        {typeof span.attributes?.["gen_ai.usage.cache_write_tokens"] === "number" && (
          <div><dt>Cache write</dt><dd>{Number(span.attributes["gen_ai.usage.cache_write_tokens"]).toLocaleString()}</dd></div>
        )}
        <div><dt>Estimated model cost</dt><dd className="estimated-cost">{spanEstimateLabel(span)}</dd></div>
        <div><dt>Actual billed cost</dt><dd className="billed-cost">{formatCostLabel(span.cost_usd, spanUsageStatus(span, "cost"))}</dd></div>
        {typeof span.attributes?.["gen_ai.request.model"] === "string" && (
          <div><dt>Model</dt><dd>{String(span.attributes["gen_ai.request.model"])}</dd></div>
        )}
        {typeof span.attributes?.["nm5.model_variant"] === "string" && (
          <div><dt>Variant</dt><dd>{String(span.attributes["nm5.model_variant"])}</dd></div>
        )}
        <div><dt>Started</dt><dd>{new Date(span.started_at).toLocaleTimeString()}</dd></div>
      </dl>
      <Json label="Input" value={span.input} />
      <Json label="Output" value={span.output} />
      <Json label="Attributes" value={Object.keys(span.attributes).length ? span.attributes : null} />
    </>
  );
}

interface TurnDetail { turn_key: string; status: string; evidence: string; provenance: string }

function slug(label: string): string {
  return label.toLowerCase().replace(/[^a-z]+/g, "-").replace(/^-|-$/g, "");
}

function spanEstimateLabel(span: Span): string {
  return costPresentation({ attributes: span.attributes }).label;
}

function Json({ label, value }: { label: string; value: unknown }) {
  if (value == null) return null;
  return (
    <details className="json-inspector">
      <summary>{label}</summary>
      <pre>{JSON.stringify(value, null, 2)}</pre>
    </details>
  );
}

function navigateTabs(event: KeyboardEvent<HTMLDivElement>) {
  if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
  const tabs = [...event.currentTarget.querySelectorAll<HTMLButtonElement>('[role="tab"]')];
  const current = tabs.indexOf(document.activeElement as HTMLButtonElement);
  if (current < 0) return;
  event.preventDefault();
  const next = event.key === "Home" ? 0 : event.key === "End" ? tabs.length - 1 : (current + (event.key === "ArrowRight" ? 1 : -1) + tabs.length) % tabs.length;
  tabs[next].focus();
  tabs[next].click();
}
