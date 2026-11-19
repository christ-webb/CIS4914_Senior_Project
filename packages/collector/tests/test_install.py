from __future__ import annotations

import json
from pathlib import Path

from nomore500s_collector.install import install_hooks, uninstall_hooks


def test_install_merges_and_uninstall_preserves_other_hooks(tmp_path: Path) -> None:
    home = tmp_path / "codex"
    home.mkdir()
    existing = {
        "hooks": {
            "PreToolUse": [
                {
                    "matcher": "Bash",
                    "hooks": [
                        {
                            "type": "command",
                            "command": "echo preexisting",
                            "statusMessage": "mine",
                        }
                    ],
                }
            ]
        }
    }
    (home / "hooks.json").write_text(json.dumps(existing), encoding="utf-8")
    install_hooks(codex_home=home, command="nm5-codex-hook")
    install_hooks(codex_home=home, command="nm5-codex-hook")
    document = json.loads((home / "hooks.json").read_text(encoding="utf-8"))
    pre = document["hooks"]["PreToolUse"]
    commands = [
        handler["command"]
        for group in pre
        for handler in group["hooks"]
    ]
    assert commands.count("echo preexisting") == 1
    assert commands.count("nm5-codex-hook") == 1
    backups = list(home.glob("hooks.json.nm5.bak.*"))
    assert backups
    uninstall_hooks(codex_home=home)
    document = json.loads((home / "hooks.json").read_text(encoding="utf-8"))
    remaining = [
        handler["command"]
        for group in document["hooks"]["PreToolUse"]
        for handler in group["hooks"]
    ]
    assert remaining == ["echo preexisting"]
    assert "SessionStart" not in document["hooks"]


def test_default_install_writes_absolute_executable_wrapper(tmp_path: Path) -> None:
    import os

    from nomore500s_collector.install import SYNC_EVENTS, wrapper_path

    home = tmp_path / "codex"
    install_hooks(codex_home=home, python_executable=Path("/opt/venv/bin/python"))
    wrapper = wrapper_path(home)
    assert wrapper.is_file() and os.access(wrapper, os.X_OK)
    text = wrapper.read_text(encoding="utf-8")
    assert "hook_main" in text and "/opt/venv/bin/python" in text
    document = json.loads((home / "hooks.json").read_text(encoding="utf-8"))
    for event, groups in document["hooks"].items():
        handlers = [h for g in groups for h in g["hooks"]]
        assert len(handlers) == 1
        handler = handlers[0]
        assert Path(handler["command"]).is_absolute()
        assert handler["command"] == str(wrapper)
        if event in SYNC_EVENTS:
            assert "async" not in handler and handler["timeout"] == 3
        else:
            assert handler["async"] is True and handler["timeout"] == 2
    assert "PreModelRequest" not in document["hooks"]


def test_reinstall_replaces_legacy_relative_handler(tmp_path: Path) -> None:
    home = tmp_path / "codex"
    install_hooks(codex_home=home, command="nm5-codex-hook")
    install_hooks(codex_home=home, python_executable=Path("/opt/venv/bin/python"))
    document = json.loads((home / "hooks.json").read_text(encoding="utf-8"))
    commands = [h["command"] for g in document["hooks"]["Stop"] for h in g["hooks"]]
    assert commands == [str(home / "hooks" / "nm5-codex-hook")]
    uninstall_hooks(codex_home=home)
    assert not (home / "hooks" / "nm5-codex-hook").exists()


def test_cursor_wrapper_output_is_unchanged_by_codex_parametrization() -> None:
    from nomore500s_collector.install_cursor import wrapper_script

    text = wrapper_script(Path("/opt/venv/bin/python"))
    assert text.startswith("#!/bin/sh\n# nm5-cursor-hook observe-only wrapper.")
    assert "import cursor_hook_main as main" in text
