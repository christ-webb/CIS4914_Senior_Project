import assert from "node:assert/strict";
import test from "node:test";

import { computeStats, filterTraces, formatCostLabel, formatEstimatedCostLabel, formatTokenLabel } from "../lib/stats.ts";

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
  assert.equal(stats.estimatedCostUsd, 0);
});

test("computeStats handles empty input", () => {
  assert.equal(computeStats([]).errorRate, 0);
});

test("formatCostLabel distinguishes unavailable cost", () => {
  assert.equal(formatCostLabel(0, "unavailable"), "Not reported");
  assert.equal(formatCostLabel(0, "reported"), "$0.0000");
  assert.equal(formatCostLabel(0), "Not reported");
});

test("formatTokenLabel distinguishes unavailable tokens", () => {
  assert.equal(formatTokenLabel(0, "unavailable"), "Not reported");
  assert.equal(formatTokenLabel(0, "reported"), "0");
  assert.equal(formatTokenLabel(0), "Not reported");
});

test("computeStats excludes unavailable usage from measured totals", () => {
  const mixed = [
    { ...make("alpha", "ok", 100, 10, 0.5), token_status: "reported", cost_status: "reported" },
    { ...make("beta", "error", 300, 0, 0), token_status: "unavailable", cost_status: "unavailable" },
  ];
  const stats = computeStats(mixed);
  assert.equal(stats.totalTokens, 10);
  assert.equal(stats.totalCostUsd, 0.5);
});

test("filterTraces filters by status and query", () => {
  assert.equal(filterTraces(traces, "error", "").length, 1);
  assert.equal(filterTraces(traces, "all", "ALP")[0].name, "alpha");
  assert.equal(filterTraces(traces, "ok", "beta").length, 0);
});

test("computeStats excludes incomplete estimates from estimated totals", () => {
  const mixed = [
    {
      ...make("alpha", "ok", 100, 10, 0.5),
      estimated_cost_status: "complete",
      estimated_cost_usd: 0.12,
    },
    {
      ...make("beta", "error", 300, 30, 1.5),
      estimated_cost_status: "unavailable",
      estimated_cost_usd: null,
      scenario_standard_usd: 0.2,
      scenario_fast_usd: 0.8,
    },
  ];
  const stats = computeStats(mixed);
  assert.equal(stats.estimatedCostUsd, 0.12);
  assert.equal(stats.totalCostUsd, 2);
});

test("formatEstimatedCostLabel does not present scenarios as actual", () => {
  assert.equal(formatEstimatedCostLabel(null, "unavailable"), "Estimate unavailable");
  assert.equal(formatEstimatedCostLabel(0.12, "complete"), "$0.1200");
});
