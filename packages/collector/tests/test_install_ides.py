from __future__ import annotations

import json
from pathlib import Path

from nomore500s_collector.install_cursor import install_cursor_hooks, uninstall_cursor_hooks
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
        json.dumps({"version": 1, "hooks": {"preToolUse": [{"command": "echo other"}]}}),
        encoding="utf-8",
    )
    install_cursor_hooks(cursor_home=home, command="nm5-cursor-hook")
    install_cursor_hooks(cursor_home=home, command="nm5-cursor-hook")
    document = json.loads((home / "hooks.json").read_text(encoding="utf-8"))
    commands = [item["command"] for item in document["hooks"]["preToolUse"]]
    assert commands.count("echo other") == 1
    assert commands.count("nm5-cursor-hook") == 1
    uninstall_cursor_hooks(cursor_home=home)
    document = json.loads((home / "hooks.json").read_text(encoding="utf-8"))
    assert document["hooks"]["preToolUse"] == [{"command": "echo other"}]


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
