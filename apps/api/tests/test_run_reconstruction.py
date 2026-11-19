from datetime import UTC, datetime, timedelta
from uuid import uuid4

from no_more_500s_api.domain.models import (
    ExecutionState,
    RunEventType,
    RunLifecycle,
    SpanStatus,
    SpanType,
    StoredRunEvent,
)
from no_more_500s_api.services.run_reconstruction import (
    InvalidLifecycleError,
    gate_new_event,
    reconstruct_trace,
)


def _event(
    event_type: RunEventType,
    sequence: int,
    occurred: datetime,
    payload: dict,
    *,
    event_id=None,
    received=None,
    trace_id=None,
) -> StoredRunEvent:
    return StoredRunEvent(
        event_id=event_id or uuid4(),
        trace_id=trace_id or uuid4(),
        event_type=event_type,
        sequence=sequence,
        occurred_at=occurred,
        received_at=received or occurred,
        payload=payload,
    )


def test_out_of_order_complete_then_start_does_not_downgrade_or_deflate_totals() -> None:
    trace_id = uuid4()
    span_id = uuid4()
    t0 = datetime(2026, 1, 1, tzinfo=UTC)
    start = _event(
        RunEventType.RUN_START,
        0,
        t0,
        {"project_id": "p", "name": "run", "started_at": t0.isoformat()},
        trace_id=trace_id,
    )
    complete = _event(
        RunEventType.SPAN_COMPLETE,
        2,
        t0 + timedelta(seconds=2),
        {
            "span_id": str(span_id),
            "status": "ok",
            "duration_ms": 20,
            "token_count": 8,
            "cost_usd": 0.2,
            "output": {"done": True},
        },
        trace_id=trace_id,
    )
    begin = _event(
        RunEventType.SPAN_START,
        1,
        t0 + timedelta(seconds=1),
        {
            "span_id": str(span_id),
            "type": "llm",
            "name": "model",
            "status": "unset",
            "token_count": 1,
            "cost_usd": 0.01,
        },
        trace_id=trace_id,
    )
    trace = reconstruct_trace([complete, begin, start], now=t0 + timedelta(seconds=3))
    assert len(trace.spans) == 1
    span = trace.spans[0]
    assert span.status is SpanStatus.OK
    assert span.token_count == 8
    assert span.cost_usd == 0.2
    assert span.output == {"done": True}
    assert span.name == "model"
    assert span.type is SpanType.LLM


def test_duplicate_event_id_does_not_inflate_tokens() -> None:
    trace_id = uuid4()
    span_id = uuid4()
    event_id = uuid4()
    t0 = datetime(2026, 1, 1, tzinfo=UTC)
    events = [
        _event(
            RunEventType.RUN_START,
            0,
            t0,
            {"project_id": "p", "name": "run", "started_at": t0.isoformat()},
            trace_id=trace_id,
        ),
        _event(
            RunEventType.SPAN_COMPLETE,
            1,
            t0,
            {
                "span_id": str(span_id),
                "type": "llm",
                "name": "m",
                "status": "ok",
                "token_count": 4,
                "cost_usd": 0.1,
            },
            event_id=event_id,
            trace_id=trace_id,
        ),
        _event(
            RunEventType.SPAN_COMPLETE,
            1,
            t0,
            {
                "span_id": str(span_id),
                "type": "llm",
                "name": "m",
                "status": "ok",
                "token_count": 4,
                "cost_usd": 0.1,
            },
            event_id=event_id,
            trace_id=trace_id,
        ),
    ]
    trace = reconstruct_trace(events, now=t0)
    assert trace.total_tokens == 4
    assert len(trace.spans) == 1


def test_inactivity_is_interrupted_not_failed() -> None:
    t0 = datetime(2026, 1, 1, tzinfo=UTC)
    events = [
        _event(
            RunEventType.RUN_START,
            0,
            t0,
            {"project_id": "p", "name": "run", "started_at": t0.isoformat()},
            received=t0,
        )
    ]
    trace = reconstruct_trace(events, now=t0 + timedelta(seconds=121), inactivity_seconds=120)
    assert trace.lifecycle is RunLifecycle.INTERRUPTED
    assert trace.status is SpanStatus.UNSET
    assert trace.ended_at is None


