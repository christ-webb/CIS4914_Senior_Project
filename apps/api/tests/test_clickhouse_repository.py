"""Run against local ClickHouse with NM5_TEST_CLICKHOUSE=1."""

import os
from datetime import UTC, datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from no_more_500s_api.core.config import Settings
from no_more_500s_api.domain.models import (
    RunEventIn,
    RunEventType,
    RunStart,
    Span,
    SpanStatus,
    SpanType,
    StoredRunEvent,
    TraceCreate,
)
from no_more_500s_api.main import create_app
from no_more_500s_api.services.clickhouse_trace_repository import ClickHouseTraceRepository
from no_more_500s_api.services.trace_service import (
    TraceListQuery,
    TraceNotFoundError,
    TraceStorageError,
)

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


def _payload(
    project: str, name: str, started_at: datetime, trace_id: str | None = None
) -> dict[str, object]:
    body: dict[str, object] = {
        "project_id": project,
        "attributes": {"nm5.is_fixture": True},
        "name": name,
        "started_at": started_at.isoformat(),
        "spans": [
            {
                "type": "agent",
                "name": "parent",
                "started_at": started_at.isoformat(),
                "duration_ms": 50,
                "token_count": 2,
                "cost_usd": 0.01,
            }
        ],
    }
    if trace_id is not None:
        body["trace_id"] = trace_id
    return body


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


def test_failure_analysis_survives_clickhouse_reconnection() -> None:
    """New failure-analysis route must read persisted spans, not memory-only state."""
    project = f"failure-integration-{uuid4()}"
    with TestClient(create_app(repository())) as client:
        response = client.post(
            "/v1/traces",
            json={
                "project_id": project,
                "name": "repeated-lookup",
                "spans": [
                    {
                        "type": "tool",
                        "name": "lookup",
                        "duration_ms": 5,
                        "input": {"query": "same"},
                        "token_count": 10,
                        "cost_usd": 0.02,
                    }
                    for _ in range(3)
                ],
            },
        )
        assert response.status_code == 201, response.text
        trace_id = response.json()["trace_id"]

    with TestClient(create_app(repository())) as client:
        response = client.get(f"/v1/traces/{trace_id}/failures")
        assert response.status_code == 200, response.text
        findings = response.json()
        assert len(findings) == 1
        assert findings[0]["span_name"] == "lookup"
        assert findings[0]["repeat_count"] == 3
        assert findings[0]["wasted_tokens"] == 20
        assert findings[0]["wasted_cost_usd"] == pytest.approx(0.04)
        assert len(findings[0]["span_ids"]) == 3

        diagnostics = client.get(f"/v1/traces/{trace_id}/diagnostics")
        assert diagnostics.status_code == 200, diagnostics.text
        types = {item["detection_type"] for item in diagnostics.json()["findings"]}
        assert "repeated_tool_call" in types
        first_ids = [
            item["finding_id"]
            for item in diagnostics.json()["findings"]
            if item["detection_type"] == "repeated_tool_call"
        ]

    with TestClient(create_app(repository())) as client:
        again = client.get(f"/v1/traces/{trace_id}/diagnostics")
        assert again.status_code == 200, again.text
        again_ids = [
            item["finding_id"]
            for item in again.json()["findings"]
            if item["detection_type"] == "repeated_tool_call"
        ]
        assert again_ids == first_ids


