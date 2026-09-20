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

