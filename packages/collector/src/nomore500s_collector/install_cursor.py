from __future__ import annotations

import json
import os
import shlex
import sys
from pathlib import Path
from typing import Any

from nomore500s_collector.install import _atomic_write, backup_hooks

HOOK_MARKER = "nm5-cursor-hook"
WRAPPER_NAME = "nm5-cursor-hook"
HOOK_TIMEOUT_SECONDS = 5
CURSOR_EVENTS = [
    "sessionStart",
    "sessionEnd",
    "postToolUse",
    "postToolUseFailure",
    "subagentStop",
    "stop",
    "preCompact",
    "afterAgentResponse",
    "afterAgentThought",
]
PERMISSION_EVENTS = (
    "preToolUse",
    "subagentStart",
    "beforeShellExecution",
    "beforeMCPExecution",
    "beforeReadFile",
    "beforeTabFileRead",
)


def default_cursor_home() -> Path:
    env = os.environ.get("CURSOR_HOME")
    if env:
        return Path(env).expanduser()
    return Path.home() / ".cursor"


def hooks_path(*, project: Path | None = None, cursor_home: Path | None = None) -> Path:
    if project is not None:
        return project / ".cursor" / "hooks.json"
    return (cursor_home or default_cursor_home()) / "hooks.json"


def wrapper_path(*, project: Path | None = None, cursor_home: Path | None = None) -> Path:
    if project is not None:
        return project / ".cursor" / "hooks" / WRAPPER_NAME
    return (cursor_home or default_cursor_home()) / "hooks" / WRAPPER_NAME


def wrapper_command(*, project: Path | None = None) -> str:
    if project is not None:
        return ".cursor/hooks/nm5-cursor-hook"
    return "./hooks/nm5-cursor-hook"


def hook_command(command: str | None = None) -> str:
    return command or os.environ.get("NM5_CURSOR_HOOK_COMMAND") or "nm5-cursor-hook"


def resolved_python() -> Path:
    # Keep the venv entrypoint. resolve() follows .venv/bin/python to the
    # base interpreter, which does not have collector packages installed.
    path = Path(sys.executable).expanduser()
    return path if path.is_absolute() else Path.cwd() / path


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


def wrapper_script(python_executable: Path | None = None, *, debug: bool = False) -> str:
    python = shlex.quote(str(python_executable or resolved_python()))
    debug_default = "1" if debug else ""
    return f"""#!/bin/sh
# nm5-cursor-hook observe-only wrapper. Always prints {{}} and exits 0.
NM5_HOOK_DEBUG="${{NM5_HOOK_DEBUG:-{debug_default}}}"
export NM5_HOOK_DEBUG
NM5_COLLECTOR_HOME="${{NM5_COLLECTOR_HOME:-$HOME/.nomore500s/collector}}"
LOG_FILE="$NM5_COLLECTOR_HOME/hook.log"
PYTHON={python}
MAX_LOG_BYTES=262144

_log() {{
  [ -n "$NM5_HOOK_DEBUG" ] || return 0
  mkdir -p "$(dirname "$LOG_FILE")" 2>/dev/null || true
  if [ -f "$LOG_FILE" ]; then
    size=$(wc -c < "$LOG_FILE" 2>/dev/null || echo 0)
    if [ "$size" -gt "$MAX_LOG_BYTES" ]; then
      tail -c $((MAX_LOG_BYTES / 2)) "$LOG_FILE" > "$LOG_FILE.tmp" 2>/dev/null \\
        && mv "$LOG_FILE.tmp" "$LOG_FILE"
    fi
  fi
  printf '%s %s\\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*" >> "$LOG_FILE" 2>/dev/null || true
}}

_log "wrapper_invoked"
if [ ! -x "$PYTHON" ]; then
  _log "interpreter_missing"
  printf '%s\\n' '{{}}'
  exit 0
fi
if ! "$PYTHON" -c 'import nomore500s_collector.cli' </dev/null >/dev/null 2>&1; then
  _log "interpreter_import_failed"
  printf '%s\\n' '{{}}'
  exit 0
fi
_log "interpreter_ok"
exec "$PYTHON" -c \\
  'from nomore500s_collector.cli import cursor_hook_main as main; raise SystemExit(main())'
"""


def write_wrapper(
    path: Path, python_executable: Path | None = None, *, debug: bool = False
) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(wrapper_script(python_executable, debug=debug), encoding="utf-8")
    path.chmod(0o755)
    return path


def _handler(command: str) -> dict[str, Any]:
    return {"command": command, "timeout": HOOK_TIMEOUT_SECONDS}


def _replace_nm5_handlers(entries: list[Any], handler: dict[str, Any]) -> list[Any]:
    replaced = False
    updated: list[Any] = []
    for item in entries:
        if isinstance(item, dict) and is_nm5_handler(item):
            if not replaced:
                updated.append(handler)
                replaced = True
            continue
        updated.append(item)
    if not replaced:
        updated.append(handler)
    return updated


def _strip_nm5_handlers(entries: list[Any]) -> list[Any]:
    return [
        item for item in entries if not (isinstance(item, dict) and is_nm5_handler(item))
    ]


def install_cursor_hooks(
    *,
    project: Path | None = None,
    cursor_home: Path | None = None,
    command: str | None = None,
    python_executable: Path | None = None,
    debug: bool = False,
) -> Path:
    path = hooks_path(project=project, cursor_home=cursor_home)
    document = _load(path)
    backup_hooks(path)
    override = command or os.environ.get("NM5_CURSOR_HOOK_COMMAND")
    if override:
        command_value = override
    else:
        write_wrapper(
            wrapper_path(project=project, cursor_home=cursor_home),
            python_executable=python_executable,
            debug=debug,
        )
        command_value = wrapper_command(project=project)
    handler = _handler(command_value)
    hooks = document.setdefault("hooks", {})
    if isinstance(hooks, list):
        raise ValueError("unsupported hooks.json list format")
    for event in CURSOR_EVENTS:
        entries = hooks.setdefault(event, [])
        if not isinstance(entries, list):
            entries = []
        hooks[event] = _replace_nm5_handlers(entries, handler)
    for event in PERMISSION_EVENTS:
        entries = hooks.get(event)
        if not isinstance(entries, list):
            continue
        remaining = _strip_nm5_handlers(entries)
        if remaining:
            hooks[event] = remaining
        else:
            hooks.pop(event, None)
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
            remaining = _strip_nm5_handlers(entries)
            if remaining:
                hooks[event] = remaining
            else:
                hooks.pop(event, None)
        document["hooks"] = hooks
    _atomic_write(path, document)
    installed_wrapper = wrapper_path(project=project, cursor_home=cursor_home)
    if installed_wrapper.exists() and HOOK_MARKER in installed_wrapper.name:
        installed_wrapper.unlink()
    return path
