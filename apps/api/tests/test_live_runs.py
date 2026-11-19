from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from no_more_500s_api.main import create_app
from no_more_500s_api.services.trace_service import InMemoryTraceRepository, TraceStorageError


@pytest.fixture
def repository() -> InMemoryTraceRepository:
    return InMemoryTraceRepository()


@pytest.fixture
def client(repository: InMemoryTraceRepository) -> TestClient:
    with TestClient(create_app(repository)) as test_client:
        yield test_client


def _start(client: TestClient, **overrides: object) -> dict:
    body = {
        "project_id": "live",
        "name": "agent-run",
        "agent_name": "demo-agent",
        **overrides,
    }
    response = client.post("/v1/runs", json=body)
    assert response.status_code == 201, response.text
    return response.json()


def test_external_run_summary_copies_source_metadata(client: TestClient) -> None:
    created = _start(
        client,
        attributes={
            "nm5.source": "cli",
            "agent.framework": "codex",
            "gen_ai.provider": "openai",
            "nm5.workspace": "demo",
            "nm5.cost_status": "unavailable",
            "nm5.token_status": "unavailable",
            "nm5.coverage": "lifecycle+tools",
        },
    )
    listing = client.get("/v1/traces")
    row = next(item for item in listing.json() if item["trace_id"] == created["trace_id"])
    assert row["source"] == "cli"
    assert row["agent_framework"] == "codex"
    assert row["provider"] == "openai"
    assert row["workspace"] == "demo"
    assert row["cost_status"] == "unavailable"
    assert row["token_status"] == "unavailable"
    assert row["instrumentation_coverage"] == "lifecycle+tools"


def test_start_run_is_immediately_retrievable(client: TestClient) -> None:
    created = _start(client)
    assert created["lifecycle"] == "running"
    assert created["status"] == "unset"
    assert len(created["spans"]) == 1
    fetched = client.get(f"/v1/traces/{created['trace_id']}")
    assert fetched.status_code == 200
    assert fetched.json()["lifecycle"] == "running"
    listing = client.get("/v1/traces", params={"lifecycle": "running"})
    assert created["trace_id"] in {row["trace_id"] for row in listing.json()}


def test_incremental_model_and_tool_tree(client: TestClient) -> None:
    created = _start(client)
    trace_id = created["trace_id"]
    agent_id = created["spans"][0]["span_id"]
    llm_id = str(uuid4())
    tool_id = str(uuid4())
    t0 = datetime.now(UTC)
    events = [
        {
            "event_id": str(uuid4()),
            "event_type": "span_start",
            "sequence": 1,
            "occurred_at": t0.isoformat(),
            "span_id": llm_id,
            "parent_span_id": agent_id,
            "type": "llm",
            "name": "model",
        },
        {
            "event_id": str(uuid4()),
            "event_type": "span_complete",
            "sequence": 2,
            "occurred_at": (t0 + timedelta(milliseconds=12)).isoformat(),
            "span_id": llm_id,
            "status": "ok",
            "duration_ms": 12,
            "token_count": 5,
            "cost_usd": 0.01,
        },
        {
            "event_id": str(uuid4()),
            "event_type": "span_start",
            "sequence": 3,
            "occurred_at": (t0 + timedelta(milliseconds=13)).isoformat(),
            "span_id": tool_id,
            "parent_span_id": agent_id,
            "type": "tool",
            "name": "search",
        },
        {
            "event_id": str(uuid4()),
            "event_type": "span_complete",
            "sequence": 4,
            "occurred_at": (t0 + timedelta(milliseconds=20)).isoformat(),
            "span_id": tool_id,
            "status": "ok",
            "duration_ms": 7,
            "output": {"hits": 1},
        },
    ]
    updated = client.post(f"/v1/runs/{trace_id}/events", json=events)
    assert updated.status_code == 200, updated.text
    body = updated.json()
    by_name = {span["name"]: span for span in body["spans"]}
    assert by_name["model"]["parent_span_id"] == agent_id
    assert by_name["search"]["parent_span_id"] == agent_id
    assert by_name["model"]["duration_ms"] == 12
    assert body["total_tokens"] == 5
    finalized = client.post(
        f"/v1/runs/{trace_id}/finalize",
        json={"status": "completed", "sequence": 5},
    )
    assert finalized.status_code == 200
    assert finalized.json()["lifecycle"] == "completed"
    assert finalized.json()["status"] == "ok"


