from __future__ import annotations

from pathlib import Path
from typing import Any

SENSITIVE_KEYS = {
    "tool_input",
    "tool_response",
    "prompt",
    "last_assistant_message",
    "transcript_path",
    "agent_transcript_path",
    "llm_request",
    "llm_response",
}


def workspace_name(cwd: str | None) -> str | None:
    if not cwd:
        return None
    name = Path(cwd).name
    return name or None


def redact_payload(payload: dict[str, Any], *, collect_io: bool = False) -> dict[str, Any]:
    cleaned: dict[str, Any] = {}
    for key, value in payload.items():
        if key in SENSITIVE_KEYS and not collect_io:
            continue
        if key == "cwd" and isinstance(value, str):
            cleaned["cwd"] = workspace_name(value)
            continue
        cleaned[key] = value
    return cleaned
