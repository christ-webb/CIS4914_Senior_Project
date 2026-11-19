import type { RunLifecycle, TraceSummary } from "./types.ts";

export type TurnStatus = "executing" | "response_observed" | "completed" | "failed" | "interrupted";
export type TaskOutcome = "unverified" | "reported_success" | "reported_failure" | "verified_success" | "verified_failure";

type TurnSource = Partial<Pick<TraceSummary,
  | "turns_total" | "turns_completed" | "turns_failed" | "turns_interrupted" | "turns_executing" | "turns_provisional"
  | "latest_turn_status" | "latest_turn_evidence" | "latest_turn_completed_at" | "task_outcome" | "task_outcome_source"
  | "lifecycle" | "execution_state" | "ended_at"
>> & { attributes?: Record<string, unknown> };

export interface TurnSummary {
  total: number;
  completed: number;
  failed: number;
  interrupted: number;
  executing: number;
  provisional: number;
  latest: TurnStatus | null;
  latestEvidence: string | null;
  latestCompletedAt: string | null;
  taskOutcome: TaskOutcome;
  taskOutcomeSource: string | null;
}

function num(value: unknown): number | undefined {
  return typeof value === "number" && Number.isFinite(value) ? value : undefined;
}

function str(value: unknown): string | undefined {
  return typeof value === "string" && value ? value : undefined;
}

/** Normalizes list summaries and full traces (which carry the rollup only in attributes). */
export function turnSummary(trace: TurnSource): TurnSummary {
  const a = trace.attributes ?? {};
  const hasRollup = trace.turns_total !== undefined || a["nm5.turns_total"] !== undefined;
  const outcome = (trace.task_outcome ?? str(a["nm5.task_outcome_status"]) ?? "unverified") as TaskOutcome;
  const outcomeSource = trace.task_outcome_source ?? str(a["nm5.task_outcome_source"]) ?? null;
  if (!hasRollup) {
    const status = trace.lifecycle === "completed" ? "completed" : trace.lifecycle === "failed" ? "failed" : null;
    return {
      total: status ? 1 : 0, completed: status === "completed" ? 1 : 0, failed: status === "failed" ? 1 : 0,
      interrupted: 0, executing: 0, provisional: 0, latest: status, latestEvidence: status ? "batch.status" : null,
      latestCompletedAt: status === "completed" ? trace.ended_at ?? null : null, taskOutcome: outcome, taskOutcomeSource: outcomeSource,
    };
  }
  return {
    total: trace.turns_total ?? num(a["nm5.turns_total"]) ?? 0,
    completed: trace.turns_completed ?? num(a["nm5.turns_completed"]) ?? 0,
    failed: trace.turns_failed ?? num(a["nm5.turns_failed"]) ?? 0,
    interrupted: trace.turns_interrupted ?? num(a["nm5.turns_interrupted"]) ?? 0,
    executing: trace.turns_executing ?? num(a["nm5.turns_executing"]) ?? 0,
    provisional: trace.turns_provisional ?? num(a["nm5.turns_provisional"]) ?? 0,
    latest: (trace.latest_turn_status ?? str(a["nm5.latest_turn_status"]) ?? null) as TurnStatus | null,
    latestEvidence: trace.latest_turn_evidence ?? str(a["nm5.latest_turn_evidence"]) ?? null,
    latestCompletedAt: trace.latest_turn_completed_at ?? str(a["nm5.latest_turn_completed_at"]) ?? null,
    taskOutcome: outcome,
    taskOutcomeSource: outcomeSource,
  };
}

export interface TurnCompletion {
  completed: number;
  failed: number;
  interrupted: number;
  /** Turns with authoritative terminal evidence: the rate denominator. */
  terminal: number;
  executing: number;
  provisional: number;
  /** null when no terminal turns were observed. */
  rate: number | null;
}

export function turnCompletion(traces: TurnSource[]): TurnCompletion {
  const totals = { completed: 0, failed: 0, interrupted: 0, executing: 0, provisional: 0 };
  for (const trace of traces) {
    const turns = turnSummary(trace);
    totals.completed += turns.completed;
    totals.failed += turns.failed;
    totals.interrupted += turns.interrupted;
    totals.executing += turns.executing;
    totals.provisional += turns.provisional;
  }
  const terminal = totals.completed + totals.failed + totals.interrupted;
  return { ...totals, terminal, rate: terminal ? totals.completed / terminal : null };
}