def test_duplicate_events_do_not_duplicate_spans_or_totals(client: TestClient) -> None:
    created = _start(client)
    trace_id = created["trace_id"]
    span_id = str(uuid4())
    event_id = str(uuid4())
    event = {
        "event_id": event_id,
        "event_type": "span_complete",
        "sequence": 1,
        "span_id": span_id,
        "type": "llm",
        "name": "call",
        "status": "ok",
        "token_count": 9,
        "cost_usd": 0.3,
        "duration_ms": 4,
    }
    first = client.post(f"/v1/runs/{trace_id}/events", json=[event])
    second = client.post(f"/v1/runs/{trace_id}/events", json=[event])
    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["total_tokens"] == 9
    assert second.json()["total_tokens"] == 9
    assert len(second.json()["spans"]) == len(first.json()["spans"])


def test_invalid_lifecycle_rejected_duplicates_ok(client: TestClient) -> None:
    created = _start(client)
    trace_id = created["trace_id"]
    finalize_id = str(uuid4())
    done = client.post(
        f"/v1/runs/{trace_id}/finalize",
        json={"event_id": finalize_id, "status": "completed", "sequence": 9},
    )
    assert done.status_code == 200
    replay = client.post(
        f"/v1/runs/{trace_id}/finalize",
        json={"event_id": finalize_id, "status": "completed", "sequence": 9},
    )
    assert replay.status_code == 200
    rejected = client.post(
        f"/v1/runs/{trace_id}/events",
        json=[{
            "event_type": "span_start",
            "sequence": 10,
            "span_id": str(uuid4()),
            "type": "tool",
            "name": "late",
        }],
    )
    assert rejected.status_code == 409
    assert rejected.json()["detail"] == "Invalid lifecycle transition"
    heartbeat = client.post(
        f"/v1/runs/{trace_id}/events",
        json=[{"event_type": "heartbeat", "sequence": 11, "progress": "nope"}],
    )
    assert heartbeat.status_code == 409


def test_delayed_completion_after_finalize_is_reconciled(client: TestClient) -> None:
    created = _start(client)
    trace_id = created["trace_id"]
    agent_id = created["spans"][0]["span_id"]
    client.post(
        f"/v1/runs/{trace_id}/finalize",
        json={"status": "completed", "sequence": 3},
    )
    late = client.post(
        f"/v1/runs/{trace_id}/events",
        json=[{
            "event_type": "span_complete",
            "sequence": 2,
            "span_id": agent_id,
            "status": "ok",
            "token_count": 11,
            "duration_ms": 15,
        }],
    )
    assert late.status_code == 200, late.text
    body = late.json()
    assert body["lifecycle"] == "completed"
    assert body["total_tokens"] == 11


def test_cancelled_run_is_filterable_and_not_ok(client: TestClient) -> None:
    created = _start(client, attributes={"nm5.source": "cursor", "agent.framework": "cursor"})
    done = client.post(
        f"/v1/runs/{created['trace_id']}/finalize",
        json={"status": "cancelled", "sequence": 1, "attributes": {"nm5.outcome": "cancelled"}},
    )
    assert done.status_code == 200, done.text
    assert done.json()["lifecycle"] == "cancelled"
    assert done.json()["status"] == "unset"
    listing = client.get("/v1/traces", params={"lifecycle": "cancelled", "source": "cursor"})
    assert created["trace_id"] in {row["trace_id"] for row in listing.json()}


def test_batch_unset_stays_unknown_not_completed(client: TestClient) -> None:
    response = client.post("/v1/traces", json={"project_id": "legacy", "name": "unset-run"})
    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "unset"
    assert body["lifecycle"] == "unknown"
    listing = client.get("/v1/traces", params={"lifecycle": "unknown"})
    assert body["trace_id"] in {row["trace_id"] for row in listing.json()}


def test_partial_event_insert_failure_is_repaired_by_retry(
    repository: InMemoryTraceRepository,
) -> None:
    client = TestClient(create_app(repository))
    created = _start(client)
    trace_id = created["trace_id"]
    first_id = str(uuid4())
    second_id = str(uuid4())
    first_span = str(uuid4())
    second_span = str(uuid4())
    repository.fail_after_event_insert = repository._new_event_inserts + 1
    payload = [
        {
            "event_id": first_id,
            "event_type": "span_complete",
            "sequence": 1,
            "span_id": first_span,
            "type": "llm",
            "name": "one",
            "status": "ok",
            "token_count": 2,
            "duration_ms": 1,
        },
        {
            "event_id": second_id,
            "event_type": "span_complete",
            "sequence": 2,
            "span_id": second_span,
            "type": "tool",
            "name": "two",
            "status": "ok",
            "token_count": 3,
            "duration_ms": 1,
        },
    ]
    failed = client.post(f"/v1/runs/{trace_id}/events", json=payload)
    assert failed.status_code == 503
    after_fault = client.get(f"/v1/traces/{trace_id}").json()
    names = {span["name"] for span in after_fault["spans"]}
    assert "one" in names
    assert "two" not in names
    repository.fail_after_event_insert = None
    retried = client.post(f"/v1/runs/{trace_id}/events", json=payload)
    assert retried.status_code == 200, retried.text
    names = {span["name"] for span in retried.json()["spans"]}
    assert names >= {"demo-agent", "one", "two"}
    assert retried.json()["total_tokens"] == 5


