import assert from "node:assert/strict";
import test from "node:test";

import { computeStats, filterTraces } from "../lib/stats.ts";

const make = (name, status, duration_ms, tokens, cost) => ({
  trace_id: name,
  project_id: "demo",
  name,
  started_at: "2026-01-01T00:00:00Z",
  duration_ms,
  total_tokens: tokens,
  total_cost_usd: cost,
  status,
});

const traces = [make("alpha", "ok", 100, 10, 0.5), make("beta", "error", 300, 30, 1.5)];

test("computeStats aggregates runs", () => {
  const stats = computeStats(traces);
  assert.equal(stats.totalRuns, 2);
  assert.equal(stats.errorRate, 0.5);
  assert.equal(stats.avgLatencyMs, 200);
  assert.equal(stats.totalTokens, 40);
  assert.equal(stats.totalCostUsd, 2);
});

test("computeStats handles empty input", () => {
  assert.equal(computeStats([]).errorRate, 0);
});

test("filterTraces filters by status and query", () => {
  assert.equal(filterTraces(traces, "error", "").length, 1);
  assert.equal(filterTraces(traces, "all", "ALP")[0].name, "alpha");
  assert.equal(filterTraces(traces, "ok", "beta").length, 0);
});
