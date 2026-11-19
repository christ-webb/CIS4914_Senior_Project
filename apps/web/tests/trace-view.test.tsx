import { fireEvent, render, screen } from "@testing-library/react";
import { useState } from "react";
import { describe, expect, it } from "vitest";

import { TraceView } from "../components/traces/trace-view";
import type { DiagnosticFinding, Span, Trace } from "../lib/types";

function tool(id: string, name: string, start: number, status: Span["status"] = "ok"): Span {
  return {
    span_id: id,
    parent_span_id: "agent",
    type: "tool",
    name,
    started_at: new Date(start).toISOString(),
    duration_ms: 12,
    status,
    input: null,
    output: null,
    attributes: { "tool.name": name },
    token_count: 0,
    cost_usd: 0,
  };
}

function makeTrace(spans: Span[]): Trace {
  return {
    trace_id: "trace-1",
    project_id: "p",
    name: "Cursor run",
    started_at: new Date(0).toISOString(),
    duration_ms: 100,
    total_tokens: 0,
    total_cost_usd: 0,
    status: "ok",
    lifecycle: "completed",
    execution_state: "idle",
    attributes: { "nm5.source": "cursor" },
    spans: [
      {
        span_id: "agent",
        parent_span_id: null,
        type: "agent",
        name: "cursor",
        started_at: new Date(0).toISOString(),
        duration_ms: 100,
        status: "ok",
        input: null,
        output: null,
        attributes: {},
        token_count: 0,
        cost_usd: 0,
      },
      ...spans,
    ],
  };
}

const finding: DiagnosticFinding = {
  finding_id: "find-shell",
  trace_id: "trace-1",
  span_ids: ["s-fail", "s-ok"],
  detector_id: "tool_failure",
  detection_type: "tool_failure",
  severity: "warning",
  confidence: "heuristic",
  title: "Shell failed",
  explanation: "observed",
  evidence: ["status=error"],
  evidence_data: {},
  suggested_action: "inspect",
  first_observed_at: null,
  last_observed_at: null,
  detector_version: "1",
  provisional: false,
};

function Harness({ extraReads = 0, selectedFindingId = null }: { extraReads?: number; selectedFindingId?: string | null }) {
  const reads = Array.from({ length: 3 + extraReads }, (_, index) => tool(`r${index}`, "Read", index + 1));
  const trace = makeTrace([
    ...reads,
    tool("s-fail", "Shell", 50, "error"),
    tool("s-ok", "Shell", 51),
  ]);
  const [activeId, setActiveId] = useState<string | null>(null);
  return (
    <div style={{ width: 900, height: 640 }}>
      <TraceView
        trace={trace}
        findings={[finding]}
        selectedFindingId={selectedFindingId}
        activeId={activeId}
        onSelectSpan={setActiveId}
      />
    </div>
  );
}

describe("TraceView React Flow graph", () => {
  it("starts in overview and groups repeated Reads", () => {
    render(<Harness />);
    const graph = screen.getByTestId("execution-graph");
    expect(graph.dataset.graphMode).toBe("overview");
    expect(Number(graph.dataset.nodeCount)).toBeLessThan(8);
    expect(graph.dataset.fitNonce).toBe("0");
  });

  it("does not reset camera nonce or expansion when spans arrive from a poll", () => {
    const view = render(<Harness extraReads={0} />);
    fireEvent.click(screen.getByTestId("graph-mode-full"));
    const nonceAfterMode = screen.getByTestId("execution-graph").dataset.fitNonce;
    expect(nonceAfterMode).toBe("1");
    view.rerender(<Harness extraReads={2} />);
    const graph = screen.getByTestId("execution-graph");
    expect(graph.dataset.fitNonce).toBe(nonceAfterMode);
    expect(graph.dataset.graphMode).toBe("full");
  });

  it("fits on explicit mode changes", () => {
    render(<Harness />);
    expect(screen.getByTestId("execution-graph").dataset.fitNonce).toBe("0");
    fireEvent.click(screen.getByTestId("graph-mode-failure"));
    expect(screen.getByTestId("execution-graph").dataset.graphMode).toBe("failure");
    expect(screen.getByTestId("execution-graph").dataset.fitNonce).toBe("1");
  });

  it("finding selection focuses failure mode and all supporting spans", () => {
    const view = render(<Harness selectedFindingId={null} />);
    view.rerender(<Harness selectedFindingId="find-shell" />);
    const graph = screen.getByTestId("execution-graph");
    expect(graph.dataset.graphMode).toBe("failure");
    expect(Number(graph.dataset.fitNonce)).toBeGreaterThan(0);
    expect(graph.dataset.selected === "s-fail" || graph.innerHTML.includes("s-fail")).toBeTruthy();
  });
});
