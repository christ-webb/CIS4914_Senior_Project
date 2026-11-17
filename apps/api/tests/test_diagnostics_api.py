from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from no_more_500s_api.main import create_app
from no_more_500s_api.services.trace_service import InMemoryTraceRepository, TraceStorageError


@pytest.fixture
def client() -> TestClient:
    with TestClient(create_app(InMemoryTraceRepository())) as test_client:
        yield test_client


def _error_tool(path: str = "missing.txt") -> dict:
    return {
        "type": "tool",
        "name": "read_file",
        "duration_ms": 8,
        "status": "error",
        "input": {"path": path},
        "output": {"error": "No such file or directory"},
    }


def test_diagnostics_for_failure_demo_sequence(client: TestClient) -> None:
    ok = {
        "type": "tool",
        "name": "search",
        "duration_ms": 5,
        "status": "ok",
        "input": {"query": "ok"},
        "output": {"hits": 1},
    }
    response = client.post(
        "/v1/traces",
        json={
            "project_id": "diagnostic-demo",
            "name": "failure-demo",
            "spans": [ok] + [_error_tool() for _ in range(4)],
        },
    )
    assert response.status_code == 201, response.text
    trace = response.json()
    diagnostics = client.get(f"/v1/traces/{trace['trace_id']}/diagnostics")
    assert diagnostics.status_code == 200, diagnostics.text
    body = diagnostics.json()
    types = {finding["detection_type"] for finding in body["findings"]}
    assert "tool_failure" in types
    failure = next(item for item in body["findings"] if item["detection_type"] == "tool_failure")
    failed_ids = {span["span_id"] for span in trace["spans"] if span["status"] == "error"}
    assert set(failure["span_ids"]) == failed_ids
    assert failure["confidence"] == "confirmed"
    assert failure["evidence"]


def test_successful_run_has_no_false_failure_findings(client: TestClient) -> None:
    response = client.post(
        "/v1/traces",
        json={
            "project_id": "diagnostic-demo",
            "name": "success",
            "spans": [
                {
                    "type": "llm",
                    "name": "plan",
                    "duration_ms": 10,
                    "status": "ok",
                    "token_count": 12,
                    "cost_usd": 0.001,
                },
                {
                    "type": "tool",
                    "name": "search",
                    "duration_ms": 8,
                    "status": "ok",
                    "input": {"query": "agent observability"},
                    "output": {"results": ["ok"]},
                },
            ],
        },
    )
    body = client.get(f"/v1/traces/{response.json()['trace_id']}/diagnostics").json()
    failure_types = {
        finding["detection_type"]
        for finding in body["findings"]
        if finding["detection_type"]
        in {"tool_failure", "schema_mismatch", "ineffective_recovery", "provider_error"}
    }
    assert failure_types == set()


def test_diagnostics_filters_and_404(client: TestClient) -> None:
    created = client.post(
        "/v1/traces",
        json={
            "project_id": "filters",
            "name": "run",
            "spans": [_error_tool() for _ in range(3)],
        },
    ).json()
    trace_id = created["trace_id"]
    filtered = client.get(
        f"/v1/traces/{trace_id}/diagnostics", params={"detection_type": "tool_failure"}
    )
    assert filtered.status_code == 200
    assert {item["detection_type"] for item in filtered.json()["findings"]} == {"tool_failure"}
    span_id = created["spans"][0]["span_id"]
    by_span = client.get(f"/v1/traces/{trace_id}/diagnostics", params={"span_id": span_id})
    assert by_span.status_code == 200
    for finding in by_span.json()["findings"]:
        assert span_id in finding["span_ids"]
    missing = client.get(f"/v1/traces/{uuid4()}/diagnostics")
    assert missing.status_code == 404
    invalid = client.get("/v1/traces/not-a-uuid/diagnostics")
    assert invalid.status_code == 422


def test_summary_is_bounded_and_explicit(client: TestClient) -> None:
    for index in range(3):
        client.post(
            "/v1/traces",
            json={
                "project_id": "window",
                "name": f"run-{index}",
                "spans": [_error_tool(f"{index}.txt")],
            },
        )
    response = client.get("/v1/diagnostics", params={"limit": 2, "project_id": "window"})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["analysis_scope"]["analyzed_run_count"] == 2
    assert body["analysis_scope"]["limit"] == 2
    assert "not all historical data" in body["analysis_scope"]["note"]
    assert body["totals_by_severity"]["critical"] >= 1


def test_failures_endpoint_remains_loop_finding_shape(client: TestClient) -> None:
    created = client.post(
        "/v1/traces",
        json={
            "project_id": "compat",
            "name": "loops",
            "spans": [
                {
                    "type": "tool",
                    "name": "search",
                    "duration_ms": 5,
                    "input": {"query": "same"},
                    "token_count": 10,
                    "cost_usd": 0.02,
                }
                for _ in range(3)
            ],
        },
    ).json()
    response = client.get(f"/v1/traces/{created['trace_id']}/failures")
    assert response.status_code == 200
    finding = response.json()[0]
    assert set(finding) == {
        "span_name",
        "repeat_count",
        "span_ids",
        "wasted_tokens",
        "wasted_cost_usd",
    }


def test_storage_error_on_diagnostics_is_503() -> None:
    class Broken(InMemoryTraceRepository):
        def get(self, trace_id):  # type: ignore[no-untyped-def]
            raise TraceStorageError("down")

        def list(self, query=None):  # type: ignore[no-untyped-def]
            raise TraceStorageError("down")

    with TestClient(create_app(Broken())) as client:
        response = client.get("/v1/diagnostics")
        assert response.status_code == 503
        missing = client.get(f"/v1/traces/{uuid4()}/diagnostics")
        assert missing.status_code == 503
