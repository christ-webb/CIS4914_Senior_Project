import assert from "node:assert/strict";
import test from "node:test";

import {
  errorMessage,
  extractErrors,
  groupErrors,
  projectStats,
  spanPath,
  spanTypeStats,
  timeBuckets,
} from "../lib/analytics.ts";

const span = (id, parent, type, name, status, dur, extra = {}) => ({
  span_id: id,
  parent_span_id: parent,
  type,
  name,
  started_at: "2026-01-01T00:00:00Z",
  duration_ms: dur,
  status,
  input: null,
  output: null,
  attributes: {},
  token_count: 10,
  cost_usd: 0.5,
  ...extra,
});

const trace = (id, at, spans, status = "ok", project = "p") => ({
  trace_id: id,
  project_id: project,
  name: id,
  started_at: at,
  duration_ms: 100,
  total_tokens: 20,
  total_cost_usd: 1,
  status,
  attributes: {},
  spans,
});

const failing = (id, at, code) =>
  trace(
    id,
    at,
    [
      span("r" + id, null, "workflow", "wf", "error", 50),
      span("t" + id, "r" + id, "tool", "pay", "error", 30, { output: { error: `HTTP ${code} bad` } }),
    ],
    "error",
  );

test("errorMessage prefers output.error then falls back", () => {
  assert.equal(errorMessage(span("a", null, "tool", "x", "error", 1, { output: { error: "boom" } })), "boom");
  assert.equal(errorMessage(span("a", null, "tool", "x", "error", 1)), "x failed");
});

test("groupErrors merges the same failure with different numbers", () => {
  const traces = [failing("1", "2026-01-01T00:00:00Z", 502), failing("2", "2026-01-02T00:00:00Z", 503)];
  const groups = groupErrors(extractErrors(traces));
  const pay = groups.find((g) => g.name === "pay");
  assert.equal(pay.count, 2);
  assert.equal(pay.traceIds.length, 2);
  assert.equal(pay.lastSeen, "2026-01-01T00:00:00Z");
});

test("spanPath walks to the root and survives cycles", () => {
  const spans = [span("a", "b", "tool", "a", "ok", 1), span("b", "a", "tool", "b", "ok", 1)];
  assert.equal(spanPath(spans[0], spans).length, 2);
  const tree = [span("r", null, "agent", "r", "ok", 1), span("c", "r", "tool", "c", "ok", 1)];
  assert.deepEqual(spanPath(tree[1], tree).map((s) => s.span_id), ["r", "c"]);
});

test("spanTypeStats and projectStats aggregate", () => {
  const traces = [failing("1", "2026-01-01T00:00:00Z", 500), trace("2", "2026-01-01T00:00:00Z", [span("x", null, "tool", "t", "ok", 10)], "ok", "q")];
  const tool = spanTypeStats(traces).find((s) => s.type === "tool");
  assert.equal(tool.count, 2);
  assert.equal(tool.errors, 1);
  assert.equal(projectStats(traces).length, 2);
});

test("timeBuckets distributes runs", () => {
  const traces = [trace("1", "2026-01-01T00:00:00Z", []), trace("2", "2026-01-01T01:00:00Z", [], "error")];
  const buckets = timeBuckets(traces, 4);
  assert.equal(buckets.length, 4);
  assert.equal(buckets[0].runs, 1);
  assert.equal(buckets[3].errors, 1);
  assert.deepEqual(timeBuckets([], 4), []);
});
