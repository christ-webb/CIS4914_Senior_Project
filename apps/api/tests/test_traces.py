from fastapi.testclient import TestClient
from no_more_500s_api.main import app


def test_create_then_fetch_trace() -> None:
    client = TestClient(app)
    payload = {
        "project_id": "test-project",
        "name": "healthy run",
        "status": "ok",
        "spans": [
            {
                "type": "tool",
                "name": "search",
                "duration_ms": 42,
                "status": "ok",
                "token_count": 12,
                "cost_usd": 0.001,
            }
        ],
    }

    created = client.post("/v1/traces", json=payload)
    assert created.status_code == 201
    trace = created.json()
    assert trace["total_tokens"] == 12
    assert trace["total_cost_usd"] == 0.001

    fetched = client.get(f"/v1/traces/{trace['trace_id']}")
    assert fetched.status_code == 200
    assert fetched.json()["name"] == "healthy run"

def test_create_trace_aggregates_multiple_spans() -> None:
    client = TestClient(app)
    payload = {
        "project_id": "test-project",
        "name": "multi-step run",
        "status": "ok",
        "spans": [
            {
                "type": "tool",
                "name": "plan",
                "duration_ms": 20,
                "status": "ok",
                "token_count": 10,
                "cost_usd": 0.001,
            },
            {
                "type": "tool",
                "name": "search",
                "duration_ms": 30,
                "status": "ok",
                "token_count": 15,
                "cost_usd": 0.002,
            },
        ],
    }

    created = client.post("/v1/traces", json=payload)

    assert created.status_code == 201
    trace = created.json()
    assert trace["total_tokens"] == 25
    assert trace["total_cost_usd"] == 0.003
    assert len(trace["spans"]) == 2
