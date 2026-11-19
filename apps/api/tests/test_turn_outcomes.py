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


def test_completed_turn_survives_chat_closure() -> None:
    log = readme_turn(cursor()).finalize(10, "cancelled", **{"nm5.end_reason": "user_close"})
    summary = log.summary()
    assert summary.lifecycle is RunLifecycle.CANCELLED
    assert summary.turns_completed == 1
    assert summary.turns_failed == summary.turns_interrupted == 0
    assert summary.latest_turn_status == "completed"
    assert summary.task_outcome == "unverified"


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
