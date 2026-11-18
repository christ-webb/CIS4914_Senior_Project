from datetime import UTC, datetime, timedelta
from uuid import uuid4

from no_more_500s_api.domain.models import (
    RunEventType,
    RunLifecycle,
    SpanStatus,
    SpanType,
    StoredRunEvent,
)
from no_more_500s_api.services.run_reconstruction import reconstruct_trace


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