def test_storage_failure_on_start_is_503(repository: InMemoryTraceRepository) -> None:
    repository.fail_after_event_insert = 1
    client = TestClient(create_app(repository))
    response = client.post("/v1/runs", json={"project_id": "x", "name": "y"})
    assert response.status_code == 503


def test_concurrent_appends_serialize_under_lock(client: TestClient) -> None:
    created = _start(client)
    trace_id = created["trace_id"]

    def send(index: int) -> int:
        response = client.post(
            f"/v1/runs/{trace_id}/events",
            json=[{
                "event_type": "span_complete",
                "sequence": index,
                "span_id": str(uuid4()),
                "type": "tool",
                "name": f"t{index}",
                "status": "ok",
                "duration_ms": 1,
                "token_count": 1,
            }],
        )
        return response.status_code

    with ThreadPoolExecutor(max_workers=4) as pool:
        codes = list(pool.map(send, range(1, 5)))
    assert codes == [200, 200, 200, 200]
    body = client.get(f"/v1/traces/{trace_id}").json()
    assert body["total_tokens"] == 4
    assert len([span for span in body["spans"] if span["type"] == "tool"]) == 4


def test_out_of_order_http_complete_before_start(client: TestClient) -> None:
    created = _start(client)
    trace_id = created["trace_id"]
    span_id = str(uuid4())
    complete = client.post(
        f"/v1/runs/{trace_id}/events",
        json=[{
            "event_type": "span_complete",
            "sequence": 2,
            "span_id": span_id,
            "status": "ok",
            "duration_ms": 9,
            "token_count": 6,
        }],
    )
    start = client.post(
        f"/v1/runs/{trace_id}/events",
        json=[{
            "event_type": "span_start",
            "sequence": 1,
            "span_id": span_id,
            "type": "llm",
            "name": "late-start",
            "status": "unset",
            "token_count": 1,
        }],
    )
    assert complete.status_code == 200
    assert start.status_code == 200
    span = next(item for item in start.json()["spans"] if item["span_id"] == span_id)
    assert span["status"] == "ok"
    assert span["token_count"] == 6
    assert span["name"] == "late-start"


def test_late_identity_verified_usage_after_finalize(client: TestClient) -> None:
    created = _start(
        client,
        attributes={
            "nm5.source": "cursor",
            "agent.framework": "cursor",
            "nm5.session_id": "conv-1",
            "nm5.conversation_id": "conv-1",
            "nm5.token_status": "unavailable",
            "nm5.cost_status": "unavailable",
        },
    )
    trace_id = created["trace_id"]
    done = client.post(
        f"/v1/runs/{trace_id}/finalize",
        json={"status": "cancelled", "sequence": 1, "attributes": {"nm5.outcome": "cancelled"}},
    )
    assert done.status_code == 200
    usage = {
        "event_id": str(uuid4()),
        "event_type": "span_complete",
        "sequence": 2,
        "span_id": str(uuid4()),
        "type": "llm",
        "name": "cursor-turn",
        "status": "ok",
        "token_count": 8,
        "attributes": {
            "nm5.usage_event": "stop",
            "nm5.token_status": "reported",
            "nm5.generation_id": "g1",
            "nm5.conversation_id": "conv-1",
            "nm5.usage_scope": "parent_agent",
        },
    }
    first = client.post(f"/v1/runs/{trace_id}/events", json=[usage])
    replay = client.post(f"/v1/runs/{trace_id}/events", json=[usage])
    assert first.status_code == 200, first.text
    assert replay.status_code == 200
    body = replay.json()
    assert body["lifecycle"] == "cancelled"
    assert body["attributes"]["nm5.outcome"] == "cancelled"
    assert body["total_tokens"] == 8
    assert body["attributes"]["nm5.token_status"] == "reported"
    rejected = client.post(
        f"/v1/runs/{trace_id}/events",
        json=[{
            "event_type": "heartbeat",
            "sequence": 3,
            "progress": "stop",
        }],
    )
    assert rejected.status_code == 409


def test_unknown_run_events_are_404(client: TestClient) -> None:
    missing = uuid4()
    response = client.post(
        f"/v1/runs/{missing}/events",
        json=[{"event_type": "heartbeat", "sequence": 1}],
    )
    assert response.status_code == 404


def test_failing_list_still_503_not_empty() -> None:
    class Broken(InMemoryTraceRepository):
        def list(self, query=None):  # type: ignore[no-untyped-def, override]
            raise TraceStorageError("down")

    with TestClient(create_app(Broken())) as client:
        response = client.get("/v1/traces")
        assert response.status_code == 503
