from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient
from nomore500s_collector.server import CollectorApp, create_app
from nomore500s_collector.settings import CollectorSettings

from .conftest import RecordingClient


def test_requires_auth_and_accepts_hooks(tmp_path: Path) -> None:
    settings = CollectorSettings(collector_home=tmp_path, collector_token="secret-token")
    runtime = CollectorApp(settings)
    runtime.pipeline.client = RecordingClient()  # type: ignore[method-assign]
    with TestClient(create_app(runtime)) as client:
        denied = client.post(
            "/v1/collector/events",
            json={"session_id": "s", "hook_event_name": "SessionStart", "cwd": "/tmp/ws"},
        )
        assert denied.status_code == 401
        accepted = client.post(
            "/v1/collector/events",
            headers={"Authorization": "Bearer secret-token", "X-NM5-Delivery-Id": "d1"},
            json={
                "session_id": "s",
                "hook_event_name": "SessionStart",
                "cwd": "/tmp/ws",
                "model": "gpt-5",
            },
        )
        assert accepted.status_code == 202
        assert accepted.json()["status"] == "accepted"


def test_hook_offline_does_not_raise() -> None:
    from nomore500s_collector.hook import run_hook

    assert run_hook('{"session_id":"s","hook_event_name":"SessionStart"}') == 0
