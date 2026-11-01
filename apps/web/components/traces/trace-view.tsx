"use client";

import {
  Background,
  Controls,
  Handle,
  MarkerType,
  MiniMap,
  Position,
  ReactFlow,
  type Edge,
  type Node,
  type NodeProps,
} from "@xyflow/react";
import { useMemo, useState } from "react";

import { computeTimeline, layoutSpans, NODE_H, NODE_W } from "@/lib/layout";
import { formatCost, formatMs } from "@/lib/stats";
import type { Span, Trace } from "@/lib/types";

type SpanNodeData = { span: Span; maxDuration: number };
type SpanNode = Node<SpanNodeData, "span">;

function SpanNodeView({ data, selected }: NodeProps<SpanNode>) {
  const { span, maxDuration } = data;
  const classes = ["span-node", `type-${span.type}`, span.status === "error" ? "is-error" : "", selected ? "is-selected" : ""];
  return (
    <div className={classes.join(" ")}>
      <Handle type="target" position={Position.Left} />
      <div className="span-node-top">
        <span className="type-pill">{span.type}</span>
        <span className="muted">{formatMs(span.duration_ms)}</span>
      </div>
      <div className="span-node-name" title={span.name}>{span.name}</div>
      <div className="span-node-bar"><i style={{ width: `${Math.max(4, (span.duration_ms / maxDuration) * 100)}%` }} /></div>
      <Handle type="source" position={Position.Right} />
    </div>
  );
}

const nodeTypes = { span: SpanNodeView };

export function toGraph(spans: Span[], activeId: string | null): { nodes: SpanNode[]; edges: Edge[] } {
  const layout = layoutSpans(spans);
  const maxDuration = Math.max(1, ...spans.map((span) => span.duration_ms));
  const byId = new Map(spans.map((span) => [span.span_id, span]));
  const nodes: SpanNode[] = spans.map((span) => ({
    id: span.span_id,
    type: "span",
    position: layout.positions[span.span_id],
    width: NODE_W,
    height: NODE_H,
    selected: span.span_id === activeId,
    data: { span, maxDuration },
  }));
  const edges: Edge[] = layout.edges.map((edge) => {
    const failed = byId.get(edge.target)?.status === "error";
    return {
      ...edge,
      type: "smoothstep",
      animated: !failed,
      markerEnd: { type: MarkerType.ArrowClosed, color: failed ? "#f87171" : "#64748b" },
      style: { stroke: failed ? "#f87171" : "#64748b", strokeWidth: 1.6 },
    };
  });
  return { nodes, edges };
}

type Tab = "graph" | "timeline" | "spans";
const TAB_LABELS: Record<Tab, string> = { graph: "Execution graph", timeline: "Timeline", spans: "Spans" };

export function TraceView({ trace }: { trace: Trace }) {
  const [tab, setTab] = useState<Tab>("graph");
  const [activeId, setActiveId] = useState<string | null>(null);
  const graph = useMemo(() => toGraph(trace.spans, activeId), [trace.spans, activeId]);
  const timeline = useMemo(() => computeTimeline(trace.spans), [trace.spans]);
  const active = trace.spans.find((span) => span.span_id === activeId) ?? null;
  const errors = trace.spans.filter((span) => span.status === "error").length;

  return (
    <div className="trace-view">
      <div className="trace-heading">
        <div>
          <p className="muted">
            <span className={`pill ${trace.status}`}>{trace.status}</span>{" "}
            {new Date(trace.started_at).toLocaleString()} · {trace.spans.length} spans
            {errors > 0 && ` · ${errors} failed`}
          </p>
        </div>
        <dl className="mini-stats">
          <div><dt>Duration</dt><dd>{formatMs(trace.duration_ms)}</dd></div>
          <div><dt>Tokens</dt><dd>{trace.total_tokens.toLocaleString()}</dd></div>
          <div><dt>Cost</dt><dd>{formatCost(trace.total_cost_usd)}</dd></div>
        </dl>
      </div>

      <div className="tabs" role="tablist">
        {(Object.keys(TAB_LABELS) as Tab[]).map((value) => (
          <button key={value} role="tab" aria-selected={tab === value} className={tab === value ? "tab active" : "tab"} onClick={() => setTab(value)}>
            {TAB_LABELS[value]}
          </button>
        ))}
      </div>

      <div className="view-body">
        <div className="view-main">
          {tab === "graph" ? (
            <div className="graph" aria-label={`Execution graph for ${trace.name}`}>
              <ReactFlow
                nodes={graph.nodes}
                edges={graph.edges}
                nodeTypes={nodeTypes}
                fitView
                fitViewOptions={{ padding: 0.2 }}
                nodesDraggable={false}
                colorMode="dark"
                onNodeClick={(_, node) => setActiveId(node.id)}
                onPaneClick={() => setActiveId(null)}
              >
                <Background gap={22} />
                <Controls showInteractive={false} />
                <MiniMap pannable zoomable maskColor="rgba(8,10,18,.7)" />
              </ReactFlow>
            </div>
          ) : tab === "spans" ? (
            <div className="table-scroll">
              <table className="table">
                <thead><tr><th>Span</th><th>Status</th><th>Duration</th><th>Tokens</th><th>Cost</th></tr></thead>
                <tbody>
                  {trace.spans.map((span) => (
                    <tr key={span.span_id} className={`clickable${span.span_id === activeId ? " selected" : ""}`} onClick={() => setActiveId(span.span_id)}>
                      <td><span className={`type-pill t-${span.type}`}>{span.type}</span> {span.name}</td>
                      <td><span className={`pill ${span.status}`}>{span.status}</span></td>
                      <td className="num">{formatMs(span.duration_ms)}</td>
                      <td className="num">{span.token_count.toLocaleString()}</td>
                      <td className="num">{formatCost(span.cost_usd)}</td>
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
          {active ? <SpanDetail span={active} /> : <p className="muted">Select a span to inspect its input, output and attributes.</p>}
        </aside>
      </div>
    </div>
  );
}

function SpanDetail({ span }: { span: Span }) {
  return (
    <>
      <p className="eyebrow">{span.type.toUpperCase()}</p>
      <h3>{span.name}</h3>
      <p><span className={`pill ${span.status}`}>{span.status}</span></p>
      <dl className="kv">
        <div><dt>Duration</dt><dd>{formatMs(span.duration_ms)}</dd></div>
        <div><dt>Tokens</dt><dd>{span.token_count.toLocaleString()}</dd></div>
        <div><dt>Cost</dt><dd>{formatCost(span.cost_usd)}</dd></div>
        <div><dt>Started</dt><dd>{new Date(span.started_at).toLocaleTimeString()}</dd></div>
      </dl>
      <Json label="Input" value={span.input} />
      <Json label="Output" value={span.output} />
      <Json label="Attributes" value={Object.keys(span.attributes).length ? span.attributes : null} />
    </>
  );
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
