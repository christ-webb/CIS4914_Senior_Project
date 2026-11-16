from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from no_more_500s_api.domain.models import TraceCreate
from no_more_500s_api.main import create_app
from no_more_500s_api.services.trace_service import (
    InMemoryTraceRepository,
    TraceListQuery,
    TraceStorageError,
)


class FailingRepository(InMemoryTraceRepository):
    def save(self, trace_create: TraceCreate):  # type: ignore[override]
        raise TraceStorageError("down")

    def list(self, query: TraceListQuery | None = None):  # type: ignore[override]
        raise TraceStorageError("down")

    def get(self, trace_id):  # type: ignore[no-untyped-def, override]
        raise TraceStorageError("down")

    def list_projects(self):  # type: ignore[override]
        raise TraceStorageError("down")


@pytest.fixture
def client() -> TestClient:
    with TestClient(create_app(InMemoryTraceRepository())) as test_client:
        yield test_client


def _run(
    name: str,
    project: str,
    started_at: datetime,
    status: str = "ok",
    trace_id: str | None = None,
    tokens: int = 3,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "project_id": project,
        "name": name,
        "status": status,
        "started_at": started_at.isoformat(),
        "spans": [
            {
                "type": "tool",
                "name": "step",
                "duration_ms": 10,
                "token_count": tokens,
                "cost_usd": 0.01,
            }
        ],
    }
    if trace_id is not None:
        payload["trace_id"] = trace_id
    return payload


def test_three_runs_are_listed_newest_first_and_fetchable(client: TestClient) -> None:
    now = datetime.now(UTC).replace(microsecond=0)
    created_ids: list[str] = []
    for index, offset in enumerate((-120, -60, 0)):
        response = client.post(
            "/v1/traces",
            json=_run(f"run-{index}", "history", now + timedelta(seconds=offset), tokens=index + 1),
        )
        assert response.status_code == 201, response.text
        created_ids.append(response.json()["trace_id"])

    listing = client.get("/v1/traces").json()
    assert [row["name"] for row in listing] == ["run-2", "run-1", "run-0"]
    for row in listing:
        detail = client.get(f"/v1/traces/{row['trace_id']}").json()
        assert detail["trace_id"] == row["trace_id"]
        assert detail["total_tokens"] == row["total_tokens"]
        assert detail["total_cost_usd"] == pytest.approx(row["total_cost_usd"])
        assert len(detail["spans"]) == 1
    assert created_ids[0] != created_ids[1] != created_ids[2]


def test_identical_timestamps_use_trace_id_tie_break(client: TestClient) -> None:
    stamp = datetime(2026, 1, 1, tzinfo=UTC)
    lower = "00000000-0000-0000-0000-000000000001"
    higher = "00000000-0000-0000-0000-000000000002"
    for trace_id in (lower, higher):
        response = client.post(
            "/v1/traces",
            json=_run(trace_id[-1], "tie", stamp, trace_id=trace_id),
        )
        assert response.status_code == 201, response.text
    assert [row["trace_id"] for row in client.get("/v1/traces").json()] == [higher, lower]


def test_duplicate_trace_id_is_rejected_without_inflating_metrics(client: TestClient) -> None:
    trace_id = str(uuid4())
    payload = _run("once", "dupes", datetime.now(UTC), trace_id=trace_id)
    first = client.post("/v1/traces", json=payload)
    second = client.post("/v1/traces", json=payload)
    assert first.status_code == 201
    assert second.status_code == 409
    assert second.json()["detail"] == "Trace already exists"
    listing = client.get("/v1/traces").json()
    assert len(listing) == 1
    detail = client.get(f"/v1/traces/{trace_id}").json()
    assert len(detail["spans"]) == 1
    assert detail["total_tokens"] == 3


def test_filters_apply_before_pagination(client: TestClient) -> None:
    now = datetime.now(UTC).replace(microsecond=0)
    for project, name, offset, status in (
        ("alpha", "a-old", -30, "ok"),
        ("alpha", "a-new", 0, "error"),
        ("beta", "b-new", -5, "ok"),
    ):
        response = client.post(
            "/v1/traces",
            json=_run(name, project, now + timedelta(seconds=offset), status=status),
        )
        assert response.status_code == 201, response.text

    alpha = client.get("/v1/traces", params={"project_id": "alpha", "limit": 1, "offset": 0})
    assert [row["name"] for row in alpha.json()] == ["a-new"]
    page_two = client.get("/v1/traces", params={"project_id": "alpha", "limit": 1, "offset": 1})
    assert [row["name"] for row in page_two.json()] == ["a-old"]
    errors = client.get("/v1/traces", params={"status": "error"})
    assert [row["name"] for row in errors.json()] == ["a-new"]
    ranged = client.get(
        "/v1/traces",
        params={
            "started_after": (now + timedelta(seconds=-10)).isoformat(),
            "started_before": (now + timedelta(seconds=10)).isoformat(),
        },
    )
    assert {row["name"] for row in ranged.json()} == {"a-new", "b-new"}
    assert client.get("/v1/projects").json() == ["alpha", "beta"]


def test_limit_plus_one_keeps_array_contract(client: TestClient) -> None:
    now = datetime.now(UTC)
    for index in range(3):
        client.post("/v1/traces", json=_run(f"p{index}", "page", now + timedelta(seconds=index)))
    response = client.get("/v1/traces", params={"limit": 3, "offset": 0})
    assert response.status_code == 200
    assert isinstance(response.json(), list)
    assert len(response.json()) == 3


def test_storage_failures_are_503_not_empty_lists() -> None:
    with TestClient(create_app(FailingRepository())) as client:
        created = client.post("/v1/traces", json=_run("x", "fail", datetime.now(UTC)))
        assert created.status_code == 503
        listing = client.get("/v1/traces")
        assert listing.status_code == 503
        assert listing.json()["detail"] == "Trace storage is unavailable"
        assert client.get("/v1/projects").status_code == 503
        assert client.get(f"/v1/traces/{uuid4()}").status_code == 503
