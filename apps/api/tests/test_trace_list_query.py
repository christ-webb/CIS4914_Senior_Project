from datetime import UTC, datetime, timedelta, timezone
from uuid import uuid4
from warnings import catch_warnings, simplefilter

from fastapi.testclient import TestClient
from no_more_500s_api.domain.models import (
    RunEventType,
    RunLifecycle,
    SpanStatus,
    SpanType,
    StoredRunEvent,
    TraceSummary,
)
from no_more_500s_api.main import create_app
from no_more_500s_api.services.run_reconstruction import reconstruct_trace
from no_more_500s_api.services.trace_service import (
    InMemoryTraceRepository,
    TraceListQuery,
    apply_trace_list_query,
    clickhouse_bind_datetime,
    utc_datetime,
)


def _summary(name: str, started_at: datetime) -> TraceSummary:
    return TraceSummary(
        trace_id=uuid4(),
        project_id="p",
        name=name,
        started_at=started_at,
        duration_ms=1,
        total_tokens=0,
        total_cost_usd=0.0,
        status=SpanStatus.OK,
        lifecycle=RunLifecycle.COMPLETED,
    )


def test_naive_and_aware_started_before_compare_as_utc() -> None:
    naive = datetime(2026, 11, 17, 10, 0, 0)
    before = datetime(2026, 11, 17, 10, 33, 0, tzinfo=UTC)
    items = apply_trace_list_query(
        [_summary("kept", naive), _summary("later", datetime(2026, 11, 17, 11, 0, 0))],
        TraceListQuery(started_before=before),
    )
    assert [item.name for item in items] == ["kept"]


def test_inclusive_boundaries_and_offset_equivalents() -> None:
    instant = datetime(2026, 11, 17, 10, 33, 0, 123000, tzinfo=UTC)
    eastern = instant.astimezone(timezone(timedelta(hours=-5)))
    india = instant.astimezone(timezone(timedelta(hours=5, minutes=30)))
    zulu = datetime(2026, 11, 17, 10, 33, 0, 123000, tzinfo=UTC)
    items = [
        _summary("naive", datetime(2026, 11, 17, 10, 33, 0, 123000)),
        _summary("eastern", eastern),
        _summary("india", india),
    ]
    filtered = apply_trace_list_query(
        items,
        TraceListQuery(started_after=zulu, started_before=zulu),
    )
    assert {item.name for item in filtered} == {"naive", "eastern", "india"}


def test_date_filters_apply_before_pagination() -> None:
    t0 = datetime(2026, 11, 17, 10, 0, 0, tzinfo=UTC)
    items = [
        _summary("old", t0 - timedelta(hours=2)),
        _summary("mid", t0),
        _summary("new", t0 + timedelta(hours=1)),
    ]
    page = apply_trace_list_query(
        items,
        TraceListQuery(
            started_after=t0,
            started_before=t0 + timedelta(hours=1),
            limit=1,
            offset=0,
        ),
    )
    assert [item.name for item in page] == ["new"]
    page_two = apply_trace_list_query(
        items,
        TraceListQuery(
            started_after=t0,
            started_before=t0 + timedelta(hours=1),
            limit=1,
            offset=1,
        ),
    )
    assert [item.name for item in page_two] == ["mid"]


def test_utc_datetime_normalizes_naive_and_converts_aware() -> None:
    naive = utc_datetime(datetime(2026, 11, 17, 10, 33, 0, 123000))
    offset = utc_datetime(
        datetime(2026, 11, 17, 5, 33, 0, 123000, tzinfo=timezone(timedelta(hours=-5)))
    )
    assert naive == offset
    assert naive.tzinfo is UTC
    bound = clickhouse_bind_datetime(
        datetime(2026, 11, 17, 5, 33, 0, 123000, tzinfo=timezone(timedelta(hours=-5)))
    )
    assert bound == naive
    assert bound.tzinfo is UTC


def test_http_started_before_z_and_offsets_are_200() -> None:
    client = TestClient(create_app(InMemoryTraceRepository()))
    started = datetime(2026, 11, 17, 9, 0, 0, tzinfo=UTC)
    created = client.post(
        "/v1/traces",
        json={
            "project_id": "history",
            "name": "filtered",
            "started_at": started.isoformat(),
            "spans": [{"type": "tool", "name": "t", "duration_ms": 1, "status": "ok"}],
        },
    )
    assert created.status_code == 201, created.text
    exact = client.get(
        "/v1/traces",
        params={"started_before": "2026-11-17T10:33:00.000Z", "limit": 21},
    )
    assert exact.status_code == 200
    assert exact.json()[0]["name"] == "filtered"
    offset = client.get(
        "/v1/traces",
        params={"started_before": "2026-11-17T05:33:00.000-05:00", "limit": 21},
    )
    assert offset.status_code == 200
    assert offset.json()[0]["name"] == "filtered"
    plus = client.get(
        "/v1/traces",
        params={"started_before": "2026-11-17T16:03:00.000+05:30", "limit": 21},
    )
    assert plus.status_code == 200
    invalid = client.get("/v1/traces", params={"started_before": "not-a-date"})
    assert invalid.status_code == 422


def test_reconstructed_spans_serialize_without_enum_warnings() -> None:
    trace_id = uuid4()
    t0 = datetime(2026, 1, 1, tzinfo=UTC)
    events = [
        StoredRunEvent(
            event_id=uuid4(),
            trace_id=trace_id,
            event_type=RunEventType.RUN_START,
            sequence=0,
            occurred_at=t0,
            received_at=t0,
            payload={"project_id": "p", "name": "run", "started_at": t0.isoformat()},
        )
    ]
    for index, span_type in enumerate(("llm", "tool", "agent", "workflow"), start=1):
        span_id = uuid4()
        events.append(
            StoredRunEvent(
                event_id=uuid4(),
                trace_id=trace_id,
                event_type=RunEventType.SPAN_COMPLETE,
                sequence=index * 2,
                occurred_at=t0,
                received_at=t0,
                payload={"span_id": str(span_id), "status": "ok", "duration_ms": 1},
            )
        )
        events.append(
            StoredRunEvent(
                event_id=uuid4(),
                trace_id=trace_id,
                event_type=RunEventType.SPAN_START,
                sequence=index * 2 - 1,
                occurred_at=t0,
                received_at=t0,
                payload={"span_id": str(span_id), "type": span_type, "name": span_type},
            )
        )
    trace = reconstruct_trace(events, now=t0)
    assert {span.type for span in trace.spans} == {
        SpanType.LLM,
        SpanType.TOOL,
        SpanType.AGENT,
        SpanType.WORKFLOW,
    }
    with catch_warnings(record=True) as caught:
        simplefilter("always")
        payload = trace.model_dump(mode="json")
    assert payload["spans"]
    assert not [
        item
        for item in caught
        if "PydanticSerializationUnexpectedValue" in str(item.message)
        or "Expected `enum`" in str(item.message)
    ]