def test_finalize_then_late_complete_stays_terminal() -> None:
    trace_id = uuid4()
    span_id = uuid4()
    t0 = datetime(2026, 1, 1, tzinfo=UTC)
    events = [
        _event(
            RunEventType.RUN_START,
            0,
            t0,
            {
                "project_id": "p",
                "name": "run",
                "started_at": t0.isoformat(),
                "agent_span_id": str(span_id),
                "agent_name": "agent",
            },
            trace_id=trace_id,
        ),
        _event(
            RunEventType.FINALIZE,
            2,
            t0 + timedelta(seconds=5),
            {"status": "completed"},
            trace_id=trace_id,
        ),
        _event(
            RunEventType.SPAN_COMPLETE,
            1,
            t0 + timedelta(seconds=4),
            {"span_id": str(span_id), "status": "ok", "token_count": 3, "duration_ms": 40},
            trace_id=trace_id,
        ),
        _event(
            RunEventType.HEARTBEAT,
            3,
            t0 + timedelta(seconds=6),
            {"progress": "late"},
            trace_id=trace_id,
        ),
    ]
    trace = reconstruct_trace(events, now=t0 + timedelta(seconds=10))
    assert trace.lifecycle is RunLifecycle.COMPLETED
    assert trace.status is SpanStatus.OK
    assert trace.spans[0].token_count == 3
    assert trace.attributes.get("progress") is None


def test_cancelled_finalize_is_not_success() -> None:
    t0 = datetime(2026, 1, 1, tzinfo=UTC)
    events = [
        _event(
            RunEventType.RUN_START,
            0,
            t0,
            {"project_id": "p", "name": "run", "started_at": t0.isoformat()},
        ),
        _event(
            RunEventType.FINALIZE,
            1,
            t0,
            {
                "status": "cancelled",
                "attributes": {"nm5.outcome": "cancelled", "nm5.end_reason": "aborted"},
            },
        ),
    ]
    trace = reconstruct_trace(events, now=t0)
    assert trace.lifecycle is RunLifecycle.CANCELLED
    assert trace.status is SpanStatus.UNSET
    assert trace.attributes["nm5.outcome"] == "cancelled"


def _cursor_start(t0: datetime, trace_id=None) -> StoredRunEvent:
    return _event(
        RunEventType.RUN_START,
        0,
        t0,
        {
            "project_id": "p",
            "name": "cursor-session",
            "started_at": t0.isoformat(),
            "attributes": {
                "nm5.source": "cursor",
                "agent.framework": "cursor",
                "nm5.session_id": "conv-1",
                "nm5.conversation_id": "conv-1",
                "nm5.token_status": "unavailable",
                "nm5.cost_status": "unavailable",
            },
        },
        trace_id=trace_id,
    )


def test_cursor_stop_becomes_idle_not_interrupted() -> None:
    t0 = datetime(2026, 1, 1, tzinfo=UTC)
    events = [
        _cursor_start(t0),
        _event(
            RunEventType.HEARTBEAT,
            1,
            t0 + timedelta(seconds=5),
            {
                "progress": "stop",
                "attributes": {"nm5.turn_status": "completed", "nm5.generation_id": "g1"},
            },
        ),
    ]
    trace = reconstruct_trace(events, now=t0 + timedelta(seconds=121), inactivity_seconds=120)
    assert trace.lifecycle is RunLifecycle.RUNNING
    assert trace.execution_state is ExecutionState.IDLE
    assert trace.status is SpanStatus.UNSET
    assert trace.ended_at is None


def test_cursor_open_tool_span_inactivity_is_interrupted() -> None:
    t0 = datetime(2026, 1, 1, tzinfo=UTC)
    span_id = uuid4()
    events = [
        _cursor_start(t0),
        _event(
            RunEventType.SPAN_START,
            1,
            t0 + timedelta(seconds=1),
            {"span_id": str(span_id), "type": "tool", "name": "Shell", "status": "unset"},
        ),
    ]
    trace = reconstruct_trace(events, now=t0 + timedelta(seconds=122), inactivity_seconds=120)
    assert trace.lifecycle is RunLifecycle.INTERRUPTED
    assert trace.execution_state is ExecutionState.UNKNOWN
    assert trace.status is SpanStatus.UNSET


