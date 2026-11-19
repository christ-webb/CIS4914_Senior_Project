import assert from "node:assert/strict";
import test from "node:test";

import {
  formatRate,
  recentCompletedTurns,
  sessionLabel,
  sessionOutcomes,
  taskOutcomes,
  turnCompletion,
  turnLabel,
  turnSummary,
} from "../lib/outcomes.ts";

const run = (overrides = {}) => ({
  trace_id: "t",
  lifecycle: "running",
  execution_state: "idle",
  turns_total: 1,
  turns_completed: 1,
  turns_failed: 0,
  turns_interrupted: 0,
  turns_executing: 0,
  turns_provisional: 0,
  latest_turn_status: "completed",
  latest_turn_completed_at: "2026-01-01T00:00:04Z",
  task_outcome: "unverified",
  ...overrides,
});

test("README turn in an open session counts as a completed turn, not a failure", () => {
  const traces = [run()];
  const turns = turnCompletion(traces);
  assert.equal(turns.rate, 1);
  assert.equal(turns.terminal, 1);
  assert.equal(formatRate(turns.rate), "100%");
  const sessions = sessionOutcomes(traces);
  assert.deepEqual([sessions.open, sessions.failed, sessions.finalized], [1, 0, 0]);
  const tasks = taskOutcomes(traces);
  assert.equal(tasks.verifiedRate, null);
  assert.equal(tasks.unverified, 1);
  assert.equal(turnLabel(traces[0]), "Turn completed");
  assert.equal(sessionLabel(traces[0]), "Idle / Open");
});

test("closing the chat mid-turn keeps completed turns while the session is cancelled", () => {
  const closed = run({ lifecycle: "cancelled", execution_state: "idle", ended_at: "2026-01-01T00:01:00Z" });
  assert.equal(turnCompletion([closed]).rate, 1);
  assert.equal(sessionOutcomes([closed]).cancelled, 1);
  assert.equal(sessionLabel(closed), "Cancelled");
});

test("a chat closed after its turn completed is a completed session, not verified success", () => {
  const closed = run({ lifecycle: "completed", execution_state: "idle", ended_at: "2026-01-01T00:01:00Z" });
  const sessions = sessionOutcomes([closed]);
  assert.deepEqual([sessions.finalized, sessions.cancelled], [1, 0]);
  assert.equal(sessionLabel(closed), "Session finalized");
  assert.deepEqual(recentCompletedTurns([closed]), [closed]);
  const tasks = taskOutcomes([closed]);
  assert.deepEqual([tasks.unverified, tasks.verifiedSuccess, tasks.verifiedRate], [1, 0, null]);
});

test("denominators exclude executing, provisional and turnless sessions", () => {
  const traces = [
    run({ turns_total: 3, turns_completed: 2, turns_failed: 1, latest_turn_status: "failed" }),
    run({ turns_total: 1, turns_completed: 0, turns_interrupted: 1, latest_turn_status: "interrupted" }),
    run({ turns_total: 1, turns_completed: 0, turns_executing: 1, latest_turn_status: "executing", execution_state: "executing" }),
    run({ turns_total: 1, turns_completed: 0, turns_provisional: 1, latest_turn_status: "response_observed" }),
    run({ turns_total: 0, turns_completed: 0, latest_turn_status: null, lifecycle: "cancelled" }),
  ];
  const turns = turnCompletion(traces);
  assert.deepEqual(
    [turns.completed, turns.failed, turns.interrupted, turns.terminal, turns.executing, turns.provisional],
    [2, 1, 1, 4, 1, 1],
  );
  assert.equal(turns.rate, 0.5);
  assert.equal(turnCompletion([]).rate, null);
  assert.equal(formatRate(null), "—");
});

test("reported outcomes never count as verified success", () => {
  const tasks = taskOutcomes([
    run({ task_outcome: "reported_success" }),
    run({ task_outcome: "reported_failure" }),
    run({ task_outcome: "verified_success" }),
    run({ task_outcome: "verified_failure" }),
    run(),
  ]);
  assert.equal(tasks.verifiedRate, 0.5);
  assert.equal(tasks.reportedSuccess, 1);
  assert.equal(tasks.unverified, 1);
});

test("full traces read the rollup from attributes; batch traces fall back to status", () => {
  const full = { lifecycle: "running", attributes: { "nm5.turns_total": 2, "nm5.turns_completed": 2, "nm5.latest_turn_status": "completed", "nm5.task_outcome_status": "unverified" } };
  assert.equal(turnSummary(full).completed, 2);
  const batch = { lifecycle: "completed", ended_at: "2026-01-01", attributes: {} };
  assert.equal(turnSummary(batch).latestEvidence, "batch.status");
  assert.equal(turnSummary({ lifecycle: "unknown", attributes: {} }).total, 0);
});

test("recent completed turns include open sessions ordered by completion time", () => {
  const ordered = recentCompletedTurns([
    run({ trace_id: "old", latest_turn_completed_at: "2026-01-01T00:00:00Z", lifecycle: "completed" }),
    run({ trace_id: "open", latest_turn_completed_at: "2026-01-02T00:00:00Z" }),
    run({ trace_id: "none", turns_completed: 0 }),
  ]);
  assert.deepEqual(ordered.map((trace) => trace.trace_id), ["open", "old"]);
});
