import { readFileSync } from "node:fs";
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { TraceView } from "../components/traces/trace-view";
import { costPresentation } from "../lib/presentation";
import type { Trace } from "../lib/types";

// Opt-in, read-only validation against locally fetched telemetry. Never commit trace payloads.
const path = process.env.NM5_VALIDATION_TRACE;
describe.skipIf(!path)("Stored Cursor trace validation", () => {
  it("renders measured cumulative usage, scenarios and failures without resetting live graph state", () => {
    const trace = JSON.parse(readFileSync(path!, "utf8")) as Trace;
    expect(trace.attributes["nm5.usage_scope"]).toBe("parent_agent");
    expect(trace.total_tokens).toBeGreaterThan(1000000);
    const cost = costPresentation(trace);
    expect(cost.kind).toBe("range");
    const view = render(<TraceView trace={trace} />);
    expect(screen.getByText(cost.label)).toBeTruthy();
    expect(screen.getByText(String(trace.spans.filter(s => s.status === "error").length))).toBeTruthy();
    fireEvent.focus(screen.getByRole("button", { name: /^Total tokens/ }));
    expect(screen.getByText(trace.total_tokens.toLocaleString())).toBeTruthy();
    expect(screen.getByText("Parent agent only")).toBeTruthy();
    fireEvent.keyDown(screen.getByRole("button", { name: /^Total tokens/ }), { key: "Escape" });
    fireEvent.click(screen.getByRole("button", { name: /^Estimated cost/ }));
    if (trace.spans.some(span => span.attributes["nm5.pricing_details"])) {
      expect(screen.getAllByText("input / 1M tokens").length).toBeGreaterThan(0);
    }
    fireEvent.keyDown(screen.getByRole("button", { name: /^Estimated cost/ }), { key: "Escape" });
    const toggles = screen.getAllByRole("button", { name: "Expand" });
    expect(toggles.length).toBeGreaterThan(0);
    fireEvent.click(toggles[0]);
    const graph = screen.getByTestId("execution-graph");
    const expanded = graph.dataset.expandedCount;
    view.rerender(<TraceView trace={{ ...trace, spans: [...trace.spans] }} />);
    expect(graph.dataset.expandedCount).toBe(expanded);
    expect(graph.dataset.fitNonce).toBe("0");
    fireEvent.click(screen.getByRole("button", { name: "Inspect failures" }));
    expect(graph.dataset.graphMode).toBe("failure");
  });
});
