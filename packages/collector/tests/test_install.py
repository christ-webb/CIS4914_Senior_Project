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
