from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from nomore500s_collector.auth import load_or_create_token
from nomore500s_collector.install import _atomic_write, backup_hooks
from nomore500s_collector.jsonc import loads_jsonc
from nomore500s_collector.paths import collector_home
from nomore500s_collector.settings import CollectorSettings

HOOK_MARKER = "nm5-vscode-hook"
HOOK_FILE = "nm5.json"
RESTORE_NAME = "vscode-otel-restore.json"
MANAGED_OTEL_KEYS = (
    "github.copilot.chat.otel.enabled",
    "github.copilot.chat.otel.exporterType",
    "github.copilot.chat.otel.protocol",
    "github.copilot.chat.otel.otlpEndpoint",
    "github.copilot.chat.otel.headers",
)

VSCODE_EVENTS = [
    "sessionStart",
    "sessionEnd",
    "preToolUse",
    "postToolUse",
    "postToolUseFailure",
    "subagentStart",
    "subagentStop",
    "agentStop",
    "preCompact",
]


def default_copilot_home() -> Path:
    env = os.environ.get("COPILOT_HOME")
    if env:
        return Path(env).expanduser()
    return Path.home() / ".copilot"


def vscode_user_settings_path(user_dir: Path | None = None) -> Path:
    if user_dir is not None:
        return user_dir / "settings.json"
    mac = Path.home() / "Library" / "Application Support" / "Code" / "User"
    return mac / "settings.json"


def hook_command(command: str | None = None) -> str:
    return command or os.environ.get("NM5_VSCODE_HOOK_COMMAND") or "nm5-vscode-hook"


def user_hook_path(copilot_home: Path | None = None) -> Path:
    return (copilot_home or default_copilot_home()) / "hooks" / HOOK_FILE


def workspace_hook_path(project: Path) -> Path:
    return project / ".github" / "hooks" / HOOK_FILE


def nm5_document(command: str) -> dict[str, Any]:
    return {
        "version": 1,
        "hooks": {
            event: [{"type": "command", "command": command, "timeoutSec": 2}]
            for event in VSCODE_EVENTS
        },
    }


def install_vscode_hooks(
    *,
    copilot_home: Path | None = None,
    project: Path | None = None,
    command: str | None = None,
) -> Path:
    if project is not None:
        user_path = user_hook_path(copilot_home)
        if user_path.exists():
            raise ValueError(
                f"user-level NM5 Copilot hooks already exist at {user_path}; "
                "refuse duplicate workspace installation"
            )
        path = workspace_hook_path(project)
    else:
        path = user_hook_path(copilot_home)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        backup_hooks(path)
    _atomic_write(path, nm5_document(hook_command(command)))
    return path


def uninstall_vscode_hooks(
    *, copilot_home: Path | None = None, project: Path | None = None
) -> Path:
    path = workspace_hook_path(project) if project is not None else user_hook_path(copilot_home)
    if path.exists():
        backup_hooks(path)
        path.unlink()
    return path


def _load_settings(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    raw = path.read_text(encoding="utf-8")
    document = loads_jsonc(raw)
    if not isinstance(document, dict):
        raise ValueError("VS Code settings.json must be an object")
    return document


def enable_vscode_otel(
    *,
    settings_path: Path,
    collector_url: str,
    token: str,
    path_token: bool = False,
    home: Path | None = None,
) -> Path:
    document = _load_settings(settings_path)
    backup_hooks(settings_path)
    previous = {key: document.get(key) for key in MANAGED_OTEL_KEYS}
    restore_path = (home or collector_home()) / RESTORE_NAME
    restore_path.parent.mkdir(parents=True, exist_ok=True)
    restore_path.write_text(json.dumps(previous, indent=2) + "\n", encoding="utf-8")
    restore_path.chmod(0o600)
    endpoint = collector_url.rstrip("/")
    headers = {"Authorization": f"Bearer {token}"}
    if path_token:
        endpoint = f"{endpoint}/otlp/{token}"
        headers = {}
    document["github.copilot.chat.otel.enabled"] = True
    document["github.copilot.chat.otel.exporterType"] = "otlp-http"
    document["github.copilot.chat.otel.protocol"] = "http/json"
    document["github.copilot.chat.otel.otlpEndpoint"] = endpoint
    if headers:
        document["github.copilot.chat.otel.headers"] = headers
    elif "github.copilot.chat.otel.headers" in document:
        document.pop("github.copilot.chat.otel.headers", None)
    if document.get("github.copilot.chat.otel.captureContent") is not True:
        document["github.copilot.chat.otel.captureContent"] = False
    _atomic_write(settings_path, document)
    return settings_path


def restore_vscode_otel(*, settings_path: Path, home: Path | None = None) -> Path:
    restore_path = (home or collector_home()) / RESTORE_NAME
    document = _load_settings(settings_path)
    backup_hooks(settings_path)
    previous: dict[str, Any] = {}
    if restore_path.exists():
        loaded = json.loads(restore_path.read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            previous = loaded
    for key in MANAGED_OTEL_KEYS:
        if key in previous and previous[key] is not None:
            document[key] = previous[key]
        else:
            document.pop(key, None)
    _atomic_write(settings_path, document)
    if restore_path.exists():
        restore_path.unlink()
    return settings_path


def collector_token_value() -> str:
    settings = CollectorSettings()
    return load_or_create_token(settings.home(), settings.collector_token)
