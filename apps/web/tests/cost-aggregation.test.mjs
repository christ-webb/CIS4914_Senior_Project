import assert from "node:assert/strict";
import test from "node:test";
import { aggregateEstimatedCost, ordinaryRuns } from "../lib/cost-aggregation.ts";
const base = { trace_id: "a", status: "unset", total_cost_usd: 999, total_tokens: 100, duration_ms: 0, started_at: "2026-01-01", name: "run", project_id: "p" };
const range = (id, min, max) => ({ ...base, trace_id: id, scenario_standard_usd: min, scenario_fast_usd: max, covered_generations: 2, total_generations: 2 });
test("scenario sums preserve supported window coverage", () => {
  const sum = aggregateEstimatedCost([range("a", .2, .4), range("b", .3, .5)]);
  assert.equal(sum.minimum, .5); assert.equal(sum.maximum, .9); assert.equal(sum.fullCoverage, true);
});
test("points win over alternate fields and combine with ranges once", () => {
  const a = { ...range("a", 3, 6), estimated_cost_status: "complete", estimated_cost_usd: 1, estimated_subtotal_usd: 2 };
  const sum = aggregateEstimatedCost([a, a, range("b", 2, 4)]);
  assert.equal(sum.minimum, 3); assert.equal(sum.maximum, 5); assert.equal(sum.total, 2);
});
test("partial usage and unavailable runs qualify coverage without hiding subtotal", () => {
  const sum = aggregateEstimatedCost([{ ...base, estimated_subtotal_usd: .5 }, { ...base, trace_id: "b" }]);
  assert.equal(sum.minimum, .5); assert.equal(sum.fullCoverage, false); assert.equal(sum.priced, 1); assert.equal(sum.total, 2);
  assert.match(sum.coverage, /Priced subset/);
});
test("only explicit fixtures are excluded; missing provenance is included", () => {
  assert.equal(ordinaryRuns([base, { ...base, trace_id: "b", is_fixture: true }]).length, 1);
  assert.equal(aggregateEstimatedCost([base]).label, "Not available");
});
test("invalid amounts never become zero; a verified zero is valid", () => {
  assert.equal(aggregateEstimatedCost([{ ...base, estimated_cost_status: "complete", estimated_cost_usd: NaN }]).priced, 0);
  assert.equal(aggregateEstimatedCost([{ ...base, estimated_cost_status: "complete", estimated_cost_usd: 0 }]).priced, 1);
});
test("explicit source-scoped logical identity chooses the latest snapshot", () => {
  const older = { ...base, logical_run_id: '["cursor","p","conversation"]', estimated_cost_status: "complete", estimated_cost_usd: 1 };
  const newer = { ...older, trace_id: "b", started_at: "2026-01-02", estimated_cost_usd: 2 };
  const sum = aggregateEstimatedCost([older, newer]);
  assert.equal(sum.total, 1); assert.equal(sum.minimum, 2);
  assert.equal(aggregateEstimatedCost([older, { ...newer, logical_run_id: '["codex","p","conversation"]' }]).total, 2);
});
