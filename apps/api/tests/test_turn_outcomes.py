"""Session lifecycle, agent turn status and task outcome stay independent."""

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from no_more_500s_api.domain.models import (
    ExecutionState,
    RunEventType,
    RunLifecycle,
    Span,
    SpanStatus,
    SpanType,
    StoredRunEvent,
    Trace,
    TraceCreate,
    TraceSummary,
)
from no_more_500s_api.services.run_reconstruction import reconstruct_trace
from no_more_500s_api.services.trace_service import TraceListQuery, apply_trace_list_query

T0 = datetime(2026, 1, 1, tzinfo=UTC)


class Log:
    def __init__(self, attributes: dict[str, Any]) -> None:
        self.trace_id = uuid4()
        self.events: list[StoredRunEvent] = []
        start = {"project_id": "p", "name": "run", "attributes": attributes}
        self._add(RunEventType.RUN_START, 0, start)

    def _add(self, kind: RunEventType, seconds: float, payload: dict[str, Any]) -> None:
        at = T0 + timedelta(seconds=seconds)
        self.events.append(
            StoredRunEvent(
                event_id=uuid4(),
                trace_id=self.trace_id,
                event_type=kind,
                sequence=len(self.events),
                occurred_at=at,
                received_at=at,
                payload=payload,
            )
        )

    def beat(self, seconds: float, progress: str, **attributes: Any) -> "Log":
        self._add(RunEventType.HEARTBEAT, seconds, {"progress": progress, "attributes": attributes})
        return self

    def tool(self, seconds: float, status: str = "ok", **attributes: Any) -> "Log":
        self._add(
            RunEventType.SPAN_COMPLETE,
            seconds,
            {
                "span_id": str(uuid4()),
                "type": "tool",
                "name": "Shell",
                "status": status,
                "duration_ms": 5,
                "attributes": attributes,
            },
        )
        return self

    def finalize(self, seconds: float, status: str, **attributes: Any) -> "Log":
        self._add(RunEventType.FINALIZE, seconds, {"status": status, "attributes": attributes})
        return self

    def trace(self, now_seconds: float = 30) -> Trace:
        return reconstruct_trace(self.events, now=T0 + timedelta(seconds=now_seconds))

    def summary(self, now_seconds: float = 30) -> TraceSummary:
        return TraceSummary.from_trace(self.trace(now_seconds))


def cursor() -> Log:
    return Log({"nm5.source": "cursor", "agent.framework": "cursor", "nm5.session_id": "c1"})


def codex() -> Log:
    return Log({"agent.framework": "codex", "nm5.source": "cli", "nm5.agent": "codex"})


def readme_turn(log: Log, generation: str = "g1", start: float = 1) -> Log:
    return (
        log.beat(start, "afterAgentThought", **{"nm5.generation_id": generation})
        .tool(start + 1, **{"nm5.generation_id": generation})
        .beat(start + 2, "afterAgentResponse", **{"nm5.generation_id": generation})
        .beat(
            start + 3, "stop", **{"nm5.generation_id": generation, "nm5.turn_status": "completed"}
        )
    )


def test_completed_turn_with_open_session() -> None:
    summary = readme_turn(cursor()).summary(now_seconds=600)
    assert summary.lifecycle is RunLifecycle.RUNNING
    assert summary.execution_state is ExecutionState.IDLE
    assert summary.latest_turn_status == "completed"
    assert summary.latest_turn_evidence == "cursor.stop"
    assert (summary.turns_total, summary.turns_completed) == (1, 1)
    assert summary.task_outcome == "unverified"
    assert summary.last_turn_completed is True
    assert summary.latest_turn_completed_at == T0 + timedelta(seconds=4)


def close(log: Log, seconds: float, reason: str = "user_close") -> Log:
    """Finalize exactly as the collector maps a Cursor sessionEnd."""
    return log.finalize(
        seconds, "cancelled", **{"nm5.end_reason": reason, "nm5.outcome": "cancelled"}
    )


