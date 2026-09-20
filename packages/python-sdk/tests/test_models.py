from uuid import UUID

from nomore500s import SpanData, TraceData


def test_trace_serializes_ids_and_spans() -> None:
    trace = TraceData(
        project_id="demo",
        name="demo run",
        spans=[SpanData(type="tool", name="search", duration_ms=25)],
    )

    payload = trace.to_dict()
    assert UUID(payload["trace_id"]) == trace.trace_id
    assert payload["spans"][0]["name"] == "search"

