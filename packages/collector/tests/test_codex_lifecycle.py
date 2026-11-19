"""Codex CLI / exec / IDE lifecycle through the collector pipeline.

Payload shapes mirror a live `codex exec` probe on codex-cli 0.153.4: the event
order was SessionStart(source=startup) -> UserPromptSubmit -> PreToolUse ->
PostToolUse(tool_response=str) -> Stop(stop_hook_active=false) ->
SessionEnd(reason=other).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from nomore500s_collector.adapters.codex import CodexAdapter
from nomore500s_collector.creation_evidence import OBSERVED_AT_FIELD
from nomore500s_collector.history_guard import FILTER_BYTES, remember
from nomore500s_collector.ids import store_session_key, trace_id_for_session
from nomore500s_collector.pipeline import Pipeline
from nomore500s_collector.store import CollectorStore

from .conftest import RecordingClient


def _event(name: str, session: str = "thr-1", surface: str = "codex_exec", **extra: Any) -> dict:
    payload: dict[str, Any] = {
        "hook_event_name": name,
        "session_id": session,
        "cwd": "/tmp/fixture-ws",
        "model": "gpt-5",
        "transcript_path": "/nonexistent/rollout.jsonl",
        "_nm5_codex_originator": surface,
        "_nm5_codex_cli_version": "0.153.4",
    }
    payload.update(extra)
    return payload


def _exec_run(pipe: Pipeline, session: str = "thr-1", *, launcher: bool = False) -> None:
    marks = {"_nm5_launcher": "nm5-codex-exec"} if launcher else {}
    _ingest(pipe, _event("SessionStart", session, source="startup", **marks), f"{session}-start")
    _ingest(
        pipe, _event("UserPromptSubmit", session, turn_id="t1", prompt="x", **marks), f"{session}-p"
    )
    for index, response in enumerate(["FAILED (failures=2)", "OK"]):
        _ingest(
            pipe,
            _event(
                "PreToolUse",
                session,
                turn_id="t1",
                tool_use_id=f"call-{index}",
                tool_name="Bash",
                tool_input={"cmd": "python3 -m unittest"},
                **marks,
            ),
            f"{session}-pre-{index}",
        )
        _ingest(
            pipe,
            _event(
                "PostToolUse",
                session,
                turn_id="t1",
                tool_use_id=f"call-{index}",
                tool_name="Bash",
                tool_response=response,
                **marks,
            ),
            f"{session}-post-{index}",
        )
    _ingest(
        pipe,
        _event(
            "Stop",
            session,
            turn_id="t1",
            stop_hook_active=False,
            last_assistant_message="done",
            **marks,
        ),
        f"{session}-stop",
    )
    _ingest(pipe, _event("SessionEnd", session, reason="other", **marks), f"{session}-end")


def _ingest(pipe: Pipeline, payload: dict[str, Any], delivery: str) -> dict[str, Any]:
    # The real nm5-codex-hook always sends X-NM5-Source: codex.
    return pipe.ingest(payload, delivery, source="codex")


def _calls(client: RecordingClient, suffix: str) -> list[Any]:
    return [payload for path, payload in client.calls if path.endswith(suffix)]


def _events(client: RecordingClient) -> list[dict[str, Any]]:
    return [item for batch in _calls(client, "/events") for item in batch]  # type: ignore[union-attr]


def test_exec_run_is_attributed_and_finalized_completed(
    pipeline: tuple[Pipeline, RecordingClient],
) -> None:
    pipe, client = pipeline
    _exec_run(pipe)
    pipe.flush_once()
    start = _calls(client, "/v1/runs")[0]
    attrs = start["attributes"]
    assert attrs["nm5.source"] == "codex-exec"
    assert attrs["nm5.agent"] == "codex"
    assert attrs["nm5.codex.originator"] == "codex_exec"
    assert attrs["nm5.codex.version"] == "0.153.4"
    assert "nm5.launcher" not in attrs
    finals = _calls(client, "/finalize")
    assert len(finals) == 1
    assert finals[0]["status"] == "completed"
    assert finals[0]["attributes"]["nm5.terminal_evidence"] == "codex.SessionEnd"


def test_failed_test_then_passing_rerun_does_not_fail_run(
    pipeline: tuple[Pipeline, RecordingClient],
) -> None:
    pipe, client = pipeline
    _exec_run(pipe)
    pipe.flush_once()
    completes = [e for e in _events(client) if e.get("event_type") == "span_complete"]
    assert len(completes) == 2
    # Codex exposes no structured exit status: tool spans never claim pass/fail.
    assert all(e["status"] == "ok" for e in completes)
    assert all(e["attributes"]["nm5.tool_outcome"] == "unreported" for e in completes)
    assert "FAILED" not in repr(client.calls)  # tool output is never forwarded
    assert _calls(client, "/finalize")[0]["status"] == "completed"


def test_interactive_stop_completes_turn_but_not_session(
    pipeline: tuple[Pipeline, RecordingClient],
) -> None:
    pipe, client = pipeline
    _ingest(pipe, _event("SessionStart", "tui-1", "codex-tui", source="startup"), "s")
    _ingest(pipe, _event("UserPromptSubmit", "tui-1", "codex-tui", turn_id="t1"), "p1")
    _ingest(pipe, _event("Stop", "tui-1", "codex-tui", turn_id="t1", stop_hook_active=False), "x1")
    _ingest(pipe, _event("UserPromptSubmit", "tui-1", "codex-tui", turn_id="t2"), "p2")
    _ingest(pipe, _event("Stop", "tui-1", "codex-tui", turn_id="t2", stop_hook_active=False), "x2")
    pipe.flush_once()
    assert _calls(client, "/v1/runs")[0]["attributes"]["nm5.source"] == "codex-cli"
    assert _calls(client, "/finalize") == []
    stops = [e for e in _events(client) if e.get("progress") == "Stop"]
    assert [s["attributes"]["nm5.turn_id"] for s in stops] == ["t1", "t2"]
    assert all(s["attributes"]["nm5.turn_status"] == "completed" for s in stops)


def test_interrupt_is_distinct_from_completion(
    pipeline: tuple[Pipeline, RecordingClient],
) -> None:
    pipe, client = pipeline
    _ingest(pipe, _event("SessionStart", "ide-1", "codex_vscode", source="startup"), "s")
    _ingest(pipe, _event("UserPromptSubmit", "ide-1", "codex_vscode", turn_id="t1"), "p")
    _ingest(pipe, _event("Interrupt", "ide-1", "codex_vscode", turn_id="t1"), "i")
    pipe.flush_once()
    assert _calls(client, "/v1/runs")[0]["attributes"]["nm5.source"] == "codex-ide"
    beats = [e for e in _events(client) if e.get("progress") == "Interrupt"]
    assert beats[0]["attributes"]["nm5.turn_status"] == "interrupted"
    assert _calls(client, "/finalize") == []


def test_session_end_error_reason_is_failed(pipeline: tuple[Pipeline, RecordingClient]) -> None:
    pipe, client = pipeline
    _ingest(pipe, _event("SessionStart", source="startup"), "s")
    _ingest(pipe, _event("SessionEnd", reason="error"), "e")
    pipe.flush_once()
    final = _calls(client, "/finalize")[0]
    assert final["status"] == "failed"
    assert final["error"] == {"reason": "error"}


@pytest.mark.parametrize(
    ("exit_payload", "status", "error"),
    [
        ({"exit_code": 0}, "completed", None),
        ({"exit_code": 1}, "failed", {"reason": "nonzero_exit", "exit_code": 1}),
        ({"signal": 2}, "cancelled", {"reason": "signal", "signal": "2"}),
    ],
)
def test_launcher_finalizes_from_exit_status(
    pipeline: tuple[Pipeline, RecordingClient],
    exit_payload: dict[str, Any],
    status: str,
    error: dict[str, Any] | None,
) -> None:
    pipe, client = pipeline
    _exec_run(pipe, launcher=True)
    pipe.flush_once()
    # SessionEnd from a launcher-managed run defers to the exit status.
    assert _calls(client, "/finalize") == []
    result = _ingest(
        pipe,
        {
            "hook_event_name": "CodexExecExit",
            "session_id": "thr-1",
            "_nm5_launcher": "nm5-codex-exec",
            **exit_payload,
        },
        "exit-1",
    )
    assert result["status"] == "accepted"
    pipe.flush_once()
    final = _calls(client, "/finalize")[0]
    assert final["status"] == status
    assert final["error"] == error
    assert final["attributes"]["nm5.terminal_evidence"] == "nm5-codex-exec.exit"
    assert _calls(client, "/v1/runs")[0]["attributes"]["nm5.launcher"] == "nm5-codex-exec"
    again = _ingest(
        pipe, {"hook_event_name": "CodexExecExit", "session_id": "thr-1", **exit_payload}, "exit-2"
    )
    assert again["status"] == "duplicate_occurrence"


def test_unknown_surface_and_late_surface_heartbeat(
    pipeline: tuple[Pipeline, RecordingClient],
) -> None:
    pipe, client = pipeline
    start = _event("SessionStart", "late-1", source="startup")
    start.pop("_nm5_codex_originator")
    _ingest(pipe, start, "s")
    _ingest(pipe, _event("UserPromptSubmit", "late-1", "codex-tui", turn_id="t1"), "p")
    pipe.flush_once()
    assert _calls(client, "/v1/runs")[0]["attributes"]["nm5.source"] == "codex"
    beat = [e for e in _events(client) if e.get("progress") == "UserPromptSubmit"][0]
    assert beat["attributes"]["nm5.codex.last_surface"] == "codex-cli"


def test_unsupported_events_are_ignored(pipeline: tuple[Pipeline, RecordingClient]) -> None:
    pipe, _client = pipeline
    _ingest(pipe, _event("SessionStart", source="startup"), "s")
    result = _ingest(pipe, _event("Notification"), "n")
    assert result["status"] == "ignored"
    plan = CodexAdapter().plan(
        {"hook_event_name": "Notification", "session_id": "x"}, delivery_id="d"
    )
    assert plan.ignored


def test_duplicate_and_out_of_order_events_do_not_duplicate_or_reopen(
    pipeline: tuple[Pipeline, RecordingClient],
) -> None:
    pipe, client = pipeline
    _exec_run(pipe)
    assert _ingest(pipe, _event("SessionEnd", reason="other"), "thr-1-end")["status"] == (
        "duplicate_delivery"
    )
    assert _ingest(pipe, _event("SessionEnd", reason="other"), "late-end")["status"] == (
        "duplicate_occurrence"
    )
    assert _ingest(pipe, _event("SessionStart", source="startup"), "late-start")["status"] == (
        "duplicate_occurrence"
    )
    pipe.flush_once()
    assert len(_calls(client, "/v1/runs")) == 1
    assert len(_calls(client, "/finalize")) == 1


class ConflictClient(RecordingClient):
    def post(self, path: str, payload: object) -> int:
        super().post(path, payload)
        return 409 if path.endswith("/events") else 201


def test_post_finalize_conflicts_are_counted(store: CollectorStore) -> None:
    client = ConflictClient()
    pipe = Pipeline(store, client)
    _ingest(pipe, _event("SessionStart", source="startup"), "s")
    _ingest(pipe, _event("Stop", turn_id="t1"), "late-stop")
    pipe.flush_once()
    outcomes = pipe.diagnostics()["outcomes"]
    assert any(o["status"] == "api_conflict" and o["reason"] == "events_dropped" for o in outcomes)


def test_diagnostics_are_bounded_and_content_free(
    pipeline: tuple[Pipeline, RecordingClient],
) -> None:
    pipe, _client = pipeline
    _exec_run(pipe)
    data = pipe.diagnostics()
    assert "codex-exec" in data["last_accepted"]
    text = repr(data)
    assert "thr-1" not in text and "fixture-ws" not in text
    accepted = [o for o in data["outcomes"] if o["status"] == "accepted"]
    assert accepted and accepted[0]["surface"] == "codex-exec"


# ---- post-reset admission (history guard is unchanged; Codex surfaces must satisfy it) ----


def _guarded(store: CollectorStore, *denied: str) -> Pipeline:
    bits = bytearray(FILTER_BYTES)
    for identity in denied:
        remember(bits, "codex", identity)
    store._set_meta("history_boundary", (datetime.now(UTC) - timedelta(hours=1)).isoformat())
    store._set_meta("history_deny", bytes(bits).hex())
    return Pipeline(store, RecordingClient())


def _fresh(payload: dict[str, Any]) -> dict[str, Any]:
    payload[OBSERVED_AT_FIELD] = datetime.now(UTC).isoformat()
    return payload


@pytest.mark.parametrize("surface", ["codex-tui", "codex_exec", "codex_vscode"])
def test_new_session_on_every_surface_is_admitted(store: CollectorStore, surface: str) -> None:
    pipe = _guarded(store)
    result = _ingest(pipe, _fresh(_event("SessionStart", "new-1", surface, source="startup")), "s")
    assert result["status"] == "accepted"
    assert _ingest(pipe, _event("Stop", "new-1", surface, turn_id="t"), "x")["status"] == "accepted"


def test_resumed_cleared_and_creationless_sessions_stay_rejected(store: CollectorStore) -> None:
    pipe = _guarded(store, "cleared-1")
    resumed = _ingest(pipe, _fresh(_event("SessionStart", "old-1", source="resume")), "r")
    assert resumed == {"status": "rejected_reset_boundary", "reason": "untrusted_session_source"}
    cleared = _ingest(pipe, _fresh(_event("SessionStart", "cleared-1", source="startup")), "c")
    assert cleared["reason"] == "denied_identity"
    continuation = _ingest(pipe, _event("Stop", "old-2", turn_id="t"), "o")
    assert continuation["reason"] == "not_session_start"
    stale = _event("SessionStart", "old-3", source="startup")
    stale[OBSERVED_AT_FIELD] = (datetime.now(UTC) - timedelta(hours=3)).isoformat()
    assert _ingest(pipe, stale, "st")["reason"] == "stale_creation_evidence"
    exit_only = _ingest(
        pipe, {"hook_event_name": "CodexExecExit", "session_id": "old-4", "exit_code": 0}, "ex"
    )
    assert exit_only["reason"] == "not_session_start"
    reasons = {
        o["reason"]
        for o in pipe.diagnostics()["outcomes"]
        if o["status"] == "rejected_reset_boundary"
    }
    assert {
        "untrusted_session_source",
        "denied_identity",
        "not_session_start",
        "stale_creation_evidence",
    } <= reasons
    assert store.get_session(store_session_key("codex", "old-1")) is None
    assert trace_id_for_session("codex", "old-1")  # deterministic ids unchanged