def test_model_only_turn_stays_executing_until_stop() -> None:
    t0 = datetime(2026, 1, 1, tzinfo=UTC)
    events = [
        _cursor_start(t0),
        _event(
            RunEventType.HEARTBEAT,
            1,
            t0 + timedelta(seconds=1),
            {"progress": "afterAgentThought", "attributes": {"nm5.generation_id": "g1"}},
        ),
    ]
    mid = reconstruct_trace(events, now=t0 + timedelta(seconds=10), inactivity_seconds=120)
    assert mid.execution_state is ExecutionState.EXECUTING
    assert mid.lifecycle is RunLifecycle.RUNNING
    events.append(
        _event(
            RunEventType.HEARTBEAT,
            2,
            t0 + timedelta(seconds=8),
            {
                "progress": "afterAgentResponse",
                "attributes": {"nm5.generation_id": "g1", "nm5.turn_status": "completed"},
            },
        )
    )
    done = reconstruct_trace(events, now=t0 + timedelta(seconds=130), inactivity_seconds=120)
    assert done.execution_state is ExecutionState.IDLE
    assert done.lifecycle is RunLifecycle.RUNNING
    events.append(
        _event(
            RunEventType.HEARTBEAT,
            3,
            t0 + timedelta(seconds=20),
            {"progress": "afterAgentThought", "attributes": {"nm5.generation_id": "g1"}},
        )
    )
    late = reconstruct_trace(events, now=t0 + timedelta(seconds=130), inactivity_seconds=120)
    assert late.execution_state is ExecutionState.IDLE
    events.append(
        _event(
            RunEventType.SPAN_COMPLETE,
            4,
            t0 + timedelta(seconds=21),
            {
                "span_id": str(uuid4()),
                "type": "tool",
                "name": "late tool result",
                "duration_ms": 10,
                "attributes": {"nm5.generation_id": "g1"},
            },
        )
    )
    assert (
        reconstruct_trace(events, now=t0 + timedelta(seconds=22)).execution_state
        is ExecutionState.IDLE
    )


def test_stop_usage_replaces_after_agent_response_not_max() -> None:
    t0 = datetime(2026, 1, 1, tzinfo=UTC)
    trace_id = uuid4()
    span_id = uuid4()
    events = [
        _cursor_start(t0, trace_id=trace_id),
        _event(
            RunEventType.SPAN_COMPLETE,
            1,
            t0 + timedelta(seconds=1),
            {
                "span_id": str(span_id),
                "type": "llm",
                "name": "cursor-turn",
                "status": "ok",
                "token_count": 40,
                "attributes": {
                    "nm5.usage_event": "afterAgentResponse",
                    "nm5.token_status": "reported",
                    "nm5.generation_id": "g1",
                    "nm5.usage_scope": "parent_agent",
                    "gen_ai.usage.input_tokens": 30,
                    "gen_ai.usage.output_tokens": 10,
                },
            },
            trace_id=trace_id,
        ),
        _event(
            RunEventType.SPAN_COMPLETE,
            2,
            t0 + timedelta(seconds=2),
            {
                "span_id": str(span_id),
                "type": "llm",
                "name": "cursor-turn",
                "status": "ok",
                "token_count": 12,
                "attributes": {
                    "nm5.usage_event": "stop",
                    "nm5.token_status": "reported",
                    "nm5.generation_id": "g1",
                    "nm5.usage_scope": "parent_agent",
                    "gen_ai.usage.input_tokens": 8,
                    "gen_ai.usage.output_tokens": 4,
                },
            },
            trace_id=trace_id,
        ),
    ]
    trace = reconstruct_trace(events, now=t0 + timedelta(seconds=3))
    span = next(item for item in trace.spans if item.span_id == span_id)
    assert span.token_count == 12
    assert span.attributes["nm5.usage_event"] == "stop"
    assert trace.total_tokens == 12
    assert trace.attributes["nm5.token_status"] == "reported"
    assert trace.attributes["nm5.cost_status"] == "unavailable"
    assert trace.attributes["nm5.usage_scope"] == "parent_agent"


def test_after_agent_response_does_not_override_stop_usage() -> None:
    t0 = datetime(2026, 1, 1, tzinfo=UTC)
    span_id = uuid4()
    events = [
        _cursor_start(t0),
        _event(
            RunEventType.SPAN_COMPLETE,
            1,
            t0 + timedelta(seconds=1),
            {
                "span_id": str(span_id),
                "type": "llm",
                "name": "cursor-turn",
                "status": "ok",
                "token_count": 12,
                "attributes": {
                    "nm5.usage_event": "stop",
                    "nm5.token_status": "reported",
                    "nm5.generation_id": "g1",
                },
            },
        ),
        _event(
            RunEventType.SPAN_COMPLETE,
            2,
            t0 + timedelta(seconds=2),
            {
                "span_id": str(span_id),
                "type": "llm",
                "name": "cursor-turn",
                "status": "ok",
                "token_count": 99,
                "attributes": {
                    "nm5.usage_event": "afterAgentResponse",
                    "nm5.token_status": "reported",
                    "nm5.generation_id": "g1",
                },
            },
        ),
    ]
    trace = reconstruct_trace(events, now=t0 + timedelta(seconds=3))
    span = next(item for item in trace.spans if item.span_id == span_id)
    assert span.token_count == 12
    assert span.attributes["nm5.usage_event"] == "stop"


