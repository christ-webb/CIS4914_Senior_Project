from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from nomore500s_collector.install import _atomic_write, backup_hooks

HOOK_MARKER = "nm5-cursor-hook"
CURSOR_EVENTS = [
    "sessionStart",
    "sessionEnd",
    "preToolUse",
    "postToolUse",
    "postToolUseFailure",
    "subagentStart",
    "subagentStop",
    "stop",
    "preCompact",
]


def default_cursor_home() -> Path:
    env = os.environ.get("CURSOR_HOME")
    if env:
        return Path(env).expanduser()
    return Path.home() / ".cursor"


def hooks_path(*, project: Path | None = None, cursor_home: Path | None = None) -> Path:
    if project is not None:
        return project / ".cursor" / "hooks.json"
    return (cursor_home or default_cursor_home()) / "hooks.json"


def hook_command(command: str | None = None) -> str:
    return command or os.environ.get("NM5_CURSOR_HOOK_COMMAND") or "nm5-cursor-hook"


def is_nm5_handler(handler: dict[str, Any]) -> bool:
    command = str(handler.get("command") or "")
    return HOOK_MARKER in command


def _load(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"version": 1, "hooks": {}}
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("hooks.json must be a JSON object")
    raw.setdefault("version", 1)
    hooks = raw.get("hooks")
    if hooks is None:
        raw["hooks"] = {}
    elif not isinstance(hooks, list) and not isinstance(hooks, dict):
        raise ValueError("hooks.json hooks key must be an object or list")
    return raw


def install_cursor_hooks(
    *,
    project: Path | None = None,
    cursor_home: Path | None = None,
    command: str | None = None,
) -> Path:
    path = hooks_path(project=project, cursor_home=cursor_home)
    document = _load(path)
    backup_hooks(path)
    handler = {"command": hook_command(command)}
    hooks = document.setdefault("hooks", {})
    if isinstance(hooks, list):
        raise ValueError("unsupported hooks.json list format")
    for event in CURSOR_EVENTS:
        entries = hooks.setdefault(event, [])
        if not isinstance(entries, list):
            entries = []
            hooks[event] = entries
        if any(isinstance(item, dict) and is_nm5_handler(item) for item in entries):
            continue
        entries.append(handler)
    _atomic_write(path, document)
    return path


def uninstall_cursor_hooks(*, project: Path | None = None, cursor_home: Path | None = None) -> Path:
    path = hooks_path(project=project, cursor_home=cursor_home)
    document = _load(path)
    backup_hooks(path)
    hooks = document.get("hooks") or {}
    if isinstance(hooks, dict):
        for event, entries in list(hooks.items()):
            if not isinstance(entries, list):
                continue
            remaining = [
                item for item in entries if not (isinstance(item, dict) and is_nm5_handler(item))
            ]
            if remaining:
                hooks[event] = remaining
            else:
                hooks.pop(event, None)
        document["hooks"] = hooks
    _atomic_write(path, document)
    return path