def test_completed_turn_survives_chat_closure() -> None:
    log = close(readme_turn(cursor()), 10)
    trace = log.trace()
    summary = TraceSummary.from_trace(trace)
    assert summary.lifecycle is RunLifecycle.COMPLETED
    assert trace.status is SpanStatus.OK
    assert summary.ended_at == T0 + timedelta(seconds=10)
    assert summary.turns_completed == 1
    assert summary.turns_failed == summary.turns_interrupted == 0
    assert summary.latest_turn_status == "completed"
    assert summary.task_outcome == "unverified"
    assert summary.task_outcome_source is None
    assert trace.attributes["nm5.end_reason"] == "user_close"
    assert trace.attributes["nm5.reported_session_status"] == "cancelled"
    assert trace.attributes["nm5.lifecycle_evidence"] == "cursor.close_after_completed_turn"
    assert "error" not in trace.attributes


def test_window_close_after_completed_turn_is_completed() -> None:
    trace = close(readme_turn(cursor()), 10, "window_close").trace()
    assert trace.lifecycle is RunLifecycle.COMPLETED
    assert trace.attributes["nm5.end_reason"] == "window_close"


def test_multiple_completed_turns_then_close_is_completed() -> None:
    log = readme_turn(cursor(), "g1", 1)
    readme_turn(log, "g2", 10)
    readme_turn(log, "g3", 20)
    summary = close(log, 30).summary()
    assert summary.lifecycle is RunLifecycle.COMPLETED
    assert (summary.turns_total, summary.turns_completed) == (3, 3)


def test_earlier_aborted_turn_does_not_block_completion_of_latest() -> None:
    log = cursor().beat(1, "afterAgentThought", **{"nm5.generation_id": "g1"})
    log.beat(2, "stop", **{"nm5.generation_id": "g1", "nm5.turn_status": "aborted"})
    readme_turn(log, "g2", 5)
    summary = close(log, 20).summary()
    assert summary.lifecycle is RunLifecycle.COMPLETED
    assert (summary.turns_completed, summary.turns_interrupted) == (1, 1)


def test_close_during_execution_is_cancelled() -> None:
    log = cursor().beat(1, "afterAgentThought", **{"nm5.generation_id": "g1"})
    log.tool(2, **{"nm5.generation_id": "g1"})
    trace = close(log, 3).trace()
    assert trace.lifecycle is RunLifecycle.CANCELLED
    assert trace.status is SpanStatus.UNSET
    assert trace.attributes["nm5.latest_turn_status"] == "interrupted"
    assert trace.attributes["nm5.lifecycle_evidence"] == "cursor.close_before_turn_completed"


def test_completed_turn_then_unfinished_turn_then_close_is_not_completed() -> None:
    log = readme_turn(cursor()).beat(10, "afterAgentThought", **{"nm5.generation_id": "g2"})
    trace = close(log, 12).trace()
    assert trace.lifecycle is RunLifecycle.CANCELLED
    turns = {turn["turn_key"]: turn["status"] for turn in trace.attributes["nm5.turns"]}
    assert turns == {"id:g1": "completed", "id:g2": "interrupted"}


def test_completed_turn_then_provisional_response_then_close_is_not_completed() -> None:
    log = readme_turn(cursor()).beat(10, "afterAgentResponse", **{"nm5.generation_id": "g2"})
    summary = close(log, 12).summary()
    assert summary.lifecycle is RunLifecycle.CANCELLED
    assert summary.turns_completed == 1 and summary.turns_provisional == 1


def test_completed_turn_then_aborted_turn_then_close_is_cancelled() -> None:
    log = readme_turn(cursor()).beat(10, "afterAgentThought", **{"nm5.generation_id": "g2"})
    log.beat(11, "stop", **{"nm5.generation_id": "g2", "nm5.turn_status": "aborted"})
    summary = close(log, 12).summary()
    assert summary.lifecycle is RunLifecycle.CANCELLED
    assert summary.latest_turn_status == "interrupted"