def test_complementary_categories_fill_without_double_count() -> None:
    t0 = datetime(2026, 1, 1, tzinfo=UTC)
    span_id = uuid4()
    events = [
        _cursor_start(t0),
        _event(
            RunEventType.SPAN_COMPLETE,
            1,
            t0 + timedelta(seconds=1),
            {
                "span_id": str(span_id),
                "type": "llm",
                "name": "cursor-turn",
                "status": "ok",
                "token_count": 12,
                "attributes": {
                    "nm5.usage_event": "afterAgentResponse",
                    "nm5.token_status": "reported",
                    "nm5.generation_id": "g1",
                    "nm5.usage_scope": "parent_agent",
                    "nm5.token_accounting": "raw",
                    "nm5.billable_breakdown": "complete",
                    "gen_ai.usage.input_tokens": 10,
                    "gen_ai.usage.output_tokens": 2,
                },
            },
        ),
        _event(
            RunEventType.SPAN_COMPLETE,
            2,
            t0 + timedelta(seconds=2),
            {
                "span_id": str(span_id),
                "type": "llm",
                "name": "cursor-turn",
                "status": "ok",
                "token_count": 12,
                "attributes": {
                    "nm5.usage_event": "stop",
                    "nm5.token_status": "reported",
                    "nm5.generation_id": "g1",
                    "nm5.usage_scope": "parent_agent",
                    "nm5.token_accounting": "inclusive_cache",
                    "gen_ai.usage.cache_read_tokens": 3,
                    "gen_ai.usage.cache_write_tokens": 1,
                },
            },
        ),
    ]
    trace = reconstruct_trace(events, now=t0 + timedelta(seconds=3))
    span = next(item for item in trace.spans if item.span_id == span_id)
    assert span.attributes["gen_ai.usage.input_tokens"] == 10
    assert span.attributes["gen_ai.usage.cache_read_tokens"] == 3
    assert span.token_count == 12
    assert span.attributes["nm5.usage_event"] == "stop"
    assert trace.total_tokens == 12


def test_conflicting_usage_is_not_resolved_with_max() -> None:
    t0 = datetime(2026, 1, 1, tzinfo=UTC)
    span_id = uuid4()
    events = [
        _cursor_start(t0),
        _event(
            RunEventType.SPAN_COMPLETE,
            1,
            t0 + timedelta(seconds=1),
            {
                "span_id": str(span_id),
                "type": "llm",
                "name": "cursor-turn",
                "status": "ok",
                "token_count": 40,
                "attributes": {
                    "nm5.usage_event": "afterAgentResponse",
                    "nm5.token_status": "reported",
                    "nm5.generation_id": "g1",
                    "nm5.usage_scope": "parent_agent",
                    "gen_ai.usage.input_tokens": 30,
                    "gen_ai.usage.output_tokens": 10,
                },
            },
        ),
        _event(
            RunEventType.SPAN_COMPLETE,
            2,
            t0 + timedelta(seconds=2),
            {
                "span_id": str(span_id),
                "type": "llm",
                "name": "cursor-turn",
                "status": "ok",
                "token_count": 12,
                "attributes": {
                    "nm5.usage_event": "stop",
                    "nm5.token_status": "reported",
                    "nm5.generation_id": "g1",
                    "nm5.usage_scope": "parent_agent",
                    "gen_ai.usage.input_tokens": 8,
                    "gen_ai.usage.output_tokens": 4,
                },
            },
        ),
    ]
    trace = reconstruct_trace(events, now=t0 + timedelta(seconds=3))
    span = next(item for item in trace.spans if item.span_id == span_id)
    assert span.token_count == 12
    assert span.attributes["gen_ai.usage.input_tokens"] == 8
    assert span.attributes["nm5.usage_conflict"] is True
    assert span.attributes["nm5.token_accounting"] == "inconsistent"
    assert span.attributes.get("nm5.estimated_cost_status") == "unavailable"


