import assert from "node:assert/strict";
import test from "node:test";

import { layoutSpans, mergeLayoutPositions } from "../lib/layout.ts";
import { isExecutingRun, isIdleOpenRun, needsLivePolling, traceLifecycle } from "../lib/types.ts";

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

test("traceLifecycle does not infer finalization from status", () => {
  assert.equal(traceLifecycle({ status: "unset" }), "unknown");
  assert.equal(traceLifecycle({ status: "ok" }), "unknown");
  assert.equal(traceLifecycle({ status: "error" }), "unknown");
  assert.equal(traceLifecycle({ status: "unset", lifecycle: "running" }), "running");
  assert.equal(isExecutingRun({ status: "unset", lifecycle: "interrupted", source: "cli" }), false);
  assert.equal(isExecutingRun({ status: "unset", lifecycle: "interrupted", source: "cursor", execution_state: "executing" }), false);
  assert.equal(isExecutingRun({ status: "unset", lifecycle: "running", source: "cursor", execution_state: "idle" }), false);
  assert.equal(isExecutingRun({ status: "unset", lifecycle: "running", source: "cursor", execution_state: "executing" }), true);
  assert.equal(
    isExecutingRun({
      status: "unset",
      lifecycle: "running",
      execution_state: "idle",
      attributes: { "nm5.source": "cursor" },
    }),
    false,
  );
  assert.equal(isIdleOpenRun({ status: "unset", lifecycle: "running", source: "cursor", execution_state: "idle" }), true);
  assert.equal(
    isIdleOpenRun({
      status: "unset",
      lifecycle: "running",
      execution_state: "idle",
      attributes: { "agent.framework": "cursor" },
    }),
    true,
  );
  assert.equal(needsLivePolling({ status: "unset", lifecycle: "interrupted", source: "cursor", execution_state: "executing" }), false);
});
