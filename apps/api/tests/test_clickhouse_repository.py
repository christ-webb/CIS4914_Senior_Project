"""Run against local ClickHouse with NM5_TEST_CLICKHOUSE=1."""

import os
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from no_more_500s_api.core.config import Settings
from no_more_500s_api.main import create_app
from no_more_500s_api.services.clickhouse_trace_repository import ClickHouseTraceRepository
from no_more_500s_api.services.trace_service import TraceNotFoundError

pytestmark = pytest.mark.skipif(
    os.getenv("NM5_TEST_CLICKHOUSE") != "1", reason="requires local ClickHouse"
)


def repository() -> ClickHouseTraceRepository:
    settings = Settings()
    return ClickHouseTraceRepository(
        host=settings.clickhouse_host,
        port=settings.clickhouse_port,
        database=settings.clickhouse_database,
        username=settings.clickhouse_user,
        password=settings.clickhouse_password,
    )


def test_persistence_across_app_instances() -> None:
    parent, child = str(uuid4()), str(uuid4())
    now = datetime.now(UTC).replace(microsecond=0)
    project = f"integration-{uuid4()}"
    first = create_app(repository())
    with TestClient(first) as client:
        for offset in (0, 1):
            response = client.post(
                "/v1/traces",
                json={
                    "project_id": project,
                    "name": f"run-{offset}",
                    "started_at": (now + timedelta(seconds=offset)).isoformat(),
                    "spans": [
                        {
                            "span_id": parent if offset == 0 else str(uuid4()),
                            "type": "agent",
                            "name": "parent",
                            "started_at": now.isoformat(),
                            "duration_ms": 50,
                            "token_count": 2,
                            "cost_usd": 0.01,
                        },
                        {
                            "span_id": child if offset == 0 else str(uuid4()),
                            "parent_span_id": parent if offset == 0 else None,
                            "type": "tool",
                            "name": "child",
                            "started_at": (now + timedelta(seconds=1)).isoformat(),
                            "duration_ms": 10,
                            "token_count": 3,
                            "cost_usd": 0.02,
                        },
                    ],
                },
            )
            assert response.status_code == 201, response.text
            if offset == 0:
                trace_id = response.json()["trace_id"]

    # A fresh app and fresh database connection simulate an API restart.
    with TestClient(create_app(repository())) as client:
        response = client.get(f"/v1/traces/{trace_id}")
        assert response.status_code == 200, response.text
        trace = response.json()
        assert [span["name"] for span in trace["spans"]] == ["parent", "child"]
        assert trace["spans"][1]["parent_span_id"] == parent
        assert trace["total_tokens"] == 5
        assert trace["total_cost_usd"] == pytest.approx(0.03)
        assert trace["duration_ms"] == 50
        listing = [row for row in client.get("/v1/traces").json() if row["project_id"] == project]
        assert [row["name"] for row in listing] == ["run-1", "run-0"]
        assert listing[1]["total_tokens"] == 5
        assert client.get(f"/v1/traces/{uuid4()}").status_code == 404
        with pytest.raises(TraceNotFoundError):
            repository().get(uuid4())
