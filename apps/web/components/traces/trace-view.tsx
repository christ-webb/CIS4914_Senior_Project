"use client";

import { useEffect, useMemo, useRef, useState } from "react";

import { ExecutionGraphCanvas } from "@/components/traces/execution-graph-canvas";
import { useVisibleGraph } from "@/hooks/use-visible-graph";
import { findingsForSpan } from "@/lib/diagnostics";
import { groupsToExpandForFinding, type GraphMode, type SpanGroup } from "@/lib/execution-graph";
import { computeTimeline } from "@/lib/layout";
import {
  formatBilledCostLabel,
  formatCostLabel,
  formatEstimatedCostLabel,
  formatMs,
  formatTokenLabel,
  spanUsageStatus,
} from "@/lib/stats";
import {
  executionState,
  isExecutingRun,
  traceLifecycle,
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
  const errors = trace.spans.filter((span) => span.status === "error").length;
  const lifecycle = traceLifecycle(trace);
  const execution = executionState(trace);
  const live = isExecutingRun(trace);
  const tokenStatus =
    typeof trace.attributes?.["nm5.token_status"] === "string"
      ? String(trace.attributes["nm5.token_status"])
      : trace.token_status;
  const costStatus =
    typeof trace.attributes?.["nm5.cost_status"] === "string"
      ? String(trace.attributes["nm5.cost_status"])
      : trace.cost_status;
  const usageScope =
    typeof trace.attributes?.["nm5.usage_scope"] === "string"
      ? String(trace.attributes["nm5.usage_scope"])
      : trace.usage_scope;
  const contextTokens = trace.attributes?.["nm5.context_tokens"];
  const contextWindow = trace.attributes?.["nm5.context_window_size"];
  const inputTokens = numberAttr(trace.attributes, "nm5.usage.input_tokens");
  const outputTokens = numberAttr(trace.attributes, "nm5.usage.output_tokens");
  const cacheRead = numberAttr(trace.attributes, "nm5.usage.cache_read_tokens");
  const cacheWrite = numberAttr(trace.attributes, "nm5.usage.cache_write_tokens");
  const estimatedStatus =
    typeof trace.attributes?.["nm5.estimated_cost_status"] === "string"
      ? String(trace.attributes["nm5.estimated_cost_status"])
      : trace.estimated_cost_status;
  const estimatedUsd = numberAttr(trace.attributes, "nm5.estimated_cost_usd") ?? trace.estimated_cost_usd;
  const subtotalUsd = numberAttr(trace.attributes, "nm5.estimated_subtotal_usd") ?? trace.estimated_subtotal_usd;
  const scenarioStandard = numberAttr(trace.attributes, "nm5.scenario_standard_usd") ?? trace.scenario_standard_usd;
  const scenarioFast = numberAttr(trace.attributes, "nm5.scenario_fast_usd") ?? trace.scenario_fast_usd;
  const covered = numberAttr(trace.attributes, "nm5.estimate_covered_generations") ?? trace.covered_generations;
  const totalGens = numberAttr(trace.attributes, "nm5.estimate_total_generations") ?? trace.total_generations;
  const modelId =
    typeof trace.attributes?.["nm5.model_id"] === "string"
      ? String(trace.attributes["nm5.model_id"])
      : trace.model;
  const modelVariant =
    typeof trace.attributes?.["nm5.model_variant"] === "string"
      ? String(trace.attributes["nm5.model_variant"])
      : trace.model_variant;
  const pricingSource =
    typeof trace.attributes?.["nm5.pricing_source"] === "string"
      ? String(trace.attributes["nm5.pricing_source"])
      : null;
  const pricingVerified =
    typeof trace.attributes?.["nm5.pricing_verified_date"] === "string"
      ? String(trace.attributes["nm5.pricing_verified_date"])
      : null;
  const estimateBasis =
    typeof trace.attributes?.["nm5.estimate_basis"] === "string"
      ? String(trace.attributes["nm5.estimate_basis"])
      : trace.estimate_basis;
  const registryVersion =
    typeof trace.attributes?.["nm5.pricing_registry_version"] === "string"
      ? String(trace.attributes["nm5.pricing_registry_version"])
      : null;

  return (
    <div className="trace-view">
      <div className="trace-heading">
        <div>
          <p className="muted">
            <span className={`pill ${lifecycle}`}>{lifecycle}</span>{" "}
            <span className={`pill ${execution}`}>{execution}</span>{" "}
            <span className={`pill ${trace.status}`}>{trace.status}</span>{" "}
            {typeof trace.attributes?.["nm5.source"] === "string" && (
              <span className="pill">{String(trace.attributes["nm5.source"])}</span>
            )}{" "}
            {typeof trace.attributes?.["gen_ai.provider"] === "string" && (
              <span className="pill">{String(trace.attributes["gen_ai.provider"])}</span>
            )}{" "}
            {typeof trace.attributes?.["agent.framework"] === "string" && (
              <span className="pill">{String(trace.attributes["agent.framework"])}</span>
            )}{" "}
            {new Date(trace.started_at).toLocaleString()}
            {trace.ended_at ? ` → ${new Date(trace.ended_at).toLocaleString()}` : ""}
            {" · "}
            {trace.spans.length} spans
            {errors > 0 && ` · ${errors} failed`}
            {live && " · live"}
          </p>
            {typeof trace.attributes?.["nm5.instrumentation"] === "string" && (
            <p className="muted">Method: {String(trace.attributes["nm5.instrumentation"])}</p>
          )}
          {typeof trace.attributes?.["nm5.coverage"] === "string" && (
            <p className="muted">Instrumentation: {String(trace.attributes["nm5.coverage"])}</p>
          )}
          {typeof trace.attributes?.["nm5.outcome"] === "string" && (
            <p className="muted">Outcome: {String(trace.attributes["nm5.outcome"])}</p>
          )}
          {typeof trace.attributes?.["nm5.turn_status"] === "string" && (
            <p className="muted">Last turn: {String(trace.attributes["nm5.turn_status"])}</p>
          )}
          {usageScope === "parent_agent" && tokenStatus === "reported" && (
            <p className="muted">Usage coverage: Parent agent only. Subagent tokens were not reported.</p>
          )}
          {modelId && (
            <p className="muted">
              Model: {modelId}
              {modelVariant ? ` · ${modelVariant}` : " · variant not verified"}
            </p>
          )}
          {typeof trace.attributes?.["nm5.workspace"] === "string" && (
            <p className="muted">Workspace: {String(trace.attributes["nm5.workspace"])}</p>
          )}
        </div>
        <dl className="mini-stats">
          <div><dt>Duration</dt><dd>{formatMs(trace.duration_ms)}</dd></div>
          <div>
            <dt>Tokens</dt>
            <dd>{formatTokenLabel(trace.total_tokens, tokenStatus)}</dd>
          </div>
          <div>
            <dt>Actual billed cost</dt>
            <dd className="billed-cost">{formatBilledCostLabel(trace.total_cost_usd, costStatus)}</dd>
          </div>
          <div>
            <dt>Estimated model cost</dt>
            <dd className="estimated-cost">{formatEstimatedCostLabel(estimatedUsd, estimatedStatus)}</dd>
          </div>
          {typeof contextTokens === "number" && typeof contextWindow === "number" && (
            <div>
              <dt>Context window</dt>
              <dd>{contextTokens.toLocaleString()} / {contextWindow.toLocaleString()}</dd>
            </div>
          )}
        </dl>
      </div>
      {(inputTokens != null || outputTokens != null || cacheRead != null) && (
        <section className="token-breakdown" aria-label="Token breakdown">
          <h3>Token breakdown</h3>
          <dl className="mini-stats">
            <div><dt>Cumulative consumed</dt><dd>{formatTokenLabel(trace.total_tokens, tokenStatus)}</dd></div>
            {inputTokens != null && <div><dt>Input</dt><dd>{inputTokens.toLocaleString()}</dd></div>}
            {outputTokens != null && <div><dt>Output</dt><dd>{outputTokens.toLocaleString()}</dd></div>}
            {cacheRead != null && <div><dt>Cache read</dt><dd>{cacheRead.toLocaleString()}</dd></div>}
            {cacheWrite != null && <div><dt>Cache write</dt><dd>{cacheWrite.toLocaleString()}</dd></div>}
            {typeof contextTokens === "number" && (
              <div>
                <dt>Current context occupancy</dt>
                <dd>
                  {contextTokens.toLocaleString()}
                  {typeof contextWindow === "number" ? ` / ${contextWindow.toLocaleString()}` : ""}
                </dd>
              </div>
            )}
          </dl>
          {subtotalUsd != null && estimatedStatus !== "complete" && (
            <p className="muted">Qualified estimated subtotal: {formatCost(subtotalUsd)} covering {covered ?? 0}/{totalGens ?? 0} generations. Full-run estimated cost is unavailable.</p>
          )}
          {scenarioStandard != null && scenarioFast != null && (
            <p className="muted">
              Standard vs Fast scenario range (not the actual run estimate): {formatCost(scenarioStandard)} – {formatCost(scenarioFast)}
            </p>
          )}
          <p className="muted">
            Estimated model cost uses published list rates
            {pricingSource ? ` (${pricingSource}` : ""}
            {pricingVerified ? `, verified ${pricingVerified}` : ""}
            {pricingSource ? ")" : ""}
            {estimateBasis === "current_rate" ? "; current-rate re-estimate" : ""}
            {registryVersion ? `; registry ${registryVersion}` : ""}.
            It may differ from the amount charged by a subscription provider. Credits and discounts
            are not applied.
          </p>
        </section>
      )}

      <div className="tabs" role="tablist">
        {(Object.keys(TAB_LABELS) as Tab[]).map((value) => (
          <button key={value} role="tab" aria-selected={tab === value} className={tab === value ? "tab active" : "tab"} onClick={() => setTab(value)}>
            {TAB_LABELS[value]}
          </button>
        ))}
      </div>
      {tab === "graph" && (
        <div className="graph-toolbar">
          <div className="tabs nested" role="tablist" aria-label="Graph view mode">
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
          <p className="graph-legend muted">
            Solid arrows are verified parent-child links. Dashed arrows are observed chronological order, not causality.
            Groups sum member durations and do not represent wall-clock elapsed time.
          </p>
        </div>
      )}

      <div className="view-body">
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
                      <td><span className={`type-pill t-${span.type}`}>{span.type}</span> {span.name}</td>
                      <td><span className={`pill ${span.status}`}>{span.status}</span></td>
                      <td className="num">{formatMs(span.duration_ms)}</td>
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
                  <span className="tl-dur muted">{formatMs(span.duration_ms)}</span>
                </button>
              ))}
            </div>
          )}
        </div>
        <aside className="span-detail" aria-label="Span details">
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
        </aside>
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
      <p className="eyebrow">GROUP</p>
      <h3>{group.name}</h3>
      <p>
        <span className="pill">{group.memberIds.length} calls</span>{" "}
        {group.failedCount > 0 && <span className="pill error">{group.failedCount} failed</span>}{" "}
        {group.findingCount > 0 && (
          <span className={`pill ${group.worstFindingSeverity ?? "info"}`}>{group.findingCount} findings</span>
        )}
      </p>
      <p className="muted">
        Observed duration {formatMs(group.durationMs)} is the sum of member spans, not wall-clock elapsed time.
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
      <p className="eyebrow">{span.type.toUpperCase()}</p>
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
      <dl className="kv">
        <div><dt>Duration</dt><dd>{formatMs(span.duration_ms)}</dd></div>
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

function numberAttr(attributes: Record<string, unknown> | undefined, key: string): number | null {
  const value = attributes?.[key];
  if (typeof value === "number" && Number.isFinite(value)) return value;
  if (typeof value === "string" && value) {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : null;
  }
  return null;
}

function formatCost(usd: number): string {
  return `$${usd.toFixed(usd >= 1 ? 2 : 4)}`;
}

function spanEstimateLabel(span: Span): string {
  const status = typeof span.attributes?.["nm5.estimated_cost_status"] === "string"
    ? String(span.attributes["nm5.estimated_cost_status"])
    : null;
  const amount = numberAttr(span.attributes, "nm5.estimated_cost_usd");
  return formatEstimatedCostLabel(amount, status);
}

function Json({ label, value }: { label: string; value: unknown }) {
  if (value == null) return null;
  return (
    <>
      <h4>{label}</h4>
      <pre>{JSON.stringify(value, null, 2)}</pre>
    </>
  );
}
