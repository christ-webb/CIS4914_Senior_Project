import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { TraceMetrics } from "../components/traces/trace-metrics";
import { Disclosure } from "../components/ui/disclosure";
import type { Trace } from "../lib/types";

const trace: Trace = { trace_id: "id", name: "Stored trace shape", project_id: "p", started_at: "2026-01-01", duration_ms: 100, total_tokens: 5249214, total_cost_usd: 0, status: "ok", spans: [], attributes: { "nm5.token_status": "reported", "nm5.usage_scope": "parent_agent", "nm5.usage.input_tokens": 5000000, "nm5.usage.output_tokens": 249214, "nm5.usage.cache_read_tokens": 4000000, "nm5.scenario_standard_usd": "3.301884", "nm5.scenario_fast_usd": "6.603768", "nm5.estimated_cost_status": "unavailable", "nm5.cost_status": "unavailable" } };

describe("Trace metrics", () => {
  it("shows the compact total and scenario range directly with no permanent breakdown", () => {
    render(<TraceMetrics trace={trace} />);
    expect(screen.getByText("5.25M")).toBeTruthy();
    expect(screen.getByText("$3.30–$6.60")).toBeTruthy();
    expect(screen.queryByText("Exact total")).toBeNull();
    expect(screen.queryByText("Actual billed cost")).toBeNull();
  });
  it("reveals exact accounting and coverage on hover and focus", () => {
    render(<TraceMetrics trace={trace} />);
    const trigger = screen.getByRole("button", { name: /Total tokens/ });
    fireEvent.mouseEnter(trigger);
    expect(screen.getByText("5,249,214")).toBeTruthy();
    expect(screen.getByText("Parent agent only")).toBeTruthy();
    expect(screen.getByText(/not added.*second time/)).toBeTruthy();
    fireEvent.mouseLeave(trigger.closest(".disclosure")!);
    expect(screen.queryByText("Exact total")).toBeNull();
    fireEvent.focus(trigger);
    expect(screen.getByText("Exact total")).toBeTruthy();
    fireEvent.keyDown(trigger, { key: "Escape" });
    expect(screen.queryByText("Exact total")).toBeNull();
  });
  it("keeps billed data separate and explains scenario uncertainty", () => {
    render(<TraceMetrics trace={trace} />);
    fireEvent.click(screen.getByRole("button", { name: /Estimated cost/ }));
    expect(screen.getByText("Actual billed cost")).toBeTruthy();
    expect(screen.getByText("Not reported")).toBeTruthy();
    expect(screen.getByText(/not an actual billed charge or bounds/)).toBeTruthy();
  });
  it("supports pinned tap, outside dismissal, close and Escape focus restoration", () => {
    render(<Disclosure label="Details"><p>Evidence</p></Disclosure>);
    const trigger = screen.getByRole("button", { name: "Details" });
    fireEvent.click(trigger);
    fireEvent.mouseLeave(trigger.closest(".disclosure")!);
    expect(screen.getByText("Evidence")).toBeTruthy();
    fireEvent.pointerDown(document.body);
    expect(screen.queryByText("Evidence")).toBeNull();
    fireEvent.click(trigger);
    fireEvent.click(screen.getByRole("button", { name: "Close Details details" }));
    expect(document.activeElement).toBe(trigger);
    expect(screen.queryByText("Evidence")).toBeNull();
  });
});