def test_unattributed_work_after_stop_blocks_completion() -> None:
    summary = close(readme_turn(cursor()).tool(8), 10).summary()
    assert summary.lifecycle is RunLifecycle.CANCELLED


def test_failed_turn_then_close_is_failed() -> None:
    log = readme_turn(cursor()).beat(10, "afterAgentThought", **{"nm5.generation_id": "g2"})
    log.beat(11, "stop", **{"nm5.generation_id": "g2", "nm5.turn_status": "error"})
    trace = close(log, 12).trace()
    assert trace.lifecycle is RunLifecycle.FAILED
    assert trace.status is SpanStatus.ERROR
    assert trace.attributes["error"]["evidence"] == "cursor.stop"
    assert trace.attributes["nm5.lifecycle_evidence"] == "cursor.close_after_failed_turn"
    assert (trace.attributes["nm5.turns_completed"], trace.attributes["nm5.turns_failed"]) == (1, 1)


def test_close_without_turn_evidence_stays_cancelled() -> None:
    trace = close(cursor(), 5).trace()
    assert trace.lifecycle is RunLifecycle.CANCELLED
    assert trace.attributes["nm5.lifecycle_evidence"] == "cursor.close_without_turn_evidence"


def test_explicit_cursor_end_reasons_are_preserved() -> None:
    aborted = readme_turn(cursor()).finalize(10, "cancelled", **{"nm5.end_reason": "aborted"})
    errored = readme_turn(cursor()).finalize(10, "failed", **{"nm5.end_reason": "error"})
    done = cursor().finalize(10, "completed", **{"nm5.end_reason": "completed"})
    assert aborted.summary().lifecycle is RunLifecycle.CANCELLED
    assert errored.summary().lifecycle is RunLifecycle.FAILED
    assert done.summary().lifecycle is RunLifecycle.COMPLETED
    assert aborted.trace().attributes["nm5.lifecycle_evidence"] == "cursor.sessionEnd"


def test_late_stop_after_close_is_reconciled() -> None:
    log = cursor().beat(1, "afterAgentThought", **{"nm5.generation_id": "g1"})
    log.beat(2, "afterAgentResponse", **{"nm5.generation_id": "g1"})
    close(log, 3)
    assert log.summary().lifecycle is RunLifecycle.CANCELLED
    log.beat(3.5, "stop", **{"nm5.generation_id": "g1", "nm5.turn_status": "completed"})
    in_order = close(
        cursor()
        .beat(1, "afterAgentThought", **{"nm5.generation_id": "g1"})
        .beat(2, "afterAgentResponse", **{"nm5.generation_id": "g1"})
        .beat(3.5, "stop", **{"nm5.generation_id": "g1", "nm5.turn_status": "completed"}),
        3,
    )
    late, ordered = log.trace(), in_order.trace()
    assert late.lifecycle is ordered.lifecycle is RunLifecycle.COMPLETED
    assert late.ended_at == ordered.ended_at == T0 + timedelta(seconds=3)
    assert late.attributes["nm5.end_reason"] == ordered.attributes["nm5.end_reason"]
    assert late.attributes["nm5.turns"] == ordered.attributes["nm5.turns"]


def test_stop_long_after_close_is_ignored() -> None:
    log = close(cursor().beat(1, "afterAgentThought", **{"nm5.generation_id": "g1"}), 3)
    log.beat(600, "stop", **{"nm5.generation_id": "g1", "nm5.turn_status": "completed"})
    assert log.summary().lifecycle is RunLifecycle.CANCELLED


def test_duplicate_lifecycle_events_are_idempotent() -> None:
    log = close(readme_turn(cursor()), 10)
    log.events.extend(list(log.events))
    log.beat(11, "stop", **{"nm5.generation_id": "g1", "nm5.turn_status": "error"})
    log.finalize(12, "cancelled", **{"nm5.end_reason": "aborted"})
    first, second = log.trace(), log.trace()
    assert first.lifecycle is RunLifecycle.COMPLETED
    assert first.attributes["nm5.end_reason"] == "user_close"
    assert first.attributes["nm5.turns_completed"] == 1
    assert first.attributes["nm5.turns_failed"] == 0
    assert first.model_dump() == second.model_dump()


