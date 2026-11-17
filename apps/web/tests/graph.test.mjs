import assert from "node:assert/strict";
import test from "node:test";

import { layoutSpans, mergeLayoutPositions } from "../lib/layout.ts";
import { isActiveLifecycle, traceLifecycle } from "../lib/types.ts";

const span = (id, parent = null) => ({
  span_id: id,
  parent_span_id: parent,
  type: "tool",
  name: id,
  started_at: "2026-01-01T00:00:00Z",
  duration_ms: 10,
  status: "ok",
  input: null,
  output: null,
  attributes: {},
  token_count: 0,
  cost_usd: 0,
});

test("mergeLayoutPositions keeps previous node coordinates", () => {
  const first = layoutSpans([span("a"), span("b", "a")]);
  const moved = { ...first.positions, a: { x: 9, y: 9 } };
  const merged = mergeLayoutPositions(moved, layoutSpans([span("a"), span("b", "a"), span("c", "b")]));
  assert.deepEqual(merged.positions.a, { x: 9, y: 9 });
  assert.ok(merged.positions.c);
});

test("traceLifecycle treats missing field as unknown unless status is terminal", () => {
  assert.equal(traceLifecycle({ status: "unset" }), "unknown");
  assert.equal(traceLifecycle({ status: "ok" }), "completed");
  assert.equal(traceLifecycle({ status: "error" }), "failed");
  assert.equal(traceLifecycle({ status: "unset", lifecycle: "running" }), "running");
  assert.equal(isActiveLifecycle("interrupted"), true);
  assert.equal(isActiveLifecycle("completed"), false);
});
