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
# Not present in the installed Codex binaries (CLI 0.153.4, IDE-bundled 0.169.0-alpha);
# kept behind a hidden flag for forward compatibility only.
MODEL_EVENTS = ["PreModelRequest", "PostModelRequest"]
# Lifecycle evidence must not be lost when a one-shot `codex exec` process exits
# before an async handler finishes, so these run synchronously (bounded timeout).
SYNC_EVENTS = frozenset({"SessionStart", "SessionEnd", "Stop", "Interrupt"})
SYNC_TIMEOUT_SECONDS = 3
ASYNC_TIMEOUT_SECONDS = 2
WRAPPER_NAME = "nm5-codex-hook"


def default_codex_home() -> Path:
    env = os.environ.get("CODEX_HOME")
    if env:
        return Path(env).expanduser()
    return Path.home() / ".codex"


def hooks_path(codex_home: Path | None = None) -> Path:
    return (codex_home or default_codex_home()) / "hooks.json"


def wrapper_path(codex_home: Path | None = None) -> Path:
    return (codex_home or default_codex_home()) / "hooks" / WRAPPER_NAME


def write_wrapper(
    codex_home: Path | None = None,
    *,
    python_executable: Path | None = None,
    debug: bool = False,
) -> Path:
    from nomore500s_collector.install_cursor import wrapper_script

    path = wrapper_path(codex_home)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        wrapper_script(
            python_executable, debug=debug, name=WRAPPER_NAME, entrypoint="hook_main"
        ),
        encoding="utf-8",
    )
    path.chmod(0o755)
    return path


def hook_command(python_executable: str | None = None) -> str | None:
    executable = python_executable or os.environ.get("NM5_HOOK_COMMAND")
    return executable or None


def nm5_handler(command: str, *, event: str | None = None) -> dict[str, Any]:
    sync = event in SYNC_EVENTS
    handler: dict[str, Any] = {"type": "command", "command": command}
    if not sync:
        handler["async"] = True
    handler["timeout"] = SYNC_TIMEOUT_SECONDS if sync else ASYNC_TIMEOUT_SECONDS
    handler["statusMessage"] = STATUS_MESSAGE
    return handler


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
    python_executable: Path | None = None,
    debug: bool = False,
) -> Path:
    path = hooks_path(codex_home)
    document = _load(path)
    backup_hooks(path)
    command_value = hook_command(command)
    if command_value is None:
        # Absolute path: Codex (and the IDE extension's app-server) does not
        # inherit the project virtualenv PATH.
        command_value = str(
            write_wrapper(
                codex_home or default_codex_home(),
                python_executable=python_executable,
                debug=debug,
            ).absolute()
        )
    hooks: dict[str, Any] = document.setdefault("hooks", {})
    events = list(CODEX_EVENTS_V153)
    if include_model_hooks:
        events.extend(MODEL_EVENTS)
    for event in events:
        groups = hooks.setdefault(event, [])
        if not isinstance(groups, list):
            groups = []
            hooks[event] = groups
        handler = nm5_handler(command_value, event=event)
        replaced = False
        for group in groups:
            if not isinstance(group, dict):
                continue
            handlers = group.get("hooks")
            if not isinstance(handlers, list):
                continue
            for index, item in enumerate(handlers):
                if isinstance(item, dict) and is_nm5_handler(item):
                    if not replaced:
                        handlers[index] = handler
                        replaced = True
            if replaced:
                group["hooks"] = [
                    item
                    for item in handlers
                    if item is handler or not (isinstance(item, dict) and is_nm5_handler(item))
                ]
                break
        if not replaced:
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
    installed_wrapper = wrapper_path(codex_home)
    if installed_wrapper.exists() and installed_wrapper.name == WRAPPER_NAME:
        installed_wrapper.unlink()
    return path