def test_reconstruction_is_independent_of_input_order() -> None:
    log = close(readme_turn(cursor()), 10)
    forward = reconstruct_trace(log.events, now=T0 + timedelta(seconds=30))
    backward = reconstruct_trace(list(reversed(log.events)), now=T0 + timedelta(seconds=30))
    assert forward.model_dump() == backward.model_dump()
    assert forward.lifecycle is RunLifecycle.COMPLETED


def test_closed_completed_session_counts_as_completed_not_verified() -> None:
    closed = close(readme_turn(cursor()), 10).summary()
    rows = apply_trace_list_query([closed], TraceListQuery(lifecycle=RunLifecycle.COMPLETED))
    assert [row.trace_id for row in rows] == [closed.trace_id]
    terminal = apply_trace_list_query([closed], TraceListQuery(activity="terminal"))
    assert [row.trace_id for row in terminal] == [closed.trace_id]
    assert closed.task_outcome == "unverified"


def test_codex_session_lifecycle_is_unchanged() -> None:
    log = codex().beat(1, "UserPromptSubmit", **{"nm5.turn_id": "t1"})
    log.beat(2, "Stop", **{"nm5.turn_id": "t1", "nm5.turn_status": "completed"})
    trace = log.finalize(3, "cancelled", **{"nm5.end_reason": "user_close"}).trace()
    assert trace.lifecycle is RunLifecycle.CANCELLED
    assert "nm5.lifecycle_evidence" not in trace.attributes


def test_sdk_session_lifecycle_is_unchanged() -> None:
    log = Log({"nm5.source": "sdk"}).tool(1)
    trace = log.finalize(2, "cancelled", **{"nm5.end_reason": "window_close"}).trace()
    assert trace.lifecycle is RunLifecycle.CANCELLED
    assert "nm5.lifecycle_evidence" not in trace.attributes


def test_cursor_thought_step_ids_belong_to_their_generation_turn() -> None:
    generation = "d40323ee-6b66-4e93-87d9-7ed00acd928c"
    log = (
        cursor()
        .beat(1, "afterAgentThought", **{"nm5.generation_id": f"{generation}-0-05ad"})
        .beat(1.1, "beforeSubmitPrompt", **{"nm5.generation_id": generation})
        .beat(2, "afterAgentThought", **{"nm5.generation_id": f"{generation}-2-q2w5"})
        .beat(3, "afterAgentThought", **{"nm5.generation_id": f"{generation}-14-7q9m"})
        .beat(4, "stop", **{"nm5.generation_id": generation, "nm5.turn_status": "completed"})
        .beat(5, "afterAgentThought", **{"nm5.generation_id": f"{generation}-16-zz01"})
    )
    summary = log.summary(now_seconds=600)
    assert (summary.turns_total, summary.turns_completed, summary.turns_executing) == (1, 1, 0)
    assert summary.latest_turn_status == "completed"


def test_after_agent_response_alone_is_provisional() -> None:
    log = cursor().beat(1, "afterAgentResponse", **{"nm5.generation_id": "g1"})
    summary = log.summary()
    assert summary.latest_turn_status == "response_observed"
    assert summary.turns_completed == 0 and summary.turns_provisional == 1
    closed = log.finalize(5, "cancelled").summary()
    assert closed.turns_completed == 0 and closed.turns_provisional == 1


def test_intermediate_response_then_more_work_is_still_executing() -> None:
    log = (
        cursor()
        .beat(1, "afterAgentResponse", **{"nm5.generation_id": "g1"})
        .tool(2, **{"nm5.generation_id": "g1"})
    )
    assert log.summary().latest_turn_status == "executing"
    log.beat(3, "stop", **{"nm5.generation_id": "g1", "nm5.turn_status": "completed"})
    assert log.summary().turns_completed == 1


