"""HTTP acceptance tests for the read-only repeated-tool-call failure endpoint."""

from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from no_more_500s_api.main import create_app
from no_more_500s_api.services.trace_service import InMemoryTraceRepository


@pytest.fixture
def client() -> TestClient:
    with TestClient(create_app(InMemoryTraceRepository())) as test_client:
        yield test_client


def _tool(name: str, query: str, tokens: int = 10, cost: float = 0.01) -> dict:
    return {
        "type": "tool",
        "name": name,
        "duration_ms": 5,
        "input": {"query": query},
        "token_count": tokens,
        "cost_usd": cost,
    }


def _create(client: TestClient, spans: list[dict]) -> dict:
    response = client.post(
        "/v1/traces",
        json={"project_id": "failure-api", "name": "example", "spans": spans},
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_finds_repeated_tool_calls_with_cost_and_span_ids(client: TestClient) -> None:
    trace = _create(
        client,
        [
            _tool("search", "same", 100, 0.5),
            _tool("search", "different", 20, 0.1),
            _tool("search", "same", 200, 0.75),
            _tool("search", "same", 300, 1.25),
        ],
    )
    response = client.get(f"/v1/traces/{trace['trace_id']}/failures")
    assert response.status_code == 200, response.text
    findings = response.json()
    assert len(findings) == 1
    finding = findings[0]
    assert finding["span_name"] == "search"
    assert finding["repeat_count"] == 3
    assert finding["span_ids"] == [
        trace["spans"][index]["span_id"] for index in (0, 2, 3)
    ]
    assert finding["wasted_tokens"] == 500
    assert finding["wasted_cost_usd"] == pytest.approx(2.0)
    assert set(finding) == {
        "span_name", "repeat_count", "span_ids", "wasted_tokens", "wasted_cost_usd"
    }


def test_no_finding_for_distinct_calls_or_empty_trace(client: TestClient) -> None:
    distinct = _create(
        client, [_tool("search", "one"), _tool("search", "two"), _tool("search", "one")]
    )
    empty = _create(client, [])
    for trace in (distinct, empty):
        response = client.get(f"/v1/traces/{trace['trace_id']}/failures")
        assert response.status_code == 200
        assert response.json() == []


def test_detector_ignores_repeated_non_tool_spans(client: TestClient) -> None:
    spans = [
        {"type": "llm", "name": "model", "duration_ms": 3, "input": {"prompt": "x"}}
        for _ in range(4)
    ]
    trace = _create(client, spans)
    assert client.get(f"/v1/traces/{trace['trace_id']}/failures").json() == []


def test_findings_do_not_modify_trace_or_listing(client: TestClient) -> None:
    trace = _create(client, [_tool("fetch", "x") for _ in range(4)])
    trace_url = f"/v1/traces/{trace['trace_id']}"
    before_detail = client.get(trace_url).json()
    before_list = client.get("/v1/traces").json()
    for _ in range(2):
        response = client.get(f"{trace_url}/failures")
        assert response.status_code == 200
        assert response.json()[0]["repeat_count"] == 4
    assert client.get(trace_url).json() == before_detail
    assert client.get("/v1/traces").json() == before_list


def test_unknown_and_invalid_trace_ids(client: TestClient) -> None:
    assert client.get(f"/v1/traces/{uuid4()}/failures").status_code == 404
    response = client.get("/v1/traces/not-a-uuid/failures")
    assert response.status_code == 422


def test_failure_endpoint_is_separate_from_trace_listing(client: TestClient) -> None:
    trace = _create(client, [_tool("search", "same") for _ in range(3)])
    listing = client.get("/v1/traces")
    assert listing.status_code == 200
    assert len(listing.json()) == 1
    assert listing.json()[0]["trace_id"] == trace["trace_id"]
    assert "span_ids" not in listing.json()[0]
