"use client";

import {
  Background,
  Controls,
  Handle,
  MarkerType,
  MiniMap,
  Position,
  ReactFlow,
  ReactFlowProvider,
  useReactFlow,
  type Edge,
  type Node,
  type NodeProps,
} from "@xyflow/react";
import { useEffect, useMemo, useRef } from "react";

import { spanHighlightClass } from "@/lib/diagnostics";
import {
  FULL_TRACE_VIRTUALIZE_AFTER,
  groupHighlightClass,
  type GraphEdge,
  type GraphMode,
  type SpanGroup,
  type VisibleGraph,
} from "@/lib/execution-graph";
import { NODE_H, NODE_W } from "@/lib/layout";
import { spanTiming } from "@/lib/presentation";
import { formatMs } from "@/lib/stats";
import type { DiagnosticFinding, Span } from "@/lib/types";

type SpanNodeData = { span: Span; maxDuration: number; highlight: string };
type GroupNodeData = {
  group: SpanGroup;
  timingLabel: string;
  highlight: string;
  expanded: boolean;
  onToggle: (id: string) => void;
};
type GraphNode = Node<SpanNodeData, "span"> | Node<GroupNodeData, "group">;

function SpanNodeView({ data, selected }: NodeProps<Node<SpanNodeData, "span">>) {
  const { span, maxDuration, highlight } = data;
  const classes = ["span-node", `type-${span.type}`, highlight, selected ? "is-selected" : ""];
  return (
    <div className={classes.join(" ")}>
      <Handle type="target" position={Position.Left} />
      <div className="span-node-top">
        <span className="type-pill">{span.type}</span>
        <span className="muted">{spanTiming(span).label}</span>
      </div>
      <div className="span-node-name" title={span.name}>{span.name}</div>
      <div className="span-node-bar"><i style={{ width: `${Math.max(4, (span.duration_ms / maxDuration) * 100)}%` }} /></div>
      <Handle type="source" position={Position.Right} />
    </div>
  );
}

function GroupNodeView({ data, selected }: NodeProps<Node<GroupNodeData, "group">>) {
  const { group, highlight, expanded, onToggle } = data;
  const classes = ["span-node", "group-node", `type-${group.type}`, highlight, selected ? "is-selected" : ""];
  return (
    <div className={classes.join(" ")}>
      <Handle type="target" position={Position.Left} />
      <div className="span-node-top">
        <span className="type-pill">{group.type} group</span>
        <span className="muted" title="Sum of member durations; not wall-clock elapsed if operations overlap.">
          {data.timingLabel}
        </span>
      </div>
      <div className="span-node-name" title={group.name}>
        {group.name} · {group.memberIds.length} calls
      </div>
      <div className="group-meta">
        {group.failedCount > 0 && <span className="pill error">{group.failedCount} failed</span>}
        {group.findingCount > 0 && (
          <span className={`pill ${group.worstFindingSeverity ?? "info"}`}>{group.findingCount} finding{group.findingCount === 1 ? "" : "s"}</span>
        )}
        <span className="muted">{group.statuses.ok} ok</span>
        <button
          type="button"
          className="group-toggle"
          aria-expanded={expanded}
          data-testid={`group-toggle-${group.id}`}
          onClick={(event) => {
            event.stopPropagation();
            onToggle(group.id);
          }}
        >
          {expanded ? "Collapse" : "Expand"}
        </button>
      </div>
      <Handle type="source" position={Position.Right} />
    </div>
  );
}

const nodeTypes = { span: SpanNodeView, group: GroupNodeView };

function edgeStyle(edge: GraphEdge, failed: boolean) {
  const color = failed ? "#b42318" : edge.kind === "parent" ? "#0021a5" : "#64748b";
  return {
    type: edge.kind === "order" ? "default" : "smoothstep",
    animated: false,
    markerEnd: { type: MarkerType.ArrowClosed, color },
    style: {
      stroke: color,
      strokeWidth: edge.kind === "parent" ? 1.8 : 1.2,
      strokeDasharray: edge.kind === "parent" ? undefined : "5 4",
    },
    className: `edge-${edge.kind}`,
    data: { kind: edge.kind },
  };
}

