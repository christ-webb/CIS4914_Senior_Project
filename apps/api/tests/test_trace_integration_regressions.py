"""Isolated end-to-end HTTP regressions for trace ingestion and retrieval.

These tests exercise FastAPI request validation, repository persistence, and
response serialization without needing ClickHouse or external services.
"""

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


def test_listing_summaries_match_details_and_are_newest_first(client: TestClient) -> None:
    now = datetime.now(UTC).replace(microsecond=0)
    created = []
    for index, offset in enumerate((-120, -60, 0)):
        response = client.post(
            "/v1/traces",
            json={
                "project_id": "listing-regression",
                "name": f"run-{index}",
                "started_at": (now + timedelta(seconds=offset)).isoformat(),
                "status": "error" if index == 1 else "ok",
                "attributes": {"private_to_detail": True},
                "spans": [
                    {
                        "type": "tool",
                        "name": f"step-{index}",
                        "duration_ms": 10 + index,
                        "token_count": index + 1,
                        "cost_usd": (index + 1) * 0.01,
                    }
                ],
            },
        )
        assert response.status_code == 201, response.text
        created.append(response.json())

    listing_response = client.get("/v1/traces")
    assert listing_response.status_code == 200
    listing = listing_response.json()
    assert [row["trace_id"] for row in listing] == [
        created[2]["trace_id"], created[1]["trace_id"], created[0]["trace_id"]
    ]
    for summary in listing:
        detail_response = client.get(f"/v1/traces/{summary['trace_id']}")
        assert detail_response.status_code == 200
        detail = detail_response.json()
        assert set(summary).isdisjoint({"spans", "attributes", "input", "output"})
        for key in (
            "trace_id", "project_id", "name", "started_at", "status",
            "duration_ms", "total_tokens", "total_cost_usd",
        ):
            assert summary[key] == detail[key]


def test_rejected_requests_do_not_mutate_existing_traces(client: TestClient) -> None:
    original = client.post(
        "/v1/traces",
        json={"project_id": "existing", "name": "preserve", "spans": []},
    )
    assert original.status_code == 201, original.text
    existing_id = original.json()["trace_id"]
    before = client.get(f"/v1/traces/{existing_id}").json()

    invalid_payloads = [
        {},  # required project and name
        {"project_id": "p", "name": "bad", "spans": "not-a-list"},
        {"project_id": "p", "name": "bad", "spans": [{}]},
        {"project_id": "p", "name": "bad", "spans": [
            {"type": "tool", "name": "step", "duration_ms": "invalid"}
        ]},
        {"project_id": "p", "name": "bad", "spans": [
            {"type": "tool", "name": "step", "duration_ms": 1,
             "parent_span_id": str(uuid4())}
        ]},
    ]
    for payload in invalid_payloads:
        response = client.post("/v1/traces", json=payload)
        assert response.status_code == 422, response.text
        assert len(client.get("/v1/traces").json()) == 1
        assert client.get(f"/v1/traces/{existing_id}").json() == before


def test_empty_trace_round_trip_and_missing_id_does_not_change_list(
    client: TestClient,
) -> None:
    response = client.post(
        "/v1/traces",
        json={"project_id": "empty-round-trip", "name": "no operations"},
    )
    assert response.status_code == 201, response.text
    created = response.json()
    assert created["spans"] == []
    assert created["duration_ms"] == 0
    assert created["total_tokens"] == 0
    assert created["total_cost_usd"] == 0

    assert client.get(f"/v1/traces/{created['trace_id']}").json() == created
    before = client.get("/v1/traces").json()
    assert client.get(f"/v1/traces/{uuid4()}").status_code == 404
    assert client.get("/v1/traces/not-a-uuid").status_code == 422
    assert client.get("/v1/traces").json() == before


def test_parent_child_relationships_and_nested_payloads_survive_round_trip(
    client: TestClient,
) -> None:
    parent_id, child_id, grandchild_id = (str(uuid4()) for _ in range(3))
    # Child-first order is valid because parents are checked against all span IDs.
    spans = [
        {
            "span_id": grandchild_id,
            "parent_span_id": child_id,
            "type": "llm",
            "name": "completion",
            "duration_ms": 5,
            "token_count": 13,
            "cost_usd": 0.04,
            "input": {"messages": [{"role": "user", "content": "question"}]},
            "output": {"answer": {"text": "response"}},
            "attributes": {"model": "test", "attempt": 2},
        },
        {
            "span_id": child_id,
            "parent_span_id": parent_id,
            "type": "tool",
            "name": "lookup",
            "duration_ms": 15,
            "token_count": 2,
            "cost_usd": 0.01,
        },
        {
            "span_id": parent_id,
            "type": "agent",
            "name": "root",
            "duration_ms": 25,
            "token_count": 0,
        },
    ]
    response = client.post(
        "/v1/traces",
        json={
            "project_id": "relationships",
            "name": "multi-level",
            "attributes": {"source": {"environment": "test"}},
            "spans": spans,
        },
    )
    assert response.status_code == 201, response.text
    body = response.json()
    fetched = client.get(f"/v1/traces/{body['trace_id']}")
    assert fetched.status_code == 200
    assert fetched.json() == body
    assert body["attributes"] == {"source": {"environment": "test"}}
    assert [(span["span_id"], span["parent_span_id"]) for span in body["spans"]] == [
        (grandchild_id, child_id), (child_id, parent_id), (parent_id, None)
    ]
    assert body["spans"][0]["input"]["messages"][0]["content"] == "question"
    assert body["spans"][0]["output"] == {"answer": {"text": "response"}}
    assert body["total_tokens"] == 15
    assert body["total_cost_usd"] == pytest.approx(0.05)
    assert body["duration_ms"] == 25