def test_multiple_runs_survive_restart_and_later_inserts() -> None:
    project = f"history-{uuid4()}"
    now = datetime.now(UTC).replace(microsecond=0)
    ids: list[str] = []
    with TestClient(create_app(repository())) as client:
        for index in range(3):
            response = client.post(
                "/v1/traces",
                json=_payload(project, f"run-{index}", now + timedelta(seconds=index)),
            )
            assert response.status_code == 201, response.text
            ids.append(response.json()["trace_id"])

    with TestClient(create_app(repository())) as client:
        listed = [row for row in client.get("/v1/traces", params={"project_id": project}).json()]
        assert [row["name"] for row in listed] == ["run-2", "run-1", "run-0"]
        for trace_id in ids:
            assert client.get(f"/v1/traces/{trace_id}").status_code == 200
        extra = client.post(
            "/v1/traces",
            json=_payload(project, "run-3", now + timedelta(seconds=3)),
        )
        assert extra.status_code == 201, extra.text
        listed = client.get("/v1/traces", params={"project_id": project}).json()
        assert [row["name"] for row in listed] == ["run-3", "run-2", "run-1", "run-0"]
        assert extra.json()["trace_id"] in {row["trace_id"] for row in listed}
        projects = client.get("/v1/projects").json()
        assert project in projects
        page = client.get("/v1/traces", params={"project_id": project, "limit": 2, "offset": 0})
        assert [row["name"] for row in page.json()] == ["run-3", "run-2"]


def test_duplicate_trace_id_does_not_duplicate_clickhouse_spans() -> None:
    project = f"dup-{uuid4()}"
    trace_id = str(uuid4())
    payload = _payload(project, "once", datetime.now(UTC), trace_id=trace_id)
    with TestClient(create_app(repository())) as client:
        assert client.post("/v1/traces", json=payload).status_code == 201
        conflict = client.post("/v1/traces", json=payload)
        assert conflict.status_code == 409
        detail = client.get(f"/v1/traces/{trace_id}").json()
        assert len(detail["spans"]) == 1
        assert detail["total_tokens"] == 2


def test_header_insert_failure_then_retry_does_not_inflate_visible_spans() -> None:
    repo = repository()
    trace_id = uuid4()
    started = datetime.now(UTC).replace(microsecond=0)
    payload = TraceCreate(
        trace_id=trace_id,
        project_id=f"partial-{uuid4()}",
        name="retry-safe",
        started_at=started,
        spans=[
            Span(type=SpanType.TOOL, name="only", duration_ms=4, token_count=7, cost_usd=0.03),
        ],
    )
    original_insert_trace = repo._insert_trace

    def fail_once(trace, ingestion_id):  # type: ignore[no-untyped-def]
        raise RuntimeError("simulated header failure")

    repo._insert_trace = fail_once  # type: ignore[method-assign]
    with pytest.raises(TraceStorageError):
        repo.save(payload)
    repo._insert_trace = original_insert_trace  # type: ignore[method-assign]

    with pytest.raises(TraceNotFoundError):
        repo.get(trace_id)

    stored = repo.save(payload)
    assert stored.trace_id == trace_id
    fetched = repo.get(trace_id)
    assert len(fetched.spans) == 1
    assert fetched.total_tokens == 7
    raw = repo._client_or_raise().query(
        "SELECT count() FROM spans WHERE trace_id = {trace_id:UUID}",
        parameters={"trace_id": str(trace_id)},
    )
    assert raw.result_rows[0][0] >= 2


def test_clickhouse_outage_is_503() -> None:
    broken = ClickHouseTraceRepository(
        host="127.0.0.1",
        port=1,
        database="nomore500s",
        username="default",
        password="wrong",
    )
    with TestClient(create_app(broken)) as client:
        listing = client.get("/v1/traces")
        assert listing.status_code == 503
        assert listing.json()["detail"] == "Trace storage is unavailable"
        created = client.post(
            "/v1/traces",
            json=_payload("outage", "nope", datetime.now(UTC)),
        )
        assert created.status_code == 503


def test_clickhouse_filters_before_limit() -> None:
    repo = repository()
    now = datetime.now(UTC).replace(microsecond=0)
    project = f"filter-{uuid4()}"
    other = f"other-{uuid4()}"
    for name, offset, target in (
        ("keep-old", -20, project),
        ("other", -10, other),
        ("keep-new", 0, project),
    ):
        repo.save(
            TraceCreate(
                project_id=target,
                name=name,
                started_at=now + timedelta(seconds=offset),
                spans=[Span(type=SpanType.AGENT, name="n", duration_ms=1)],
            )
        )
    page = list(repo.list(TraceListQuery(project_id=project, limit=1, offset=0)))
    assert [row.name for row in page] == ["keep-new"]


