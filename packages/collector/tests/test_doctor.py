from __future__ import annotations

import json
from pathlib import Path

import pytest
from nomore500s_collector import doctor
from nomore500s_collector.install import install_hooks


@pytest.fixture(autouse=True)
def offline(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(doctor, "check_binaries", lambda report: None)
    monkeypatch.setattr(doctor, "_http_json", lambda url, token=None: (None, None))


def _by_name(report: doctor.Report, name: str) -> doctor.Check:
    return next(c for c in report.configured if c.name == name)


def test_missing_hooks_is_not_ready(tmp_path: Path) -> None:
    report = doctor.run_doctor(codex_home=tmp_path)
    assert _by_name(report, "hooks.json").ok is False
    assert not report.configured_ready


def test_relative_command_is_flagged(tmp_path: Path) -> None:
    install_hooks(codex_home=tmp_path, command="nm5-codex-hook")
    report = doctor.run_doctor(codex_home=tmp_path)
    assert _by_name(report, "nm5 handlers registered").ok is True
    command = _by_name(report, "hook command")
    assert command.ok is False and "relative" in command.detail


def test_non_executable_wrapper_is_flagged(tmp_path: Path) -> None:
    install_hooks(codex_home=tmp_path, python_executable=Path("/opt/missing/python"))
    (tmp_path / "hooks" / "nm5-codex-hook").chmod(0o644)
    report = doctor.run_doctor(codex_home=tmp_path)
    assert _by_name(report, "hook command").ok is False


def test_untrusted_versus_trust_entries_present(tmp_path: Path) -> None:
    install_hooks(codex_home=tmp_path, python_executable=Path("/opt/missing/python"))
    report = doctor.run_doctor(codex_home=tmp_path)
    assert _by_name(report, "hook trust").ok is False
    assert _by_name(report, "hook interpreter").ok is False
    document = json.loads((tmp_path / "hooks.json").read_text(encoding="utf-8"))
    hooks_file = tmp_path / "hooks.json"
    lines = ["[hooks.state]"]
    for event, groups in document["hooks"].items():
        snake = doctor._snake(event)
        lines.append(f'[hooks.state."{hooks_file}:{snake}:{len(groups) - 1}:0"]')
        lines.append('trusted_hash = "sha256:abc"')
    (tmp_path / "config.toml").write_text("\n".join(lines) + "\n", encoding="utf-8")
    report = doctor.run_doctor(codex_home=tmp_path)
    trust = _by_name(report, "hook trust")
    assert trust.ok is True and "not verifiable" in trust.detail


def test_collector_down_is_not_ready_and_live_unverified(tmp_path: Path) -> None:
    report = doctor.run_doctor(codex_home=tmp_path)
    assert _by_name(report, "collector reachable").ok is False
    assert _by_name(report, "API reachable").ok is False
    assert report.live[0].ok is None


def test_live_surfaces_reported_from_diagnostics() -> None:
    report = doctor.Report()
    doctor.check_live(
        report,
        {
            "last_accepted": {"codex-exec": "2026-11-19T19:33:44+00:00"},
            "outcomes": [
                {
                    "agent": "codex",
                    "surface": "codex-ide",
                    "status": "rejected_reset_boundary",
                    "reason": "not_session_start",
                    "count": 2,
                    "last_at": "x",
                },
            ],
        },
    )
    status = {c.name: c.ok for c in report.live}
    assert status["codex-exec"] is True
    assert status["codex-ide"] is None
    assert any("not_session_start" in c.detail for c in report.live)


def test_snake_case_matches_codex_trust_keys() -> None:
    assert doctor._snake("PreToolUse") == "pre_tool_use"
    assert doctor._snake("UserPromptSubmit") == "user_prompt_submit"
    assert doctor._snake("Stop") == "stop"
