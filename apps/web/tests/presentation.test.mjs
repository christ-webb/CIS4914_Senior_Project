import test from "node:test";
import assert from "node:assert/strict";
import { costPresentation, currency, spanTiming } from "../lib/presentation.ts";

test("complete estimates win over scenarios, including zero", () => {
  assert.equal(costPresentation({ estimated_cost_status: "complete", estimated_cost_usd: 0, scenario_standard_usd: 3, scenario_fast_usd: 6 }).label, "$0.00");
});
test("unverified estimates show supported scenario ranges without verification changes", () => {
  const record = { estimated_cost_status: "unavailable", scenario_standard_usd: 3.301884, scenario_fast_usd: 6.603768 };
  assert.equal(costPresentation(record).label, "$3.30–$6.60");
  assert.equal(record.estimated_cost_status, "unavailable");
  assert.match(costPresentation(record).qualification, /not an actual billed charge/);
});
test("partial, missing endpoints, and unavailable data are explicit", () => {
  assert.equal(costPresentation({ estimated_subtotal_usd: 0.18 }).label, "$0.18 partial");
  assert.equal(costPresentation({ scenario_standard_usd: 3.3 }).label, "$3.30 scenario");
  assert.equal(costPresentation({ scenario_fast_usd: NaN }).kind, "unavailable");
  assert.equal(costPresentation({}).label, "Not available");
  assert.equal(costPresentation({ attributes: { "nm5.estimated_subtotal_usd": "0" } }).label, "$0.00 partial");
});
test("currency preserves tiny nonzero amounts and grouping", () => {
  assert.equal(currency(0.001234), "$0.001234");
  assert.equal(currency(1234.5), "$1,234.50");
  assert.equal(currency(0.0000001), "< $0.000001");
});
test("zero hook and parent placeholders never claim measured timing", () => {
  assert.equal(spanTiming({ type: "tool", duration_ms: 0, attributes: { "nm5.span_source": "hooks" } }).label, "Not reported");
  assert.equal(spanTiming({ type: "agent", duration_ms: 0, attributes: {} }).label, "Not reported");
  assert.match(spanTiming({ type: "tool", duration_ms: 20, attributes: { "nm5.duration_source": "inferred" } }).label, /inferred/);
  assert.equal(spanTiming({ type: "tool", duration_ms: 0, attributes: {} }).label, "0 ms");
});
