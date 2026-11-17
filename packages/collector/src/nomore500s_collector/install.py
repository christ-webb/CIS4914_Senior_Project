from __future__ import annotations

import json
import os
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

HOOK_MARKER = "nm5-codex-hook"
STATUS_MESSAGE = "No More 500s collector"
CODEX_EVENTS_V153 = [
    "SessionStart",
    "SessionEnd",
    "PreToolUse",
    "PostToolUse",
    "PermissionRequest",
    "PreCompact",
    "PostCompact",
    "UserPromptSubmit",
    "SubagentStart",
    "SubagentStop",
    "Stop",
    "Interrupt",
]
MODEL_EVENTS = ["PreModelRequest", "PostModelRequest"]


def default_codex_home() -> Path:
    env = os.environ.get("CODEX_HOME")
    if env:
        return Path(env).expanduser()
    return Path.home() / ".codex"


def hooks_path(codex_home: Path | None = None) -> Path:
    return (codex_home or default_codex_home()) / "hooks.json"


def hook_command(python_executable: str | None = None) -> str:
    executable = python_executable or os.environ.get("NM5_HOOK_COMMAND")
    if executable:
        return executable
    return "nm5-codex-hook"


def nm5_handler(command: str) -> dict[str, Any]:
    return {
        "type": "command",
        "command": command,
        "async": True,
        "timeout": 2,
        "statusMessage": STATUS_MESSAGE,
    }


def is_nm5_handler(handler: dict[str, Any]) -> bool:
    command = str(handler.get("command") or "")
    status = str(handler.get("statusMessage") or "")
    return HOOK_MARKER in command or status == STATUS_MESSAGE


def _load(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"hooks": {}}
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("hooks.json must be a JSON object")
    hooks = raw.get("hooks")
    if hooks is None:
        raw["hooks"] = {}
    elif not isinstance(hooks, dict):
        raise ValueError("hooks.json hooks key must be an object")
    return raw


def _atomic_write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def backup_hooks(path: Path) -> Path | None:
    if not path.exists():
        return None
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    backup = path.with_name(f"{path.name}.nm5.bak.{stamp}")
    shutil.copy2(path, backup)
    return backup


def install_hooks(
    *,
    codex_home: Path | None = None,
    command: str | None = None,
    include_model_hooks: bool = False,
) -> Path:
    path = hooks_path(codex_home)
    document = _load(path)
    backup_hooks(path)
    handler = nm5_handler(hook_command(command))
    hooks: dict[str, Any] = document.setdefault("hooks", {})
    events = list(CODEX_EVENTS_V153)
    if include_model_hooks:
        events.extend(MODEL_EVENTS)
    for event in events:
        groups = hooks.setdefault(event, [])
        if not isinstance(groups, list):
            groups = []
            hooks[event] = groups
        already = False
        for group in groups:
            if not isinstance(group, dict):
                continue
            handlers = group.get("hooks")
            if not isinstance(handlers, list):
                continue
            if any(isinstance(item, dict) and is_nm5_handler(item) for item in handlers):
                already = True
                break
        if already:
            continue
        groups.append({"hooks": [handler]})
    _atomic_write(path, document)
    return path


def uninstall_hooks(*, codex_home: Path | None = None) -> Path:
    path = hooks_path(codex_home)
    document = _load(path)
    backup_hooks(path)
    hooks: dict[str, Any] = document.get("hooks") or {}
    for event, groups in list(hooks.items()):
        if not isinstance(groups, list):
            continue
        kept_groups = []
        for group in groups:
            if not isinstance(group, dict):
                kept_groups.append(group)
                continue
            handlers = group.get("hooks")
            if not isinstance(handlers, list):
                kept_groups.append(group)
                continue
            remaining = [
                item for item in handlers if not (isinstance(item, dict) and is_nm5_handler(item))
            ]
            if remaining:
                next_group = dict(group)
                next_group["hooks"] = remaining
                kept_groups.append(next_group)
        if kept_groups:
            hooks[event] = kept_groups
        else:
            hooks.pop(event, None)
    document["hooks"] = hooks
    _atomic_write(path, document)
    return path
