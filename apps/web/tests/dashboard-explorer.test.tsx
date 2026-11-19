import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { DashboardView } from "../components/dashboard/dashboard-view";
import { TraceExplorer } from "../components/traces/trace-explorer";
import { LiveStatus } from "../components/shell/page-header";
import type { Trace } from "../lib/types";

const mocks = vi.hoisted(() => ({ replace: vi.fn(), push: vi.fn(), fetchTraces: vi.fn(), getTrace: vi.fn() }));
vi.mock("next/navigation", () => ({ useRouter: () => ({ replace: mocks.replace, push: mocks.push }), useSearchParams: () => new URLSearchParams(), usePathname: () => "/" }));
vi.mock("../lib/api", () => ({ fetchTraces: mocks.fetchTraces, getTrace: mocks.getTrace, fetchProjects: async () => ["test-project"] }));
const trace: Trace = { trace_id: "t1", name: "Cursor test run", project_id: "test-project", started_at: "2026-01-01T00:00:00Z", duration_ms: 100, total_tokens: 10, total_cost_usd: 0, status: "ok", lifecycle: "running", execution_state: "executing", source: "cursor", attributes: {}, spans: [] };
beforeEach(() => { vi.clearAllMocks(); mocks.fetchTraces.mockResolvedValue([trace]); mocks.getTrace.mockResolvedValue(trace); });

describe("Dashboard and history controls", () => {
  it("shows executing sessions and preserves direct investigation", async () => {
    render(<DashboardView />);
    await screen.findByText("1 executing");
    expect(screen.getAllByRole("link", { name: /Cursor test run/ })[0].getAttribute("href")).toBe("/traces/t1");
    fireEvent.click(screen.getByRole("button", { name: "Refresh" }));
    await screen.findByText("Updated just now");
    expect(mocks.fetchTraces.mock.calls.length).toBeGreaterThan(1);
  });
  it("preserves project filtering, paging and detail selection", async () => {
    mocks.fetchTraces.mockResolvedValue(Array.from({ length: 21 }, (_, i) => ({ ...trace, trace_id: `t${i}`, name: `Run ${i}` })));
    render(<TraceExplorer />);
    await screen.findByRole("button", { name: /Run 0/ });
    fireEvent.change(screen.getByLabelText("Filter by project"), { target: { value: "test-project" } });
    expect(mocks.replace).toHaveBeenCalledWith("/explorer?project=test-project");
    fireEvent.click(screen.getByRole("button", { name: "Next" }));
    expect(mocks.replace).toHaveBeenCalledWith("/explorer?page=2");
    fireEvent.click(screen.getByRole("button", { name: /Run 2/ }));
    await waitFor(() => expect(mocks.getTrace).toHaveBeenCalledWith("t2"));
  });
  it("confirms failed refresh without claiming success", async () => {
    render(<LiveStatus online={false} updatedAt={null} onRefresh={async () => undefined} />);
    fireEvent.click(screen.getByRole("button", { name: "Refresh" }));
    await screen.findByText("Refresh failed. Try again.");
    expect(screen.queryByText("Updated just now")).toBeNull();
  });
});
