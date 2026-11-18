from __future__ import annotations

import json
import shlex
import stat
import subprocess
import sys
from pathlib import Path

from nomore500s_collector.install_cursor import (
    CURSOR_EVENTS,
    PERMISSION_EVENTS,
    install_cursor_hooks,
    uninstall_cursor_hooks,
    wrapper_path,
    wrapper_script,
)
from nomore500s_collector.install_vscode import (
    enable_vscode_otel,
    install_vscode_hooks,
    restore_vscode_otel,
    uninstall_vscode_hooks,
)
from nomore500s_collector.jsonc import loads_jsonc


def test_cursor_install_preserves_other_hooks(tmp_path: Path) -> None:
    home = tmp_path / "cursor"
    home.mkdir()
    (home / "hooks.json").write_text(
        json.dumps({"version": 1, "hooks": {"postToolUse": [{"command": "echo other"}]}}),
        encoding="utf-8",
    )
    install_cursor_hooks(cursor_home=home, command="nm5-cursor-hook")
    install_cursor_hooks(cursor_home=home, command="nm5-cursor-hook")
    document = json.loads((home / "hooks.json").read_text(encoding="utf-8"))
    commands = [item["command"] for item in document["hooks"]["postToolUse"]]
    assert commands.count("echo other") == 1
    assert commands.count("nm5-cursor-hook") == 1
    uninstall_cursor_hooks(cursor_home=home)
    document = json.loads((home / "hooks.json").read_text(encoding="utf-8"))
    assert document["hooks"]["postToolUse"] == [{"command": "echo other"}]


def test_cursor_default_install_writes_user_wrapper(tmp_path: Path) -> None:
    home = tmp_path / "cursor home"
    path = install_cursor_hooks(cursor_home=home)
    document = json.loads(path.read_text(encoding="utf-8"))
    wrapper = wrapper_path(cursor_home=home)
    assert wrapper.exists()
    assert wrapper.stat().st_mode & stat.S_IXUSR
    assert "preToolUse" not in document["hooks"]
    assert "subagentStart" not in document["hooks"]
    for event in CURSOR_EVENTS:
        handlers = document["hooks"][event]
        assert handlers == [{"command": "./hooks/nm5-cursor-hook", "timeout": 5}]
    reinstall = install_cursor_hooks(cursor_home=home)
    again = json.loads(reinstall.read_text(encoding="utf-8"))
    assert again["hooks"]["sessionStart"] == [{"command": "./hooks/nm5-cursor-hook", "timeout": 5}]


def test_cursor_project_install_uses_project_cwd_command(tmp_path: Path) -> None:
    project = tmp_path / "repo with spaces"
    path = install_cursor_hooks(project=project)
    document = json.loads(path.read_text(encoding="utf-8"))
    wrapper = wrapper_path(project=project)
    assert wrapper.exists()
    assert document["hooks"]["sessionStart"][0]["command"] == ".cursor/hooks/nm5-cursor-hook"


def test_cursor_reinstall_repairs_bare_command_and_strips_permission_hooks(
    tmp_path: Path,
) -> None:
    home = tmp_path / "cursor"
    home.mkdir()
    (home / "hooks.json").write_text(
        json.dumps(
            {
                "version": 1,
                "hooks": {
                    "preToolUse": [{"command": "nm5-cursor-hook"}],
                    "sessionStart": [{"command": "nm5-cursor-hook"}],
                    "postToolUse": [{"command": "echo other"}],
                },
            }
        ),
        encoding="utf-8",
    )
    install_cursor_hooks(cursor_home=home)
    document = json.loads((home / "hooks.json").read_text(encoding="utf-8"))
    assert "preToolUse" not in document["hooks"]
    for event in PERMISSION_EVENTS:
        assert event not in document["hooks"]
    assert document["hooks"]["sessionStart"] == [
        {"command": "./hooks/nm5-cursor-hook", "timeout": 5}
    ]
    assert document["hooks"]["postToolUse"][0]["command"] == "echo other"
    assert document["hooks"]["postToolUse"][1]["command"] == "./hooks/nm5-cursor-hook"


def _run_wrapper(
    wrapper_cwd: Path,
    command: str,
    *,
    env: dict[str, str],
    payload: bytes,
) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        command,
        cwd=wrapper_cwd,
        input=payload,
        env=env,
        capture_output=True,
        timeout=15,
        shell=True,
        check=False,
    )


def test_user_wrapper_runs_from_cursor_cwd_with_stripped_path(tmp_path: Path) -> None:
    home = tmp_path / "cursor"
    install_cursor_hooks(cursor_home=home)
    env = {
        "PATH": "/usr/bin:/bin",
        "HOME": str(tmp_path),
        "NM5_COLLECTOR_HOME": str(tmp_path / "collector"),
        "NM5_HOOK_DEBUG": "1",
        "NM5_COLLECTOR_URL": "http://127.0.0.1:1",
    }
    result = _run_wrapper(
        home,
        "./hooks/nm5-cursor-hook",
        env=env,
        payload=b'{"hook_event_name":"sessionStart","session_id":"s1"}',
    )
    assert result.returncode == 0
    assert result.stdout.strip() == b"{}"
    log = (tmp_path / "collector" / "hook.log").read_text(encoding="utf-8")
    assert "wrapper_invoked" in log
    assert "interpreter_ok" in log
    assert "python_hook" in log
    assert "event=sessionStart" in log
    assert "has_session_id=true" in log
    assert "secret" not in log
    assert "tool_input" not in log