def test_out_of_order_stop_then_response_is_deterministic() -> None:
    t0 = datetime(2026, 1, 1, tzinfo=UTC)
    span_id = uuid4()
    start = _cursor_start(t0)
    stop = _event(
        RunEventType.SPAN_COMPLETE,
        2,
        t0 + timedelta(seconds=2),
        {
            "span_id": str(span_id),
            "type": "llm",
            "name": "cursor-turn",
            "status": "ok",
            "token_count": 12,
            "attributes": {
                "nm5.usage_event": "stop",
                "nm5.token_status": "reported",
                "nm5.generation_id": "g1",
                "nm5.usage_scope": "parent_agent",
                "gen_ai.usage.input_tokens": 8,
                "gen_ai.usage.output_tokens": 4,
            },
        },
    )
    response = _event(
        RunEventType.SPAN_COMPLETE,
        1,
        t0 + timedelta(seconds=1),
        {
            "span_id": str(span_id),
            "type": "llm",
            "name": "cursor-turn",
            "status": "ok",
            "token_count": 12,
            "attributes": {
                "nm5.usage_event": "afterAgentResponse",
                "nm5.token_status": "reported",
                "nm5.generation_id": "g1",
                "nm5.usage_scope": "parent_agent",
                "gen_ai.usage.input_tokens": 8,
                "gen_ai.usage.output_tokens": 4,
                "gen_ai.usage.cache_read_tokens": 1,
            },
        },
    )
    forward = reconstruct_trace([start, response, stop], now=t0 + timedelta(seconds=3))
    reverse = reconstruct_trace([start, stop, response], now=t0 + timedelta(seconds=3))
    assert forward.total_tokens == reverse.total_tokens == 12
    span = next(item for item in reverse.spans if item.span_id == span_id)
    assert span.attributes["nm5.usage_event"] == "stop"
    assert span.attributes["gen_ai.usage.cache_read_tokens"] == 1


def test_duplicate_usage_event_id_does_not_inflate_tokens() -> None:
    t0 = datetime(2026, 1, 1, tzinfo=UTC)
    span_id = uuid4()
    event_id = uuid4()
    usage = {
        "span_id": str(span_id),
        "type": "llm",
        "name": "cursor-turn",
        "status": "ok",
        "token_count": 12,
        "attributes": {
            "nm5.usage_event": "stop",
            "nm5.token_status": "reported",
            "nm5.generation_id": "g1",
            "nm5.usage_scope": "parent_agent",
        },
    }
    events = [
        _cursor_start(t0),
        _event(RunEventType.SPAN_COMPLETE, 1, t0 + timedelta(seconds=1), usage, event_id=event_id),
        _event(RunEventType.SPAN_COMPLETE, 1, t0 + timedelta(seconds=1), usage, event_id=event_id),
    ]
    trace = reconstruct_trace(events, now=t0 + timedelta(seconds=3))
    assert trace.total_tokens == 12


def test_distinct_generations_sum_once() -> None:
    t0 = datetime(2026, 1, 1, tzinfo=UTC)
    first = uuid4()
    second = uuid4()
    events = [
        _cursor_start(t0),
        _event(
            RunEventType.SPAN_COMPLETE,
            1,
            t0 + timedelta(seconds=1),
            {
                "span_id": str(first),
                "type": "llm",
                "name": "turn-1",
                "status": "ok",
                "token_count": 5,
                "attributes": {
                    "nm5.usage_event": "stop",
                    "nm5.token_status": "reported",
                    "nm5.generation_id": "g1",
                    "nm5.usage_scope": "parent_agent",
                },
            },
        ),
        _event(
            RunEventType.SPAN_COMPLETE,
            2,
            t0 + timedelta(seconds=2),
            {
                "span_id": str(second),
                "type": "llm",
                "name": "turn-2",
                "status": "ok",
                "token_count": 7,
                "attributes": {
                    "nm5.usage_event": "stop",
                    "nm5.token_status": "reported",
                    "nm5.generation_id": "g2",
                    "nm5.usage_scope": "parent_agent",
                },
            },
        ),
    ]
    trace = reconstruct_trace(events, now=t0 + timedelta(seconds=3))
    assert trace.total_tokens == 12


