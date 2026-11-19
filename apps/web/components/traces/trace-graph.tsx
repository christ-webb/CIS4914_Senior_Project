"use client";

import { ExecutionGraphCanvas } from "@/components/traces/execution-graph-canvas";
import { useVisibleGraph } from "@/hooks/use-visible-graph";
import { formatBilledCostLabel, formatEstimatedCostLabel, formatTokenLabel } from "@/lib/stats";
import type { Trace } from "@/lib/types";

export function TraceGraph({ trace }: { trace: Trace }) {
  const { graph, expanded, toggleGroup } = useVisibleGraph({
    spans: trace.spans,
    mode: "overview",
  });

  return (
    <div className="graph-wrap">
      <div className="trace-heading">
        <div>
          <p className="eyebrow">TRACE</p>
          <h2>{trace.name}</h2>
        </div>
        <dl>
          <div><dt>Duration</dt><dd>{trace.duration_ms.toFixed(0)} ms</dd></div>
          <div><dt>Tokens</dt><dd>{formatTokenLabel(trace.total_tokens, trace.token_status)}</dd></div>
          <div><dt>Estimated</dt><dd className="estimated-cost">{formatEstimatedCostLabel(trace.estimated_cost_usd, trace.estimated_cost_status)}</dd></div>
          <div><dt>Billed</dt><dd className="billed-cost">{formatBilledCostLabel(trace.total_cost_usd, trace.cost_status)}</dd></div>
        </dl>
      </div>
      <p className="graph-legend muted">
        Solid arrows are verified parent-child links. Dashed arrows are observed chronological order, not causality.
      </p>
      <ExecutionGraphCanvas
        graph={graph}
        activeId={null}
        expandedGroupIds={expanded}
        onSelect={() => undefined}
        onToggleGroup={toggleGroup}
        fitNonce={0}
        ariaLabel={`Execution graph for ${trace.name}`}
        mode="overview"
      />
    </div>
  );
}
