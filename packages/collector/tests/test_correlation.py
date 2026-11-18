from nomore500s_collector.pipeline import Pipeline
from nomore500s_collector.store import CollectorStore

from .conftest import RecordingClient


def _pipe(store: CollectorStore) -> tuple[Pipeline, RecordingClient]:
    client = RecordingClient()
    return Pipeline(store, client), client


def test_hook_first_then_otel_same_tool_id(store: CollectorStore) -> None:
    pipe, client = _pipe(store)
    pipe.ingest(
        {"hook_event_name": "sessionStart", "sessionId": "conv", "cwd": "/tmp/a"},
        "h-start",
        source="vscode-copilot",
    )
    pre = pipe.ingest(
        {
            "hook_event_name": "preToolUse",
            "sessionId": "conv",
            "toolName": "readFile",
            "tool_use_id": "call-1",
        },
        "h-pre",
        source="vscode-copilot",
    )
    otel = {
        "hook_event_name": "OtelSpan",
        "operation": "execute_tool",
        "session_id": "conv",
        "conversation_id": "conv",
        "otel_span_id": "aabb",
        "occurred_at": "2026-01-01T00:00:00+00:00",
        "ended_at": "2026-01-01T00:00:01+00:00",
        "attributes": {"gen_ai.tool.call.id": "call-1", "gen_ai.tool.name": "readFile"},
        "_phase": "end",
    }
    pipe.ingest(otel, "o-end", source="otel")
    pipe.flush_once()
    events = [payload for path, payload in client.calls if str(path).endswith("/events")]
    span_ids = {
        item["span_id"]
        for batch in events
        for item in batch
        if item.get("event_type") in {"span_start", "span_complete"}
    }
    assert len(span_ids) == 1
    assert pre["status"] == "accepted"


def test_otel_first_then_hook_same_tool_id(store: CollectorStore) -> None:
    pipe, client = _pipe(store)
    otel_start = {
        "hook_event_name": "OtelSpan",
        "operation": "execute_tool",
        "session_id": "conv",
        "conversation_id": "conv",
        "otel_span_id": "aabb",
        "occurred_at": "2026-01-01T00:00:00+00:00",
        "attributes": {"gen_ai.tool.call.id": "call-1", "gen_ai.tool.name": "readFile"},
        "_phase": "start",
    }
    pipe.ingest(otel_start, "o-start", source="otel")
    pipe.ingest(
        {
            "hook_event_name": "postToolUse",
            "sessionId": "conv",
            "toolName": "readFile",
            "tool_use_id": "call-1",
        },
        "h-post",
        source="vscode-copilot",
    )
    pipe.flush_once()
    events = [payload for path, payload in client.calls if str(path).endswith("/events")]
    span_ids = {
        item["span_id"]
        for batch in events
        for item in batch
        if item.get("event_type") in {"span_start", "span_complete"}
    }
    assert len(span_ids) == 1


def test_missing_otel_keeps_hook_tools(store: CollectorStore) -> None:
    pipe, client = _pipe(store)
    pipe.ingest(
        {"hook_event_name": "sessionStart", "sessionId": "conv", "cwd": "/tmp/a"},
        "h-start",
        source="vscode-copilot",
    )
    pipe.ingest(
        {
            "hook_event_name": "preToolUse",
            "sessionId": "conv",
            "toolName": "readFile",
            "tool_use_id": "only-hook",
        },
        "h-pre",
        source="vscode-copilot",
    )
    pipe.flush_once()
    events = [payload for path, payload in client.calls if str(path).endswith("/events")]
    assert any(item.get("event_type") == "span_start" for batch in events for item in batch)


def test_duplicate_otel_batch_does_not_duplicate_spans(store: CollectorStore) -> None:
    pipe, _client = _pipe(store)
    span = {
        "operation": "execute_tool",
        "session_id": "conv",
        "conversation_id": "conv",
        "otel_span_id": "ff",
        "occurred_at": "2026-01-01T00:00:00+00:00",
        "ended_at": "2026-01-01T00:00:01+00:00",
        "attributes": {"gen_ai.tool.call.id": "call-9", "gen_ai.tool.name": "bash"},
    }
    first = pipe.ingest_otlp_spans([span], "batch-1")
    second = pipe.ingest_otlp_spans([span], "batch-1")
    assert first["spans"] >= 1
    assert all(item["status"] == "duplicate_delivery" for item in second["results"])


def test_unequal_session_ids_do_not_merge(store: CollectorStore) -> None:
    pipe, client = _pipe(store)
    pipe.ingest(
        {"hook_event_name": "sessionStart", "sessionId": "hook-session", "cwd": "/tmp/a"},
        "h-start",
        source="vscode-copilot",
    )
    pipe.ingest(
        {
            "hook_event_name": "OtelSpan",
            "operation": "invoke_agent",
            "session_id": "otel-conv",
            "conversation_id": "otel-conv",
            "otel_span_id": "11",
            "occurred_at": "2026-01-01T00:00:00+00:00",
            "_phase": "start",
            "attributes": {},
        },
        "o-start",
        source="otel",
    )
    pipe.flush_once()
    starts = [payload for path, payload in client.calls if path == "/v1/runs"]
    assert len(starts) == 2
    assert starts[0]["trace_id"] != starts[1]["trace_id"]
