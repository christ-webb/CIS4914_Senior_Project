"""SDK serialization and transport tests without a running API."""

from datetime import UTC, datetime
from uuid import uuid4

import httpx
import pytest
from nomore500s import SpanData, TraceData
from nomore500s.client import NoMore500sClient


def test_nested_trace_serialization_keeps_ids_times_and_fields() -> None:
    parent_id = uuid4()
    timestamp = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)
    trace = TraceData(
        project_id="sdk-project",
        name="nested",
        started_at=timestamp,
        attributes={"labels": ["sdk", "test"]},
        spans=[
            SpanData(type="agent", name="parent", duration_ms=10,
                     span_id=parent_id, started_at=timestamp),
            SpanData(type="tool", name="child", duration_ms=5,
                     parent_span_id=parent_id, started_at=timestamp,
                     input={"query": "example"}, output={"ok": True},
                     attributes={"attempt": 1}, token_count=7, cost_usd=0.025),
        ],
    )
    payload = trace.to_dict()
    assert payload["started_at"] == timestamp.isoformat()
    assert payload["attributes"] == {"labels": ["sdk", "test"]}
    assert payload["spans"][1]["parent_span_id"] == str(parent_id)
    assert payload["spans"][1]["input"] == {"query": "example"}
    assert payload["spans"][1]["output"] == {"ok": True}
    assert payload["spans"][1]["attributes"] == {"attempt": 1}
    assert payload["spans"][1]["token_count"] == 7
    assert payload["spans"][1]["cost_usd"] == 0.025


def test_send_trace_posts_serialized_payload_and_returns_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(201, json={"trace_id": "persisted"})

    original_client = httpx.Client

    def client_factory(*args: object, **kwargs: object) -> httpx.Client:
        return original_client(transport=httpx.MockTransport(handler), trust_env=False)

    monkeypatch.setattr(httpx, "Client", client_factory)
    client = NoMore500sClient(api_url="http://localhost:8000/")
    result = client.send_trace(TraceData(project_id="project", name="run"))
    assert result == {"trace_id": "persisted"}
    assert len(requests) == 1
    assert requests[0].method == "POST"
    assert str(requests[0].url) == "http://localhost:8000/v1/traces"
    assert b'"project_id":"project"' in requests[0].content


def test_send_trace_raises_on_api_error(monkeypatch: pytest.MonkeyPatch) -> None:
    original_client = httpx.Client

    def client_factory(*args: object, **kwargs: object) -> httpx.Client:
        return original_client(
            transport=httpx.MockTransport(lambda request: httpx.Response(422, json={"detail": []})),
            trust_env=False,
        )

    monkeypatch.setattr(httpx, "Client", client_factory)
    with pytest.raises(httpx.HTTPStatusError) as exc:
        NoMore500sClient().send_trace(TraceData(project_id="project", name="run"))
    assert exc.value.response.status_code == 422