def test_multiple_completed_turns_keep_stable_identities() -> None:
    log = readme_turn(cursor(), "g1", 1)
    readme_turn(log, "g2", 10)
    log.tool(20, **{"nm5.generation_id": "g1"})
    log.beat(21, "afterAgentThought", **{"nm5.generation_id": "g1"})
    trace = log.trace()
    summary = TraceSummary.from_trace(trace)
    assert summary.turns_total == 2 and summary.turns_completed == 2
    assert [turn["turn_key"] for turn in trace.attributes["nm5.turns"]] == ["id:g1", "id:g2"]
    assert trace.execution_state is ExecutionState.IDLE


def test_completed_turn_followed_by_failed_turn() -> None:
    log = readme_turn(cursor()).beat(
        20, "stop", **{"nm5.generation_id": "g2", "nm5.turn_status": "error"}
    )
    summary = log.summary()
    assert (summary.turns_completed, summary.turns_failed) == (1, 1)
    assert summary.latest_turn_status == "failed"
    assert summary.last_turn_completed is False
    assert summary.lifecycle is RunLifecycle.RUNNING


def test_duplicate_stop_cannot_rewrite_a_terminal_turn() -> None:
    log = readme_turn(cursor()).beat(
        9, "stop", **{"nm5.generation_id": "g1", "nm5.turn_status": "error"}
    )
    summary = log.summary()
    assert summary.turns_completed == 1 and summary.turns_failed == 0


def test_cursor_aborted_stop_is_interrupted_not_completed() -> None:
    log = cursor().beat(1, "afterAgentThought", **{"nm5.generation_id": "g1"}).beat(
        2, "stop", **{"nm5.generation_id": "g1", "nm5.turn_status": "aborted"}
    )
    summary = log.summary()
    assert summary.latest_turn_status == "interrupted"
    assert summary.turns_completed == 0
    assert summary.last_turn_completed is False


def test_session_cancelled_without_turn_evidence() -> None:
    summary = cursor().finalize(5, "cancelled").summary()
    assert summary.lifecycle is RunLifecycle.CANCELLED
    assert summary.turns_total == 0
    assert summary.latest_turn_status is None


def test_session_cancelled_mid_turn_interrupts_only_the_open_turn() -> None:
    log = readme_turn(cursor()).beat(10, "afterAgentThought", **{"nm5.generation_id": "g2"})
    log.finalize(12, "cancelled")
    trace = log.trace()
    turns = {turn["turn_key"]: turn for turn in trace.attributes["nm5.turns"]}
    assert turns["id:g1"]["status"] == "completed"
    assert turns["id:g2"]["status"] == "interrupted"
    assert turns["id:g2"]["provenance"] == "inferred"


def test_missing_identity_work_does_not_open_turns() -> None:
    log = readme_turn(cursor()).tool(9).tool(10, status="error")
    summary = log.summary()
    assert summary.turns_total == 1 and summary.turns_completed == 1


def test_stop_without_identity_closes_the_open_turn() -> None:
    log = cursor().beat(1, "afterAgentThought", **{"nm5.generation_id": "g1"}).beat(
        2, "stop", **{"nm5.turn_status": "completed"}
    )
    trace = log.trace()
    assert [turn["turn_key"] for turn in trace.attributes["nm5.turns"]] == ["id:g1"]
    assert trace.attributes["nm5.turns_completed"] == 1


def test_explicit_task_failure_despite_completed_turn() -> None:
    log = (
        Log({"nm5.source": "sdk"})
        .tool(1)
        .finalize(2, "completed", **{"nm5.task_outcome": "failure"})
    )
    summary = log.summary()
    assert summary.lifecycle is RunLifecycle.COMPLETED
    assert summary.turns_completed == 1
    assert summary.task_outcome == "reported_failure"
    assert summary.task_outcome_source == "agent_report"


