from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient
from nomore500s_collector.hook import MAX_HOOK_LOG_BYTES, append_hook_debug, run_hook
from nomore500s_collector.pipeline import Pipeline
from nomore500s_collector.server import CollectorApp, create_app
from nomore500s_collector.settings import CollectorSettings
from nomore500s_collector.store import CollectorStore

from .conftest import RecordingClient


def _cursor_start() -> dict[str, object]:
    return {
        "hook_event_name": "sessionStart",
        "conversation_id": "conv-1",
        "session_id": "conv-1",
        "workspace_roots": ["/tmp/demo"],
        "model_id": "composer-2.5",
        "composer_mode": "agent",
        "cursor_version": "2.2.0",
    }


def test_cursor_session_and_tools_flush_to_api(
    pipeline: tuple[Pipeline, RecordingClient],
) -> None:
    pipe, client = pipeline
    accepted = pipe.ingest(_cursor_start(), "c-start", source="cursor")
    assert accepted["status"] == "accepted"
    pipe.ingest(
        {
            "hook_event_name": "postToolUse",
            "conversation_id": "conv-1",
            "tool_use_id": "t1",
            "tool_name": "Shell",
            "duration": 12,
        },
        "c-tool",
        source="cursor",
    )
    pipe.ingest(
        {
            "hook_event_name": "postToolUseFailure",
            "conversation_id": "conv-1",
            "tool_use_id": "t2",
            "tool_name": "Read",
            "is_interrupt": False,
            "failure_type": "error",
            "error_message": "missing",
        },
        "c-fail",
        source="cursor",
    )
    pipe.flush_once()
    start = next(payload for path, payload in client.calls if path == "/v1/runs")
    assert start["attributes"]["nm5.source"] == "cursor"
    events = [payload for path, payload in client.calls if str(path).endswith("/events")]
    kinds = [item["type"] for batch in events for item in batch]
    assert "tool" in kinds


def test_missing_session_start_is_inferred(
    pipeline: tuple[Pipeline, RecordingClient],
) -> None:
    pipe, client = pipeline
    pipe.ingest(
        {
            "hook_event_name": "postToolUse",
            "conversation_id": "late",
            "tool_use_id": "t1",
            "tool_name": "Read",
            "cwd": "/tmp/late",
        },
        "late-tool",
        source="cursor",
    )
    pipe.flush_once()
    starts = [payload for path, payload in client.calls if path == "/v1/runs"]
    assert len(starts) == 1
    assert starts[0]["attributes"]["nm5.source"] == "cursor"


def test_duplicate_cursor_delivery_is_idempotent(
    pipeline: tuple[Pipeline, RecordingClient],
) -> None:
    pipe, _client = pipeline
    first = pipe.ingest(_cursor_start(), "same", source="cursor")
    second = pipe.ingest(_cursor_start(), "same", source="cursor")
    assert first["status"] == "accepted"
    assert second["status"] == "duplicate_delivery"


def test_cursor_restart_replays_sqlite(tmp_path: Path) -> None:
    db = tmp_path / "collector.sqlite"
    store = CollectorStore(db)
    client = RecordingClient()
    client.fail_until = 1
    pipe = Pipeline(store, client)
    pipe.ingest(_cursor_start(), "d-start", source="cursor")
    assert pipe.flush_once() == 0
    store.close()

    store2 = CollectorStore(db)
    client2 = RecordingClient()
    pipe2 = Pipeline(store2, client2)
    assert pipe2.flush_once() == 1
    assert client2.calls[0][0] == "/v1/runs"
    assert client2.calls[0][1]["attributes"]["nm5.source"] == "cursor"


def test_cursor_http_ingest_uses_token_file(tmp_path: Path) -> None:
    settings = CollectorSettings(collector_home=tmp_path, collector_token="cursor-token")
    runtime = CollectorApp(settings)
    runtime.pipeline.client = RecordingClient()  # type: ignore[method-assign]
    with TestClient(create_app(runtime)) as client:
        denied = client.post(
            "/v1/collector/events",
            headers={"X-NM5-Delivery-Id": "d0", "X-NM5-Source": "cursor"},
            json=_cursor_start(),
        )
        assert denied.status_code == 401
        accepted = client.post(
            "/v1/collector/events",
            headers={
                "Authorization": "Bearer cursor-token",
                "X-NM5-Delivery-Id": "d1",
                "X-NM5-Source": "cursor",
            },
            json=_cursor_start(),
        )
        assert accepted.status_code == 202


def test_hook_uses_token_file_not_env(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.delenv("NM5_COLLECTOR_TOKEN", raising=False)
    monkeypatch.setenv("NM5_COLLECTOR_HOME", str(tmp_path))
    monkeypatch.setenv("NM5_COLLECTOR_URL", "http://127.0.0.1:1")
    (tmp_path / "token").write_text("file-token", encoding="utf-8")
    assert run_hook(
        '{"hook_event_name":"sessionStart","session_id":"s"}', source="cursor"
    ) == 0


def test_debug_log_is_bounded(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("NM5_HOOK_DEBUG", "1")
    monkeypatch.setenv("NM5_COLLECTOR_HOME", str(tmp_path))
    log = tmp_path / "hook.log"
    log.write_bytes(b"x" * (MAX_HOOK_LOG_BYTES + 10))
    append_hook_debug("python_hook event=sessionStart status=delivered")
    assert log.stat().st_size <= MAX_HOOK_LOG_BYTES
    assert "python_hook event=sessionStart status=delivered" in log.read_text(encoding="utf-8")
