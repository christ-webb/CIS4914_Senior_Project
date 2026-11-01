import assert from "node:assert/strict";
import test from "node:test";

import { computeTimeline, layoutSpans } from "../lib/layout.ts";
import { percentile, recentTraces, statusCounts } from "../lib/stats.ts";

const span = (id, parent, start, duration) => ({
  span_id: id,
  parent_span_id: parent,
  type: "tool",
  name: id,
  started_at: new Date(start).toISOString(),
  duration_ms: duration,
  status: "ok",
  input: null,
  output: null,
  attributes: {},
  token_count: 0,
  cost_usd: 0,
});

test("layoutSpans builds a tree with parent edges", () => {
  const spans = [span("root", null, 0, 100), span("a", "root", 0, 40), span("b", "root", 40, 60)];
  const { positions, edges } = layoutSpans(spans);
  assert.equal(edges.length, 2);
  assert.equal(positions.root.x, 0);
  assert.ok(positions.a.x > positions.root.x);
  assert.ok(positions.b.y > positions.a.y);
  assert.equal(positions.root.y, (positions.a.y + positions.b.y) / 2);
});

test("layoutSpans survives cycles", () => {
  const spans = [span("a", "b", 0, 1), span("b", "a", 0, 1)];
  assert.equal(Object.keys(layoutSpans(spans).positions).length, 2);
});

test("computeTimeline offsets spans from the earliest start", () => {
  const { rows, totalMs } = computeTimeline([span("a", null, 1000, 50), span("b", null, 1100, 100)]);
  assert.equal(totalMs, 200);
  assert.equal(rows[1].offsetMs, 100);
  assert.equal(rows[1].offsetPct, 50);
  assert.equal(rows[1].widthPct, 50);
});

test("percentile, statusCounts and recentTraces", () => {
  assert.equal(percentile([10, 20, 30, 40], 95), 40);
  assert.equal(percentile([], 95), 0);
  const t = (id, status, at) => ({ trace_id: id, status, started_at: at });
  const list = [t("2", "error", "2026-01-02T00:00:00Z"), t("1", "ok", "2026-01-01T00:00:00Z")];
  assert.deepEqual(statusCounts(list), { ok: 1, error: 1, unset: 0 });
  assert.equal(recentTraces(list, 1)[0].trace_id, "2");
});
