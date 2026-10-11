"""Contract and validation tests independent of the configured ClickHouse backend."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from no_more_500s_api.main import create_app
from no_more_500s_api.services.trace_service import InMemoryTraceRepository


@pytest.fixture
def client() -> TestClient:
    with TestClient(create_app(InMemoryTraceRepository())) as test_client:
        yield test_client


def test_missing_and_malformed_trace_ids(client: TestClient) -> None:
    assert client.get(f"/v1/traces/{uuid4()}").status_code == 404
    assert client.get("/v1/traces/not-a-uuid").status_code == 422


def test_trace_list_summaries_are_newest_first_and_exclude_spans(client: TestClient) -> None:
    now = datetime.now(UTC)
    for name, offset in (("older", -60), ("newer", 0)):
        response = client.post(
            "/v1/traces",
            json={
                "project_id": "listing-contract",
                "name": name,
                "started_at": (now + timedelta(seconds=offset)).isoformat(),
                "spans": [
                    {"type": "llm", "name": "call", "duration_ms": 8,
                     "token_count": 4, "cost_usd": 0.02}
                ],
            },
        )
        assert response.status_code == 201, response.text

    response = client.get("/v1/traces")
    assert response.status_code == 200
    listing = response.json()
    assert [row["name"] for row in listing] == ["newer", "older"]
    assert all("spans" not in row and "attributes" not in row for row in listing)
    assert all(row["total_tokens"] == 4 and row["total_cost_usd"] == pytest.approx(0.02)
               for row in listing)


def test_empty_trace_has_zero_aggregates(client: TestClient) -> None:
    response = client.post("/v1/traces", json={"project_id": "empty", "name": "no spans"})
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["spans"] == []
    assert body["duration_ms"] == 0
    assert body["total_tokens"] == 0
    assert body["total_cost_usd"] == 0


@pytest.mark.parametrize(
    "payload",
    [
        {"project_id": "", "name": "run"},
        {"project_id": "p", "name": ""},
        {"project_id": "p", "name": "run", "spans": [
            {"type": "tool", "name": "bad", "duration_ms": -1}]},
        {"project_id": "p", "name": "run", "spans": [
            {"type": "tool", "name": "bad", "duration_ms": 1, "token_count": -1}]},
        {"project_id": "p", "name": "run", "spans": [
            {"type": "tool", "name": "bad", "duration_ms": 1, "cost_usd": -0.1}]},
        {"project_id": "p", "name": "run", "spans": [
            {"type": "unknown", "name": "bad", "duration_ms": 1}]},
        {"project_id": "p", "name": "run", "spans": [
            {"type": "tool", "name": "orphan", "duration_ms": 1,
             "parent_span_id": str(uuid4())}]},
    ],
)
def test_invalid_traces_are_rejected_without_persistence(
    client: TestClient, payload: dict[str, object]
) -> None:
    response = client.post("/v1/traces", json=payload)
    assert response.status_code == 422, response.text
    assert client.get("/v1/traces").json() == []


def test_valid_parent_child_spans_survive_retrieval(client: TestClient) -> None:
    parent, child = str(uuid4()), str(uuid4())
    response = client.post(
        "/v1/traces",
        json={
            "project_id": "relationships",
            "name": "nested",
            "spans": [
                {"span_id": parent, "type": "agent", "name": "parent", "duration_ms": 25},
                {"span_id": child, "parent_span_id": parent,
                 "type": "tool", "name": "child", "duration_ms": 10},
            ],
        },
    )
    assert response.status_code == 201, response.text
    fetched = client.get(f"/v1/traces/{response.json()['trace_id']}")
    assert fetched.status_code == 200
    assert [span["span_id"] for span in fetched.json()["spans"]] == [parent, child]
    assert fetched.json()["spans"][1]["parent_span_id"] == parent
