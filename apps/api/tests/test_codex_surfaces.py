"""Codex CLI / exec / IDE classification in reconstruction and summaries."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from no_more_500s_api.domain.models import (
    ExecutionState,
    RunEventType,
    RunLifecycle,
    SpanStatus,
    StoredRunEvent,
    TraceSummary,
)
from no_more_500s_api.services.run_reconstruction import (
    InvalidLifecycleError,
    gate_new_event,
    reconstruct_trace,
)

T0 = datetime(2026, 11, 19, 19, 0, tzinfo=UTC)


def _ev(kind: RunEventType, seq: int, offset: float, payload: dict, trace_id) -> StoredRunEvent:
    at = T0 + timedelta(seconds=offset)
    return StoredRunEvent(
        event_id=uuid4(),
        trace_id=trace_id,
        event_type=kind,
        sequence=seq,
        occurred_at=at,
        received_at=at,
        payload=payload,
    )


def _start(trace_id, agent_span, source: str) -> StoredRunEvent:
    return _ev(
        RunEventType.RUN_START,
        0,
        0,
        {
            "project_id": "fixture",
            "name": "codex:fixture",
            "started_at": T0.isoformat(),
            "agent_span_id": str(agent_span),
            "agent_name": "codex",
            "attributes": {
                "nm5.source": source,
                "nm5.agent": "codex",
                "agent.framework": "codex",
                "nm5.session_id": "thr-1",
            },
        },
        trace_id,
    )


def _tools(trace_id, agent_span) -> list[StoredRunEvent]:
    events = []
    for index in range(2):
        span = uuid4()
        events.append(
            _ev(
                RunEventType.SPAN_START,
                2 + index * 2,
                2 + index,
                {
                    "span_id": str(span),
                    "parent_span_id": str(agent_span),
                    "type": "tool",
                    "name": "Bash",
                    "attributes": {"nm5.turn_id": "t1"},
                },
                trace_id,
            )
        )
        events.append(
            _ev(
                RunEventType.SPAN_COMPLETE,
                3 + index * 2,
                2.5 + index,
                {
                    "span_id": str(span),
                    "parent_span_id": str(agent_span),
                    "type": "tool",
                    "name": "Bash",
                    "status": "ok",
                    "attributes": {"nm5.turn_id": "t1", "nm5.tool_outcome": "unreported"},
                },
                trace_id,
            )
        )
    return events


@pytest.mark.parametrize("source", ["codex-cli", "codex-ide", "cli", "codex"])
def test_interactive_completed_turn_is_idle_open_not_finalized(source: str) -> None:
    trace_id, agent_span = uuid4(), uuid4()
    events = [
        _start(trace_id, agent_span, source),
        _ev(
            RunEventType.HEARTBEAT,
            1,
            1,
            {"progress": "UserPromptSubmit", "attributes": {"nm5.turn_id": "t1"}},
            trace_id,
        ),
        *_tools(trace_id, agent_span),
        _ev(
            RunEventType.HEARTBEAT,
            6,
            5,
            {
                "progress": "Stop",
                "attributes": {"nm5.turn_id": "t1", "nm5.turn_status": "completed"},
            },
            trace_id,
        ),
    ]
    trace = reconstruct_trace(events, now=T0 + timedelta(hours=2))
    assert trace.lifecycle is RunLifecycle.RUNNING
    assert trace.execution_state is ExecutionState.IDLE
    assert trace.ended_at is None
    summary = TraceSummary.from_trace(trace)
    assert summary.turns_completed == 1


def test_exec_completed_session_with_tools_is_completed() -> None:
    trace_id, agent_span = uuid4(), uuid4()
    events = [
        _start(trace_id, agent_span, "codex-exec"),
        _ev(
            RunEventType.HEARTBEAT,
            1,
            1,
            {"progress": "UserPromptSubmit", "attributes": {"nm5.turn_id": "t1"}},
            trace_id,
        ),
        *_tools(trace_id, agent_span),
        _ev(
            RunEventType.HEARTBEAT,
            6,
            5,
            {
                "progress": "Stop",
                "attributes": {"nm5.turn_id": "t1", "nm5.turn_status": "completed"},
            },
            trace_id,
        ),
        _ev(
            RunEventType.FINALIZE,
            7,
            6,
            {"status": "completed", "attributes": {"nm5.terminal_evidence": "codex.SessionEnd"}},
            trace_id,
        ),
    ]
    trace = reconstruct_trace(events, now=T0 + timedelta(hours=2))
    assert trace.lifecycle is RunLifecycle.COMPLETED
    assert trace.status is SpanStatus.OK
    assert trace.ended_at is not None
    summary = TraceSummary.from_trace(trace)
    assert summary.source == "codex-exec"
    assert summary.agent_framework == "codex"
    assert summary.failure_count == 0
    assert summary.turns_completed == 1


def test_tool_error_span_does_not_fail_a_completed_session() -> None:
    trace_id, agent_span = uuid4(), uuid4()
    span = uuid4()
    events = [
        _start(trace_id, agent_span, "codex-exec"),
        _ev(
            RunEventType.SPAN_START,
            1,
            1,
            {
                "span_id": str(span),
                "parent_span_id": str(agent_span),
                "type": "tool",
                "name": "Bash",
            },
            trace_id,
        ),
        _ev(
            RunEventType.SPAN_COMPLETE,
            2,
            2,
            {
                "span_id": str(span),
                "parent_span_id": str(agent_span),
                "type": "tool",
                "name": "Bash",
                "status": "error",
            },
            trace_id,
        ),
        _ev(RunEventType.FINALIZE, 3, 3, {"status": "completed"}, trace_id),
    ]
    trace = reconstruct_trace(events, now=T0 + timedelta(minutes=5))
    assert trace.lifecycle is RunLifecycle.COMPLETED
    summary = TraceSummary.from_trace(trace)
    assert summary.failure_count == 1


def test_launcher_nonzero_exit_is_failed() -> None:
    trace_id, agent_span = uuid4(), uuid4()
    events = [
        _start(trace_id, agent_span, "codex-exec"),
        _ev(
            RunEventType.FINALIZE,
            1,
            3,
            {"status": "failed", "error": {"reason": "nonzero_exit", "exit_code": 1}},
            trace_id,
        ),
    ]
    trace = reconstruct_trace(events, now=T0 + timedelta(minutes=5))
    assert trace.lifecycle is RunLifecycle.FAILED


def test_late_heartbeat_after_finalize_is_rejected_not_reopened() -> None:
    trace_id, agent_span = uuid4(), uuid4()
    events = [
        _start(trace_id, agent_span, "codex-exec"),
        _ev(RunEventType.FINALIZE, 1, 3, {"status": "completed"}, trace_id),
    ]
    late = _ev(RunEventType.HEARTBEAT, 2, 4, {"progress": "Stop"}, trace_id)
    with pytest.raises(InvalidLifecycleError):
        gate_new_event(events, late)
    assert reconstruct_trace(events, now=T0 + timedelta(hours=1)).lifecycle is (
        RunLifecycle.COMPLETED
    )


def test_late_surface_refines_display_source_only_when_start_was_unknown() -> None:
    trace_id, agent_span = uuid4(), uuid4()
    beat = _ev(
        RunEventType.HEARTBEAT,
        1,
        1,
        {"progress": "UserPromptSubmit", "attributes": {"nm5.codex.last_surface": "codex-ide"}},
        trace_id,
    )
    unknown = reconstruct_trace([_start(trace_id, agent_span, "codex"), beat], now=T0)
    assert TraceSummary.from_trace(unknown).source == "codex-ide"
    known = reconstruct_trace([_start(trace_id, agent_span, "codex-cli"), beat], now=T0)
    assert TraceSummary.from_trace(known).source == "codex-cli"


def test_inactive_codex_ide_without_stop_is_interrupted_not_completed() -> None:
    trace_id, agent_span = uuid4(), uuid4()
    events = [
        _start(trace_id, agent_span, "codex-ide"),
        _ev(RunEventType.HEARTBEAT, 1, 1, {"progress": "UserPromptSubmit"}, trace_id),
    ]
    trace = reconstruct_trace(events, now=T0 + timedelta(minutes=10))
    assert trace.lifecycle is RunLifecycle.INTERRUPTED
