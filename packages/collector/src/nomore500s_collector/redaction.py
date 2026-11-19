from __future__ import annotations

from pathlib import Path
from typing import Any

SENSITIVE_KEYS = {
    "tool_input",
    "toolArgs",
    "tool_args",
    "tool_output",
    "toolResult",
    "tool_result",
    "tool_response",
    "prompt",
    "initialPrompt",
    "initial_prompt",
    "last_assistant_message",
    "text",
    "transcript_path",
    "agent_transcript_path",
    "llm_request",
    "llm_response",
    "user_email",
    "gen_ai.input.messages",
    "gen_ai.output.messages",
    "gen_ai.tool.call.arguments",
    "gen_ai.tool.call.result",
    "gen_ai.tool.definitions",
    "user.name",
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
        if key in {"cwd", "workspace_roots"} and not collect_io:
            if key == "cwd" and isinstance(value, str):
                cleaned["cwd"] = workspace_name(value)
            elif key == "workspace_roots" and isinstance(value, list) and value:
                cleaned["cwd"] = workspace_name(str(value[0]))
            continue
        if isinstance(value, dict):
            cleaned[key] = redact_payload(value, collect_io=collect_io)
            continue
        cleaned[key] = value
    return cleaned