def test_project_wrapper_runs_from_project_root_with_stripped_path(tmp_path: Path) -> None:
    project = tmp_path / "nm5-integration-test"
    install_cursor_hooks(project=project)
    env = {
        "PATH": "/usr/bin:/bin",
        "HOME": str(tmp_path),
        "NM5_COLLECTOR_HOME": str(tmp_path / "collector"),
        "NM5_HOOK_DEBUG": "1",
        "NM5_COLLECTOR_URL": "http://127.0.0.1:1",
    }
    result = _run_wrapper(
        project,
        ".cursor/hooks/nm5-cursor-hook",
        env=env,
        payload=b'{"hook_event_name":"postToolUse","conversation_id":"c1"}',
    )
    assert result.returncode == 0
    assert result.stdout.strip() == b"{}"
    log = (tmp_path / "collector" / "hook.log").read_text(encoding="utf-8")
    assert "wrapper_invoked" in log
    assert "interpreter_ok" in log


def test_wrapper_script_quotes_python_path_with_spaces(tmp_path: Path) -> None:
    home = tmp_path / "cursor"
    install_cursor_hooks(cursor_home=home)
    script = (home / "hooks" / "nm5-cursor-hook").read_text(encoding="utf-8")
    python = str(Path(sys.executable).expanduser())
    if " " in python:
        assert f"PYTHON={shlex.quote(python)}" in script
    generated = wrapper_script(Path("/tmp/venv dir/bin/python"))
    assert "PYTHON='/tmp/venv dir/bin/python'" in generated


def test_wrapper_missing_interpreter_stays_observe_only(tmp_path: Path) -> None:
    home = tmp_path / "cursor"
    missing = tmp_path / "missing venv" / "python"
    install_cursor_hooks(cursor_home=home, python_executable=missing)
    env = {
        "PATH": "/usr/bin:/bin",
        "HOME": str(tmp_path),
        "NM5_COLLECTOR_HOME": str(tmp_path / "collector"),
        "NM5_HOOK_DEBUG": "1",
    }
    result = _run_wrapper(
        home,
        "./hooks/nm5-cursor-hook",
        env=env,
        payload=b'{"hook_event_name":"sessionStart","session_id":"s1"}',
    )
    assert result.returncode == 0
    assert result.stdout.strip() == b"{}"
    log = (tmp_path / "collector" / "hook.log").read_text(encoding="utf-8")
    assert "wrapper_invoked" in log
    assert "interpreter_missing" in log


def test_hook_debug_is_off_by_default(tmp_path: Path) -> None:
    home = tmp_path / "cursor"
    install_cursor_hooks(cursor_home=home)
    env = {
        "PATH": "/usr/bin:/bin",
        "HOME": str(tmp_path),
        "NM5_COLLECTOR_HOME": str(tmp_path / "collector"),
        "NM5_COLLECTOR_URL": "http://127.0.0.1:1",
    }
    result = _run_wrapper(
        home,
        "./hooks/nm5-cursor-hook",
        env=env,
        payload=b'{"hook_event_name":"sessionStart","session_id":"s1"}',
    )
    assert result.returncode == 0
    assert not (tmp_path / "collector" / "hook.log").exists()


def test_vscode_hooks_and_jsonc_otel_restore(tmp_path: Path) -> None:
    copilot = tmp_path / "copilot"
    path = install_vscode_hooks(copilot_home=copilot, command="nm5-vscode-hook")
    assert path.exists()
    document = json.loads(path.read_text(encoding="utf-8"))
    assert "sessionStart" in document["hooks"]
    uninstall_vscode_hooks(copilot_home=copilot)
    assert not path.exists()

    settings = tmp_path / "User" / "settings.json"
    settings.parent.mkdir()
    settings.write_text(
        (
            '{\n  // keep me\n  "editor.fontSize": 14,\n'
            '  "github.copilot.chat.otel.enabled": false\n}\n'
        ),
        encoding="utf-8",
    )
    enable_vscode_otel(
        settings_path=settings,
        collector_url="http://127.0.0.1:8788",
        token="secret",
        home=tmp_path / "nm5",
    )
    merged = json.loads(settings.read_text(encoding="utf-8"))
    assert merged["editor.fontSize"] == 14
    assert merged["github.copilot.chat.otel.enabled"] is True
    assert merged["github.copilot.chat.otel.headers"]["Authorization"] == "Bearer secret"
    assert "secret" not in merged["github.copilot.chat.otel.otlpEndpoint"]
    restore_vscode_otel(settings_path=settings, home=tmp_path / "nm5")
    restored = json.loads(settings.read_text(encoding="utf-8"))
    assert restored["github.copilot.chat.otel.enabled"] is False
    assert restored["editor.fontSize"] == 14


def test_jsonc_parser() -> None:
    document = loads_jsonc('{ "a": 1, /* x */ "b": 2, }\n')
    assert document == {"a": 1, "b": 2}