def test_clickhouse_started_before_accepts_z_and_offsets() -> None:
    repo = repository()
    started = datetime(2026, 11, 17, 10, 0, 0, 123000, tzinfo=UTC)
    project = f"before-{uuid4()}"
    repo.save(
        TraceCreate(
            project_id=project,
            name="in-window",
            started_at=started,
            spans=[Span(type=SpanType.TOOL, name="t", duration_ms=1)],
        )
    )
    later = repo.save(
        TraceCreate(
            project_id=project,
            name="after-window",
            started_at=started + timedelta(hours=2),
            spans=[Span(type=SpanType.TOOL, name="t", duration_ms=1)],
        )
    )
    cutoff = datetime(2026, 11, 17, 10, 33, 0, tzinfo=UTC)
    eastern = cutoff.astimezone(timezone(timedelta(hours=-5)))
    names = {
        row.name
        for row in repo.list(TraceListQuery(project_id=project, started_before=eastern, limit=21))
    }
    assert "in-window" in names
    assert "after-window" not in names
    with TestClient(create_app(repository())) as client:
        response = client.get(
            "/v1/traces",
            params={
                "project_id": project,
                "started_before": "2026-11-17T10:33:00.000Z",
                "limit": 21,
            },
        )
        assert response.status_code == 200, response.text
        listed = {row["name"] for row in response.json()}
        assert "in-window" in listed
        assert "after-window" not in listed
        detail = client.get(f"/v1/traces/{later.trace_id}")
        assert detail.status_code == 200


def test_live_run_survives_api_restart_and_does_not_corrupt_batch() -> None:
    project = f"live-{uuid4()}"
    batch_project = f"batch-{uuid4()}"
    with TestClient(create_app(repository())) as client:
        batch = client.post(
            "/v1/traces",
            json=_payload(batch_project, "historical", datetime.now(UTC)),
        )
        assert batch.status_code == 201, batch.text
        batch_id = batch.json()["trace_id"]
        started = client.post(
            "/v1/runs",
            json={"project_id": project, "name": "live-one", "agent_name": "agent"},
        )
        assert started.status_code == 201, started.text
        trace_id = started.json()["trace_id"]
        agent_id = started.json()["spans"][0]["span_id"]
        appended = client.post(
            f"/v1/runs/{trace_id}/events",
            json=[
                {
                    "event_type": "span_complete",
                    "sequence": 1,
                    "span_id": agent_id,
                    "status": "ok",
                    "token_count": 4,
                    "duration_ms": 8,
                }
            ],
        )
        assert appended.status_code == 200, appended.text

    with TestClient(create_app(repository())) as client:
        live = client.get(f"/v1/traces/{trace_id}").json()
        assert live["lifecycle"] == "running"
        assert live["total_tokens"] == 4
        historical = client.get(f"/v1/traces/{batch_id}").json()
        assert historical["name"] == "historical"
        assert historical["lifecycle"] == "unknown"
        done = client.post(
            f"/v1/runs/{trace_id}/finalize",
            json={"status": "completed", "sequence": 2},
        )
        assert done.status_code == 200
        assert done.json()["lifecycle"] == "completed"

    with TestClient(create_app(repository())) as client:
        assert client.get(f"/v1/traces/{trace_id}").json()["lifecycle"] == "completed"
        replay = client.post(
            f"/v1/runs/{trace_id}/events",
            json=[
                {
                    "event_type": "heartbeat",
                    "sequence": 9,
                    "progress": "too-late",
                }
            ],
        )
        assert replay.status_code == 409