def test_identity_verified_usage_after_finalize_is_accepted() -> None:
    t0 = datetime(2026, 1, 1, tzinfo=UTC)
    trace_id = uuid4()
    existing = [
        _cursor_start(t0, trace_id=trace_id),
        _event(
            RunEventType.FINALIZE,
            1,
            t0 + timedelta(seconds=2),
            {"status": "cancelled", "attributes": {"nm5.outcome": "cancelled"}},
            trace_id=trace_id,
        ),
    ]
    usage = _event(
        RunEventType.SPAN_COMPLETE,
        2,
        t0 + timedelta(seconds=3),
        {
            "span_id": str(uuid4()),
            "type": "llm",
            "name": "cursor-turn",
            "status": "ok",
            "token_count": 6,
            "attributes": {
                "nm5.usage_event": "stop",
                "nm5.token_status": "reported",
                "nm5.generation_id": "g1",
                "nm5.conversation_id": "conv-1",
                "nm5.usage_scope": "parent_agent",
            },
        },
        trace_id=trace_id,
    )
    gate_new_event(existing, usage)
    unrelated = _event(
        RunEventType.SPAN_COMPLETE,
        3,
        t0 + timedelta(seconds=4),
        {
            "span_id": str(uuid4()),
            "type": "tool",
            "name": "Shell",
            "status": "ok",
        },
        trace_id=trace_id,
    )
    try:
        gate_new_event(existing, unrelated)
        raise AssertionError("unrelated post-finalize span should be rejected")
    except InvalidLifecycleError:
        pass
    reconstructed = reconstruct_trace([*existing, usage], now=t0 + timedelta(seconds=5))
    assert reconstructed.lifecycle is RunLifecycle.CANCELLED
    assert reconstructed.attributes["nm5.outcome"] == "cancelled"
    assert reconstructed.total_tokens == 6
    assert reconstructed.execution_state is ExecutionState.IDLE


def test_codex_inactivity_without_stop_still_interrupted() -> None:
    t0 = datetime(2026, 1, 1, tzinfo=UTC)
    events = [
        _event(
            RunEventType.RUN_START,
            0,
            t0,
            {
                "project_id": "p",
                "name": "run",
                "started_at": t0.isoformat(),
                "attributes": {"nm5.source": "cli", "agent.framework": "codex"},
            },
        )
    ]
    trace = reconstruct_trace(events, now=t0 + timedelta(seconds=121), inactivity_seconds=120)
    assert trace.lifecycle is RunLifecycle.INTERRUPTED
    assert trace.execution_state is ExecutionState.UNKNOWN


def test_delayed_replay_is_not_fresh_execution() -> None:
    t0 = datetime(2026, 1, 1, tzinfo=UTC)
    events = [
        _cursor_start(t0),
        _event(
            RunEventType.SPAN_START,
            1,
            t0,
            {"span_id": str(uuid4()), "type": "tool", "name": "Shell"},
            received=t0 + timedelta(days=10),
        ),
    ]
    trace = reconstruct_trace(events, now=t0 + timedelta(days=10))
    assert trace.lifecycle is RunLifecycle.INTERRUPTED
    assert trace.execution_state is ExecutionState.UNKNOWN


def test_non_cursor_execution_requires_current_work_or_heartbeat() -> None:
    t0 = datetime(2026, 1, 1, tzinfo=UTC)
    start = _event(RunEventType.RUN_START, 0, t0, {"project_id": "p", "name": "sdk"})
    assert reconstruct_trace([start], now=t0).execution_state is ExecutionState.UNKNOWN
    heartbeat = _event(RunEventType.HEARTBEAT, 1, t0, {})
    assert reconstruct_trace([start, heartbeat], now=t0).execution_state is ExecutionState.EXECUTING
    stale = reconstruct_trace([start, heartbeat], now=t0 + timedelta(seconds=121))
    assert stale.lifecycle is RunLifecycle.INTERRUPTED
    assert stale.execution_state is ExecutionState.UNKNOWN


def test_codex_completed_turn_stays_idle_without_finalizing() -> None:
    t0 = datetime(2026, 1, 1, tzinfo=UTC)
    start = _event(
        RunEventType.RUN_START,
        0,
        t0,
        {"project_id": "p", "name": "codex", "attributes": {"nm5.source": "cli"}},
    )
    stop = _event(RunEventType.HEARTBEAT, 1, t0, {"progress": "Stop"})
    trace = reconstruct_trace([start, stop], now=t0 + timedelta(days=1))
    assert trace.lifecycle is RunLifecycle.RUNNING
    assert trace.execution_state is ExecutionState.IDLE
    assert trace.ended_at is None
    assert trace.attributes["nm5.last_turn_completed"] is True
