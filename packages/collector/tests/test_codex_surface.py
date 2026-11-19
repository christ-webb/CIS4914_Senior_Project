from __future__ import annotations

import json
from pathlib import Path

import pytest
from nomore500s_collector.codex_surface import (
    MAX_HEADER_BYTES,
    ORIGINATOR_FIELD,
    VERSION_FIELD,
    read_session_header,
    stamp_surface,
    surface_for,
)
from nomore500s_collector.hook import _prepare_codex


def _rollout(tmp_path: Path, payload: dict[str, object], extra: str = "") -> Path:
    path = tmp_path / "rollout.jsonl"
    header = {"type": "session_meta", "payload": payload}
    path.write_text(json.dumps(header) + "\n" + extra, encoding="utf-8")
    return path


@pytest.mark.parametrize(
    ("originator", "surface"),
    [
        ("codex-tui", "codex-cli"),
        ("codex_cli_rs", "codex-cli"),
        ("codex_exec", "codex-exec"),
        ("codex_vscode", "codex-ide"),
        ("something_else", "codex"),
    ],
)
def test_surface_mapping(tmp_path: Path, originator: str, surface: str) -> None:
    path = _rollout(tmp_path, {"id": "s", "originator": originator, "cli_version": "0.153.4"})
    payload: dict[str, object] = {"transcript_path": str(path)}
    stamp_surface(payload)
    assert payload[ORIGINATOR_FIELD] == originator
    assert payload[VERSION_FIELD] == "0.153.4"
    assert surface_for(payload) == surface


def test_only_allowlisted_fields_leave_the_header(tmp_path: Path) -> None:
    path = _rollout(
        tmp_path,
        {
            "originator": "codex_exec",
            "cli_version": "0.153.4",
            "instructions": "SECRET INSTRUCTIONS",
            "cwd": "/Users/someone/private",
        },
        extra=json.dumps({"type": "response_item", "payload": {"text": "SECRET PROMPT"}}) + "\n",
    )
    header = read_session_header(str(path))
    assert header == {"originator": "codex_exec", "cli_version": "0.153.4"}
    payload: dict[str, object] = {"transcript_path": str(path)}
    stamp_surface(payload)
    assert "SECRET" not in json.dumps(payload)


def test_missing_and_malformed_files_are_unknown(tmp_path: Path) -> None:
    assert read_session_header(str(tmp_path / "missing.jsonl")) == {}
    assert read_session_header(None) == {}
    bad = tmp_path / "bad.jsonl"
    bad.write_text("not json\n", encoding="utf-8")
    assert read_session_header(str(bad)) == {}
    other = tmp_path / "other.jsonl"
    other.write_text(json.dumps({"type": "event_msg", "payload": {"originator": "x"}}) + "\n")
    assert read_session_header(str(other)) == {}
    assert surface_for({}) == "codex"


def test_oversized_header_uses_bounded_match(tmp_path: Path) -> None:
    path = tmp_path / "big.jsonl"
    filler = "x" * (MAX_HEADER_BYTES + 10)
    path.write_text(
        '{"type":"session_meta","payload":{"originator":"codex_vscode",'
        f'"cli_version":"0.169.0-alpha.11.1","instructions":"{filler}"}}}}\n',
        encoding="utf-8",
    )
    header = read_session_header(str(path))
    assert header["originator"] == "codex_vscode"


def test_unsafe_values_are_rejected(tmp_path: Path) -> None:
    path = _rollout(tmp_path, {"originator": "evil value; rm -rf", "cli_version": 7})
    assert read_session_header(str(path)) == {}


def test_launcher_env_records_session_and_marks_payload(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    launch = tmp_path / "session"
    monkeypatch.setenv("NM5_CODEX_LAUNCHER", "nm5-codex-exec")
    monkeypatch.setenv("NM5_CODEX_LAUNCH_FILE", str(launch))
    start: dict[str, object] = {"hook_event_name": "SessionStart", "session_id": "thr-9"}
    _prepare_codex(start)
    assert start["_nm5_launcher"] == "nm5-codex-exec"
    assert launch.read_text(encoding="utf-8") == "thr-9"
    # A later event never overwrites the recorded session.
    _prepare_codex({"hook_event_name": "SessionStart", "session_id": "other"})
    assert launch.read_text(encoding="utf-8") == "thr-9"


def test_no_launcher_env_means_native(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NM5_CODEX_LAUNCHER", raising=False)
    payload: dict[str, object] = {"hook_event_name": "SessionStart", "session_id": "s"}
    _prepare_codex(payload)
    assert "_nm5_launcher" not in payload