def test_clickhouse_retry_after_event_insert_fault_is_idempotent() -> None:
    repo = repository()
    started = repo.start_run(
        RunStart(
            project_id=f"fault-{uuid4()}",
            name="partial",
            agent_name="agent",
        )
    )

    first = RunEventIn(
        event_type="span_complete",
        sequence=1,
        span_id=uuid4(),
        type=SpanType.LLM,
        name="one",
        status=SpanStatus.OK,
        token_count=2,
        duration_ms=1,
    )
    second = RunEventIn(
        event_type="span_complete",
        sequence=2,
        span_id=uuid4(),
        type=SpanType.TOOL,
        name="two",
        status=SpanStatus.OK,
        token_count=3,
        duration_ms=1,
    )
    repo.fail_after_event_insert = repo._new_event_inserts + 1
    with pytest.raises(TraceStorageError):
        repo.append_events(started.trace_id, [first, second])
    mid = repo.get(started.trace_id)
    assert any(span.name == "one" for span in mid.spans)
    assert not any(span.name == "two" for span in mid.spans)
    repo.fail_after_event_insert = None
    repaired = repo.append_events(started.trace_id, [first, second])
    assert repaired.total_tokens == 5
    names = {span.name for span in repaired.spans}
    assert names >= {"agent", "one", "two"}


def test_live_usage_event_id_replay_does_not_inflate_tokens() -> None:
    repo = repository()
    started = repo.start_run(
        RunStart(
            project_id=f"usage-{uuid4()}",
            name="geometry-replay",
            agent_name="cursor",
            attributes={"nm5.source": "cursor", "agent.framework": "cursor"},
        )
    )
    span_id = uuid4()
    event_id = uuid4()
    usage = RunEventIn(
        event_id=event_id,
        event_type="span_complete",
        sequence=1,
        span_id=span_id,
        type=SpanType.LLM,
        name="grok-4.7",
        status=SpanStatus.OK,
        token_count=183649,
        attributes={
            "nm5.usage_event": "stop",
            "nm5.token_status": "reported",
            "nm5.generation_id": "g1",
            "nm5.usage_scope": "parent_agent",
            "nm5.token_accounting": "inclusive_cache",
            "nm5.billable_breakdown": "complete",
            "nm5.model_id": "grok-4.7",
            "gen_ai.usage.input_tokens": 180819,
            "gen_ai.usage.output_tokens": 2830,
            "gen_ai.usage.cache_read_tokens": 137088,
            "gen_ai.usage.cache_write_tokens": 0,
        },
    )
    first = repo.append_events(started.trace_id, [usage])
    second = repo.append_events(started.trace_id, [usage])
    assert first.total_tokens == 183649
    assert second.total_tokens == 183649
    fetched = repo.get(started.trace_id)
    assert fetched.total_tokens == 183649
    assert fetched.attributes.get("nm5.estimated_cost_status") == "unavailable"
    assert fetched.attributes.get("nm5.scenario_standard_usd") is not None


def test_batch_summary_retains_explicit_fixture_and_scenario_fields() -> None:
    from no_more_500s_api.domain.models import TraceCreate

    repo = repository()
    trace = repo.save(
        TraceCreate(
            project_id=f"summary-{uuid4()}",
            name="priced fixture",
            started_at=datetime.now(UTC),
            spans=[],
            attributes={
                "nm5.is_fixture": True,
                "nm5.source": "cursor",
                "nm5.scenario_standard_usd": 0.2,
                "nm5.scenario_fast_usd": 0.4,
            },
        )
    )
    rows = list(repo.list(TraceListQuery(project_id=trace.project_id)))
    assert len(rows) == 1 and rows[0].is_fixture
    assert rows[0].scenario_standard_usd == 0.2
    assert rows[0].scenario_fast_usd == 0.4
    assert list(repo.list(TraceListQuery(project_id=trace.project_id, fixtures="exclude"))) == []


