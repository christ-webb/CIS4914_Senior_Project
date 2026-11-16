from fastapi.testclient import TestClient
from no_more_500s_api.main import create_app
from no_more_500s_api.services.trace_service import InMemoryTraceRepository


def test_health() -> None:
    response = TestClient(create_app(InMemoryTraceRepository())).get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
