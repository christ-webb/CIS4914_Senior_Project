"use client";

import { Background, Controls, Edge, Node, ReactFlow } from "@xyflow/react";
import { useMemo } from "react";

import type { Span, Trace } from "@/lib/types";

export function toGraph(spans: Span[]): { nodes: Node[]; edges: Edge[] } {
  const nodes = spans.map((span, index) => ({
    id: span.span_id,
    position: { x: 80 + (index % 2) * 280, y: 70 + index * 120 },
    data: { label: `${span.type.toUpperCase()} · ${span.name}` },
    className: span.status === "error" ? "error-node" : "trace-node",
  }));
  const byId = new Set(spans.map((span) => span.span_id));
  const edges = spans.flatMap((span, index) => {
    const fallbackParent = index > 0 ? spans[index - 1]?.span_id : undefined;
    const parent = span.parent_span_id ?? fallbackParent;
    return parent && byId.has(parent)
      ? [{ id: `${parent}-${span.span_id}`, source: parent, target: span.span_id }]
      : [];
  });
  return { nodes, edges };
}

export function TraceGraph({ trace }: { trace: Trace }) {
  const graph = useMemo(() => toGraph(trace.spans), [trace.spans]);

  return (
    <div className="graph-wrap">
      <div className="trace-heading">
        <div>
          <p className="eyebrow">TRACE</p>
          <h2>{trace.name}</h2>
        </div>
        <dl>
          <div><dt>Duration</dt><dd>{trace.duration_ms.toFixed(0)} ms</dd></div>
          <div><dt>Tokens</dt><dd>{trace.total_tokens}</dd></div>
          <div><dt>Cost</dt><dd>${trace.total_cost_usd.toFixed(4)}</dd></div>
        </dl>
      </div>
      <div className="graph" aria-label={`Execution graph for ${trace.name}`}>
        <ReactFlow nodes={graph.nodes} edges={graph.edges} fitView>
          <Background />
          <Controls />
        </ReactFlow>
      </div>
    </div>
  );
}