export function toFlowGraph(
  graph: VisibleGraph,
  activeId: string | null,
  findings: DiagnosticFinding[],
  selectedFindingId: string | null,
  expandedGroupIds: Set<string>,
  onToggleGroup: (id: string) => void,
): { nodes: GraphNode[]; edges: Edge[] } {
  const spans = [...graph.spanById.values()];
  const maxDuration = Math.max(1, ...spans.map((span) => span.duration_ms), ...graph.groups.map((group) => group.durationMs));
  const nodes: GraphNode[] = graph.nodes.map((node) => {
    const position = graph.layout.positions[node.id] ?? { x: 0, y: 0 };
    if (node.kind === "group" && node.group) {
      return {
        id: node.id,
        type: "group",
        position,
        width: NODE_W,
        height: NODE_H,
        selected: node.id === activeId,
        data: {
          group: node.group,
          timingLabel: node.group.memberIds.some(id => { const span = graph.spanById.get(id); return span && spanTiming(span).label === "Not reported"; }) ? "Timing incomplete" : `${formatMs(node.group.durationMs)} sum`,
          highlight: groupHighlightClass(node.group, findings, selectedFindingId),
          expanded: expandedGroupIds.has(node.group.id),
          onToggle: onToggleGroup,
        },
      };
    }
    const span = node.span!;
    return {
      id: node.id,
      type: "span",
      position,
      width: NODE_W,
      height: NODE_H,
      selected: node.id === activeId,
      data: {
        span,
        maxDuration,
        highlight: spanHighlightClass(span.span_id, span.status, findings, selectedFindingId),
      },
    };
  });
  const edges: Edge[] = graph.edges.map((edge) => {
    const targetSpan = graph.spanById.get(edge.target);
    const targetGroup = graph.groups.find((group) => group.id === edge.target);
    const failed = targetSpan?.status === "error" || (targetGroup?.failedCount ?? 0) > 0;
    return { id: edge.id, source: edge.source, target: edge.target, ...edgeStyle(edge, failed) };
  });
  return { nodes, edges };
}

function GraphInner({
  graph,
  activeId,
  findings,
  selectedFindingId,
  expandedGroupIds,
  onSelect,
  onToggleGroup,
  fitNonce,
  fitNodeIds,
  ariaLabel,
  mode,
}: {
  graph: VisibleGraph;
  activeId: string | null;
  findings: DiagnosticFinding[];
  selectedFindingId: string | null;
  expandedGroupIds: Set<string>;
  onSelect: (id: string | null) => void;
  onToggleGroup: (id: string) => void;
  fitNonce: number;
  fitNodeIds: string[] | null;
  ariaLabel: string;
  mode: GraphMode;
}) {
  const { fitView } = useReactFlow();
  const skipFirstEffect = useRef(true);
  const flow = useMemo(
    () => toFlowGraph(graph, activeId, findings, selectedFindingId, expandedGroupIds, onToggleGroup),
    [graph, activeId, findings, selectedFindingId, expandedGroupIds, onToggleGroup],
  );

  const applyFit = (nonce: number) => {
    const nodes = fitNodeIds?.length ? fitNodeIds.map((id) => ({ id })) : undefined;
    fitView({ padding: 0.2, nodes, duration: nonce === 0 || window.matchMedia?.("(prefers-reduced-motion: reduce)").matches ? 0 : 200 });
  };

  useEffect(() => {
    if (skipFirstEffect.current) {
      skipFirstEffect.current = false;
      return;
    }
    applyFit(fitNonce);
    // eslint-disable-next-line react-hooks/exhaustive-deps -- poll must not fit; nonce is the intent signal
  }, [fitNonce]);

  return (
    <div
      className="graph"
      aria-label={ariaLabel}
      data-testid="execution-graph"
      data-fit-nonce={String(fitNonce)}
      data-graph-mode={mode}
      data-node-count={String(flow.nodes.length)}
      data-expanded-count={String(expandedGroupIds.size)}
      data-selected={activeId ?? ""}
    >
      <ReactFlow
        nodes={flow.nodes}
        edges={flow.edges}
        nodeTypes={nodeTypes}
        fitView={false}
        minZoom={0.15}
        nodesDraggable={false}
        colorMode="light"
        onlyRenderVisibleElements={flow.nodes.length > FULL_TRACE_VIRTUALIZE_AFTER}
        onNodeClick={(_, node) => onSelect(node.id)}
        onPaneClick={() => onSelect(null)}
        onInit={() => applyFit(0)}
      >
        <Background gap={22} />
        <Controls showInteractive={false} />
        <MiniMap pannable zoomable maskColor="rgba(243,245,248,.7)" />
      </ReactFlow>
    </div>
  );
}

export function ExecutionGraphCanvas(props: {
  graph: VisibleGraph;
  activeId: string | null;
  findings?: DiagnosticFinding[];
  selectedFindingId?: string | null;
  expandedGroupIds: Set<string>;
  onSelect: (id: string | null) => void;
  onToggleGroup: (id: string) => void;
  fitNonce: number;
  fitNodeIds?: string[] | null;
  ariaLabel: string;
  mode: GraphMode;
}) {
  return (
    <ReactFlowProvider>
      <GraphInner
        {...props}
        findings={props.findings ?? []}
        selectedFindingId={props.selectedFindingId ?? null}
        fitNodeIds={props.fitNodeIds ?? null}
      />
    </ReactFlowProvider>
  );
}
