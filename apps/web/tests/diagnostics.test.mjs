import assert from "node:assert/strict";
import test from "node:test";

import { findingsForSpan, sortFindings, spanHighlightClass } from "../lib/diagnostics.ts";

const finding = (id, spanIds, severity = "warning", confidence = "heuristic") => ({
  finding_id: id,
  trace_id: "t1",
  span_ids: spanIds,
  detector_id: "tool_failure",
  detection_type: "tool_failure",
  severity,
  confidence,
  title: id,
  explanation: "observed",
  evidence: ["span status=error"],
  evidence_data: {},
  suggested_action: "inspect",
  first_observed_at: null,
  last_observed_at: null,
  detector_version: "1",
  provisional: false,
});

test("spanHighlightClass marks confirmed critical spans as errors and warnings separately", () => {
  const findings = [finding("a", ["s1"], "critical", "confirmed"), finding("b", ["s2"], "warning")];
  assert.match(spanHighlightClass("s1", "error", findings, null), /is-error/);
  assert.match(spanHighlightClass("s2", "ok", findings, null), /is-warning/);
  assert.match(spanHighlightClass("s2", "ok", findings, "b"), /is-finding-selected/);
  assert.equal(spanHighlightClass("s3", "ok", findings, null), "");
});

test("sortFindings puts critical before warning", () => {
  const sorted = sortFindings([finding("w", ["s"], "warning"), finding("c", ["s"], "critical")]);
  assert.equal(sorted[0].finding_id, "c");
});

test("findingsForSpan keeps selection helpers stable across extra findings", () => {
  const findings = [finding("a", ["s1"]), finding("b", ["s1", "s2"])];
  assert.equal(findingsForSpan(findings, "s1").length, 2);
  assert.deepEqual(findingsForSpan(findings, "s2").map((item) => item.finding_id), ["b"]);
});