def test_completed_turn_survives_restart_and_chat_closure() -> None:
    project = f"turns-{uuid4()}"
    with TestClient(create_app(repository())) as client:
        started = client.post(
            "/v1/runs",
            json={
                "project_id": project,
                "name": "cursor-readme",
                "agent_name": "cursor",
                "attributes": {"nm5.source": "cursor", "agent.framework": "cursor"},
            },
        )
        assert started.status_code == 201, started.text
        trace_id = started.json()["trace_id"]
        appended = client.post(
            f"/v1/runs/{trace_id}/events",
            json=[
                {
                    "event_type": "heartbeat",
                    "sequence": 1,
                    "progress": "afterAgentResponse",
                    "attributes": {"nm5.generation_id": "g1"},
                },
                {
                    "event_type": "heartbeat",
                    "sequence": 2,
                    "progress": "stop",
                    "attributes": {"nm5.generation_id": "g1", "nm5.turn_status": "completed"},
                },
            ],
        )
        assert appended.status_code == 200, appended.text

    with TestClient(create_app(repository())) as client:
        rows = client.get("/v1/traces", params={"project_id": project}).json()
        assert rows[0]["lifecycle"] == "running"
        assert rows[0]["latest_turn_status"] == "completed"
        assert rows[0]["turns_completed"] == 1
        assert rows[0]["task_outcome"] == "unverified"
        closed = client.post(
            f"/v1/runs/{trace_id}/finalize",
            json={
                "status": "cancelled",
                "sequence": 3,
                "attributes": {"nm5.end_reason": "user_close"},
            },
        )
        assert closed.status_code == 200, closed.text

    with TestClient(create_app(repository())) as client:
        rows = client.get(
            "/v1/traces", params={"project_id": project, "activity": "completed_turns"}
        ).json()
        assert len(rows) == 1
        assert rows[0]["lifecycle"] == "completed"
        assert rows[0]["turns_completed"] == 1
        assert rows[0]["turns_failed"] == 0
        assert rows[0]["task_outcome"] == "unverified"


def test_historical_cursor_closures_reconstruct_without_rewrites() -> None:
    repo = repository()
    project = f"history-{uuid4()}"
    t0 = datetime.now(UTC).replace(microsecond=0) - timedelta(hours=1)
    start_attrs = {"nm5.source": "cursor", "agent.framework": "cursor"}
    legacy_close = {
        "status": "cancelled",
        "error": {"reason": "user_close", "outcome": "cancelled"},
        "attributes": {"nm5.end_reason": "user_close", "nm5.outcome": "cancelled"},
    }

    def write(trace_id: UUID, progress: list[tuple[str, dict[str, str]]]) -> None:
        start = {
            "project_id": project,
            "name": "legacy",
            "started_at": t0.isoformat(),
            "attributes": start_attrs,
        }
        events: list[tuple[RunEventType, dict[str, object]]] = [(RunEventType.RUN_START, start)]
        events += [(RunEventType.HEARTBEAT, {"progress": p, "attributes": a}) for p, a in progress]
        events.append((RunEventType.FINALIZE, legacy_close))
        for sequence, (kind, payload) in enumerate(events):
            repo._insert_run_event(
                StoredRunEvent(
                    event_id=uuid4(),
                    trace_id=trace_id,
                    event_type=kind,
                    sequence=sequence,
                    occurred_at=t0 + timedelta(seconds=sequence),
                    received_at=t0 + timedelta(seconds=sequence),
                    payload=payload,
                )
            )

    finished, unproven = uuid4(), uuid4()
    write(finished, [
        ("afterAgentResponse", {"nm5.generation_id": "g1"}),
        ("stop", {"nm5.generation_id": "g1", "nm5.turn_status": "completed"}),
    ])
    write(unproven, [("afterAgentResponse", {"nm5.generation_id": "g1"})])

    def stored_rows() -> int:
        return int(
            repo._client_or_raise()
            .query(
                "SELECT count() FROM run_events WHERE trace_id IN ({a:UUID}, {b:UUID})",
                parameters={"a": str(finished), "b": str(unproven)},
            )
            .result_rows[0][0]
        )

    before = stored_rows()
    detail = repo.get(finished)
    assert detail.lifecycle.value == "completed"
    assert detail.attributes["nm5.end_reason"] == "user_close"
    assert detail.attributes["nm5.task_outcome_status"] == "unverified"
    assert repo.get(unproven).lifecycle.value == "cancelled"
    rows = repo.list(TraceListQuery(project_id=project))
    listed = {row.trace_id: row.lifecycle.value for row in rows}
    assert listed == {finished: "completed", unproven: "cancelled"}
    assert stored_rows() == before
