from __future__ import annotations

from datetime import UTC, datetime, timedelta
from io import BytesIO
from pathlib import Path
from urllib.error import HTTPError

from fastapi.testclient import TestClient
from nomore500s_collector.adapters.cursor import CursorAdapter
from nomore500s_collector.creation_evidence import (
    EVIDENCE_MODES,
    OBSERVED_AT_FIELD,
    SOURCE_FIELD,
    stamp,
    verify,
)
from nomore500s_collector.history_guard import FILTER_BYTES, remember
from nomore500s_collector.hook import post_hook, run_hook
from nomore500s_collector.redaction import redact_payload
from nomore500s_collector.server import CollectorApp, create_app
from nomore500s_collector.settings import CollectorSettings

from .conftest import RecordingClient


def _deny(*identities: tuple[str, str]) -> bytes:
    bits = bytearray(FILTER_BYTES)
    for namespace, identity in identities:
        remember(bits, namespace, identity)
    return bytes(bits)


def test_stamp_only_creation_events() -> None:
    start = {"hook_event_name": "sessionStart", "session_id": "s"}
    assert stamp(start, "cursor") is True
    assert OBSERVED_AT_FIELD in start
    assert start[SOURCE_FIELD] == "cursor"
    first = start[OBSERVED_AT_FIELD]
    assert stamp(start, "cursor") is True
    assert start[OBSERVED_AT_FIELD] == first

    tool = {"hook_event_name": "postToolUse", "session_id": "s"}
    assert stamp(tool, "cursor") is False
    assert OBSERVED_AT_FIELD not in tool

    inferred = {
        "hook_event_name": "SessionStart",
        "session_id": "s",
        "source": "inferred",
    }
    assert stamp(inferred, "codex") is False
    assert OBSERVED_AT_FIELD not in inferred


def test_verify_reason_codes() -> None:
    now = datetime.now(UTC)
    boundary = (now - timedelta(minutes=1)).isoformat()
    bits = _deny(("cursor", "old"))

    assert verify(
        "cursor",
        {"hook_event_name": "sessionStart"},
        boundary=boundary,
        now=now,
        deny_bits=bits,
        known_session=False,
    ) == (False, "missing_identity")
    assert verify(
        "cursor",
        {"session_id": "old", "hook_event_name": "sessionStart"},
        boundary=boundary,
        now=now,
        deny_bits=bits,
        known_session=False,
    ) == (False, "denied_identity")
    assert verify(
        "cursor",
        {"session_id": "n", "hook_event_name": "sessionStart"},
        boundary=boundary,
        now=now,
        deny_bits=b"",
        known_session=False,
    ) == (False, "filter_unavailable")
    assert verify(
        "cursor",
        {"session_id": "n", "hook_event_name": "afterAgentThought"},
        boundary=boundary,
        now=now,
        deny_bits=bits,
        known_session=False,
    ) == (False, "not_session_start")
    assert verify(
        "cursor",
        {"session_id": "n", "hook_event_name": "sessionStart"},
        boundary=boundary,
        now=now,
        deny_bits=bits,
        known_session=False,
    ) == (False, "missing_creation_evidence")
    stale = {
        "session_id": "n",
        "hook_event_name": "sessionStart",
        OBSERVED_AT_FIELD: (now - timedelta(minutes=10)).isoformat(),
    }
    assert verify(
        "cursor",
        stale,
        boundary=boundary,
        now=now,
        deny_bits=bits,
        known_session=False,
    ) == (False, "stale_creation_evidence")
    resume = {
        "session_id": "n",
        "hook_event_name": "SessionStart",
        "source": "resume",
        OBSERVED_AT_FIELD: now.isoformat(),
    }
    assert verify(
        "codex",
        resume,
        boundary=boundary,
        now=now,
        deny_bits=bits,
        known_session=False,
    ) == (False, "untrusted_session_source")
    known = {"session_id": "n", "hook_event_name": "afterAgentThought"}
    assert verify(
        "cursor",
        known,
        boundary=boundary,
        now=now,
        deny_bits=bits,
        known_session=True,
    ) == (True, "known_session")
    fresh = {
        "session_id": "n",
        "hook_event_name": "sessionStart",
        OBSERVED_AT_FIELD: now.isoformat(),
    }
    assert verify(
        "cursor",
        fresh,
        boundary=boundary,
        now=now,
        deny_bits=bits,
        known_session=False,
    ) == (True, "fresh_session")


def test_redaction_keeps_evidence_and_adapters_do_not_leak() -> None:
    payload = {
        "hook_event_name": "sessionStart",
        "session_id": "s",
        "conversation_id": "s",
        "prompt": "secret",
    }
    stamp(payload, "cursor")
    cleaned = redact_payload(payload)
    assert OBSERVED_AT_FIELD in cleaned
    assert SOURCE_FIELD in cleaned
    assert "prompt" not in cleaned
    plan = CursorAdapter().plan(cleaned, delivery_id="d1")
    attributes = plan.events[0].payload["attributes"]
    assert OBSERVED_AT_FIELD not in attributes
    assert SOURCE_FIELD not in attributes
    assert "prompt" not in attributes


def test_health_reports_evidence_modes(tmp_path: Path) -> None:
    settings = CollectorSettings(collector_home=tmp_path, collector_token="token")
    runtime = CollectorApp(settings)
    runtime.pipeline.client = RecordingClient()  # type: ignore[method-assign]
    with TestClient(create_app(runtime)) as client:
        health = client.get("/health")
        assert health.status_code == 200
        assert health.json()["creation_evidence_modes"] == list(EVIDENCE_MODES)


def test_hook_logs_rejection_reason(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("NM5_HOOK_DEBUG", "1")
    monkeypatch.setenv("NM5_COLLECTOR_HOME", str(tmp_path))
    monkeypatch.setenv("NM5_COLLECTOR_URL", "http://127.0.0.1:8788")
    monkeypatch.setenv("NM5_COLLECTOR_TOKEN", "token")

    class _Error(HTTPError):
        def __init__(self) -> None:
            super().__init__(
                "http://127.0.0.1:8788/v1/collector/events",
                409,
                "Conflict",
                hdrs=None,  # type: ignore[arg-type]
                fp=BytesIO(
                    b'{"status":"rejected_reset_boundary","reason":"denied_identity"}'
                ),
            )

    monkeypatch.setattr(
        "nomore500s_collector.hook.urlopen",
        lambda *args, **kwargs: (_ for _ in ()).throw(_Error()),
    )
    payload = {"hook_event_name": "sessionStart", "session_id": "old"}
    assert post_hook(payload, "d1", source="cursor") == "http_409"
    log = (tmp_path / "hook.log").read_text(encoding="utf-8")
    assert "http_status=409" in log
    assert "result=rejected_reset_boundary" in log
    assert "reason=denied_identity" in log
    assert "old" not in log


def test_run_hook_stamps_before_delivery(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    captured: dict[str, object] = {}

    def _capture(payload: dict[str, object], delivery_id: str, *, source: str | None = None) -> str:
        captured["payload"] = dict(payload)
        captured["source"] = source
        captured["delivery_id"] = delivery_id
        return "delivered"

    monkeypatch.setattr("nomore500s_collector.hook.post_hook", _capture)
    assert (
        run_hook(
            '{"hook_event_name":"sessionStart","session_id":"s"}', source="cursor"
        )
        == 0
    )
    payload = captured["payload"]
    assert isinstance(payload, dict)
    assert OBSERVED_AT_FIELD in payload
    assert payload[SOURCE_FIELD] == "cursor"
