from __future__ import annotations

from pathlib import Path

import pytest
from nomore500s_collector.api_client import LiveRunClient
from nomore500s_collector.pipeline import Pipeline
from nomore500s_collector.store import CollectorStore


class RecordingClient(LiveRunClient):
    def __init__(self) -> None:
        super().__init__("http://example.invalid")
        self.calls: list[tuple[str, object]] = []
        self.fail_until = 0
        self._attempts = 0

    def post(self, path: str, payload: object) -> int:
        self._attempts += 1
        if self._attempts <= self.fail_until:
            from nomore500s_collector.api_client import DeliveryError

            raise DeliveryError("simulated outage", retryable=True)
        self.calls.append((path, payload))
        return 201


@pytest.fixture(autouse=True)
def isolate_history_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NM5_HISTORY_HOME", str(tmp_path / "history-home"))


@pytest.fixture
def store(tmp_path: Path) -> CollectorStore:
    return CollectorStore(tmp_path / "collector.sqlite", max_pending=20)


@pytest.fixture
def pipeline(store: CollectorStore) -> tuple[Pipeline, RecordingClient]:
    client = RecordingClient()
    return Pipeline(store, client), client