def test_independent_verification_outranks_agent_report() -> None:
    log = Log({"nm5.source": "sdk"}).finalize(
        2,
        "completed",
        **{
            "nm5.task_outcome": "success",
            "nm5.task_verification": "failed",
            "nm5.task_verifier": "pytest",
        },
    )
    summary = log.summary()
    assert summary.task_outcome == "verified_failure"
    assert summary.task_outcome_source == "pytest"


def test_unknown_task_outcome_stays_unverified() -> None:
    for log in (readme_turn(cursor()), Log({}).finalize(1, "completed")):
        assert log.summary().task_outcome == "unverified"


def test_sdk_failed_and_cancelled_runs() -> None:
    failed = Log({}).finalize(1, "failed").summary()
    assert failed.turns_failed == 1 and failed.latest_turn_status == "failed"
    cancelled = Log({}).finalize(1, "cancelled").summary()
    assert cancelled.turns_total == 0


def test_codex_turns_use_turn_id() -> None:
    log = (
        codex()
        .beat(1, "UserPromptSubmit", **{"nm5.turn_id": "t1"})
        .tool(2, **{"nm5.turn_id": "t1"})
    )
    assert log.summary().latest_turn_status == "executing"
    log.beat(3, "Stop", **{"nm5.turn_id": "t1", "nm5.turn_status": "completed"})
    log.beat(4, "UserPromptSubmit", **{"nm5.turn_id": "t2"})
    log.beat(5, "Interrupt", **{"nm5.turn_id": "t2", "nm5.turn_status": "interrupted"})
    log.finalize(6, "completed")
    summary = log.summary()
    assert summary.lifecycle is RunLifecycle.COMPLETED
    assert (summary.turns_completed, summary.turns_interrupted) == (1, 1)
    assert summary.latest_turn_evidence == "codex.Interrupt"


def test_legacy_codex_stop_without_ids_counts_ordinal_turns() -> None:
    log = codex().beat(1, "Stop").beat(5, "Stop").beat(6, "Interrupt")
    trace = log.trace()
    assert trace.attributes["nm5.turns_completed"] == 2
    assert trace.attributes["nm5.turn_identity"] == "ordinal"
    assert trace.attributes["nm5.turns_interrupted"] == 0


def test_stale_executing_turn_becomes_interrupted_by_inactivity() -> None:
    log = Log({}).beat(1, "working")
    log._add(
        RunEventType.SPAN_START,
        2,
        {"span_id": str(uuid4()), "type": "tool", "name": "x", "attributes": {"nm5.turn_id": "a"}},
    )
    summary = log.summary(now_seconds=1000)
    assert summary.lifecycle is RunLifecycle.INTERRUPTED
    assert summary.turns_interrupted == 1 and summary.turns_executing == 0


def test_historical_batch_traces_map_to_one_turn() -> None:
    def batch(status: SpanStatus) -> TraceSummary:
        span = Span(type=SpanType.AGENT, name="a", duration_ms=5, started_at=T0)
        create = TraceCreate(project_id="p", name="b", started_at=T0, status=status, spans=[span])
        return TraceSummary.from_trace(Trace.from_create(create))

    ok, error, unset = batch(SpanStatus.OK), batch(SpanStatus.ERROR), batch(SpanStatus.UNSET)
    assert (ok.turns_completed, ok.latest_turn_evidence) == (1, "batch.status")
    assert error.turns_failed == 1
    assert unset.turns_total == 0
    assert ok.task_outcome == "unverified"


def test_historical_cursor_events_without_new_attributes_reconstruct() -> None:
    log = cursor().beat(1, "stop", **{"nm5.generation_id": "g1", "nm5.turn_status": "completed"})
    assert log.summary().turns_completed == 1


def test_completed_turns_activity_filter_includes_open_sessions() -> None:
    open_session = readme_turn(cursor()).summary()
    closed = readme_turn(cursor()).finalize(10, "cancelled").summary()
    nothing = cursor().finalize(5, "cancelled").summary()
    rows = apply_trace_list_query(
        [open_session, closed, nothing], TraceListQuery(activity="completed_turns")
    )
    assert {row.trace_id for row in rows} == {open_session.trace_id, closed.trace_id}
