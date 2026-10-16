import pytest
from fastapi.testclient import TestClient
from no_more_500s_api.main import create_app
from no_more_500s_api.services.trace_service import InMemoryTraceRepository

DASHBOARD_ORIGIN = "http://localhost:3000"
UNTRUSTED_ORIGIN = "http://untrusted.example"


@pytest.fixture
def client() -> TestClient:
    with TestClient(create_app(InMemoryTraceRepository())) as test_client:
        yield test_client


def test_dashboard_origin_receives_cors_header(client: TestClient) -> None:
    response = client.get("/v1/traces", headers={"Origin": DASHBOARD_ORIGIN})

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == DASHBOARD_ORIGIN


def test_trace_preflight_allows_dashboard_origin(client: TestClient) -> None:
    response = client.options(
        "/v1/traces",
        headers={
            "Origin": DASHBOARD_ORIGIN,
            "Access-Control-Request-Method": "GET",
        },
    )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == DASHBOARD_ORIGIN
    assert "GET" in response.headers["access-control-allow-methods"]


def test_untrusted_origin_is_not_granted_cors_access(client: TestClient) -> None:
    response = client.options(
        "/v1/traces",
        headers={
            "Origin": UNTRUSTED_ORIGIN,
            "Access-Control-Request-Method": "GET",
        },
    )

    assert response.status_code == 400
    assert "access-control-allow-origin" not in response.headers