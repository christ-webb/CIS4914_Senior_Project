from __future__ import annotations

from pathlib import Path

from nomore500s_collector.pipeline import Pipeline
from nomore500s_collector.store import CollectorStore

from .conftest import RecordingClient


def _session_start() -> dict[str, object]:
    return {
        "hook_event_name": "SessionStart",
        "session_id": "thr_1",
        "cwd": "/tmp/demo-workspace",
        "model": "gpt-5",
        "source": "startup",
        "prompt": "do not store",
    }


def test_duplicate_delivery_reuses_event_ids(pipeline: tuple[Pipeline, RecordingClient]) -> None:
    pipe, _client = pipeline
    first = pipe.ingest(_session_start(), "del-1")
    second = pipe.ingest(_session_start(), "del-1")
    assert first["status"] == "accepted"
    assert second["status"] == "duplicate_delivery"
    pipe.flush_once()
    pipe.flush_once()
    starts = [call for call in _client.calls if call[0] == "/v1/runs"]
    assert len(starts) == 1


def test_stop_does_not_finalize(pipeline: tuple[Pipeline, RecordingClient]) -> None:
    pipe, client = pipeline
    pipe.ingest(_session_start(), "del-start")
    pipe.ingest(
        {
            "hook_event_name": "Stop",
            "session_id": "thr_1",
            "turn_id": "turn-a",
            "cwd": "/tmp/demo-workspace",
        },
        "del-stop",
    )
    pipe.flush_once()
    kinds = [call[0] for call in client.calls]
    assert any(path.endswith("/events") for path in kinds)
    assert not any(path.endswith("/finalize") for path in kinds)


def test_repeated_model_requests_are_distinct(pipeline: tuple[Pipeline, RecordingClient]) -> None:
    pipe, client = pipeline
    pipe.ingest(_session_start(), "s")
    for index in range(2):
        pipe.ingest(
            {
                "hook_event_name": "PreModelRequest",
                "session_id": "thr_1",
                "model": "gpt-5",
                "request_id": f"req-{index}",
            },
            f"pre-{index}",
        )
        pipe.ingest(
            {
                "hook_event_name": "PostModelRequest",
                "session_id": "thr_1",
                "model": "gpt-5",
                "request_id": f"req-{index}",
                "status": "completed",
                "token_usage": {"prompt_tokens": 1, "completion_tokens": 1},
            },
            f"post-{index}",
        )
    pipe.flush_once()
    events = [payload for path, payload in client.calls if str(path).endswith("/events")]
    starts = [
        item
        for batch in events
        for item in batch  # type: ignore[union-attr]
        if item["event_type"] == "span_start" and item["type"] == "llm"
    ]
    assert len(starts) == 2
    assert starts[0]["span_id"] != starts[1]["span_id"]


def test_api_outage_replays_after_restart(tmp_path: Path) -> None:
    db = tmp_path / "collector.sqlite"
    store = CollectorStore(db)
    client = RecordingClient()
    client.fail_until = 1
    pipe = Pipeline(store, client)
    pipe.ingest(_session_start(), "d-start")
    assert pipe.flush_once() == 0
    store.close()

    store2 = CollectorStore(db)
    client2 = RecordingClient()
    pipe2 = Pipeline(store2, client2)
    assert pipe2.flush_once() == 1
    assert client2.calls[0][0] == "/v1/runs"


def test_redaction_before_persist(pipeline: tuple[Pipeline, RecordingClient]) -> None:
    pipe, _client = pipeline
    pipe.ingest(_session_start(), "d1")
    row = pipe.store._conn.execute("SELECT payload_json FROM pending").fetchone()
    assert row is not None
    assert "do not store" not in row["payload_json"]
    assert "secret" not in row["payload_json"]