export interface TaskOutcomes {
  verifiedSuccess: number;
  verifiedFailure: number;
  reportedSuccess: number;
  reportedFailure: number;
  unverified: number;
  /** Independently verified outcomes only; null when none exist. */
  verifiedRate: number | null;
}

export function taskOutcomes(traces: TurnSource[]): TaskOutcomes {
  const counts: TaskOutcomes = { verifiedSuccess: 0, verifiedFailure: 0, reportedSuccess: 0, reportedFailure: 0, unverified: 0, verifiedRate: null };
  for (const trace of traces) {
    const outcome = turnSummary(trace).taskOutcome;
    if (outcome === "verified_success") counts.verifiedSuccess += 1;
    else if (outcome === "verified_failure") counts.verifiedFailure += 1;
    else if (outcome === "reported_success") counts.reportedSuccess += 1;
    else if (outcome === "reported_failure") counts.reportedFailure += 1;
    else counts.unverified += 1;
  }
  const verified = counts.verifiedSuccess + counts.verifiedFailure;
  counts.verifiedRate = verified ? counts.verifiedSuccess / verified : null;
  return counts;
}

export interface SessionOutcomes {
  finalized: number;
  failed: number;
  cancelled: number;
  interrupted: number;
  open: number;
  unknown: number;
  total: number;
}

export function sessionOutcomes(traces: Pick<TurnSource, "lifecycle">[]): SessionOutcomes {
  const counts: SessionOutcomes = { finalized: 0, failed: 0, cancelled: 0, interrupted: 0, open: 0, unknown: 0, total: traces.length };
  for (const trace of traces) {
    const lifecycle: RunLifecycle = trace.lifecycle ?? "unknown";
    if (lifecycle === "completed") counts.finalized += 1;
    else if (lifecycle === "running") counts.open += 1;
    else if (lifecycle === "failed" || lifecycle === "cancelled" || lifecycle === "interrupted") counts[lifecycle] += 1;
    else counts.unknown += 1;
  }
  return counts;
}

export const TURN_LABELS: Record<TurnStatus, string> = {
  executing: "Turn executing",
  response_observed: "Response observed",
  completed: "Turn completed",
  failed: "Turn failed",
  interrupted: "Turn interrupted",
};

export function turnLabel(trace: TurnSource): string {
  const latest = turnSummary(trace).latest;
  return latest ? TURN_LABELS[latest] : "No turn evidence";
}

export function sessionLabel(trace: Pick<TurnSource, "lifecycle" | "execution_state">): string {
  switch (trace.lifecycle) {
    case "running": return trace.execution_state === "executing" ? "Executing" : trace.execution_state === "idle" ? "Idle / Open" : "Open";
    case "completed": return "Session finalized";
    case "cancelled": return "Cancelled";
    case "failed": return "Session failed";
    case "interrupted": return "Interrupted";
    default: return "Unknown";
  }
}

export const TASK_OUTCOME_LABELS: Record<TaskOutcome, string> = {
  unverified: "Unverified",
  reported_success: "Reported success",
  reported_failure: "Reported failure",
  verified_success: "Verified success",
  verified_failure: "Verified failure",
};

/** Runs with at least one completed turn, newest completion first, regardless of session state. */
export function recentCompletedTurns<T extends TurnSource>(traces: T[]): T[] {
  return traces
    .map((trace) => ({ trace, turns: turnSummary(trace) }))
    .filter(({ turns }) => turns.completed > 0)
    .sort((a, b) => Date.parse(b.turns.latestCompletedAt ?? "") - Date.parse(a.turns.latestCompletedAt ?? "") || 0)
    .map(({ trace }) => trace);
}

export function formatRate(rate: number | null): string {
  return rate === null ? "—" : `${(rate * 100).toFixed(rate === 1 || rate === 0 ? 0 : 1)}%`;
}
