from __future__ import annotations

from typing import Any

_EVENT_ALIASES = {
    "sessionstart": "sessionStart",
    "sessionend": "sessionEnd",
    "pretooluse": "preToolUse",
    "posttooluse": "postToolUse",
    "posttoolusefailure": "postToolUseFailure",
    "subagentstart": "subagentStart",
    "subagentstop": "subagentStop",
    "agentstop": "agentStop",
    "stop": "stop",
    "userpromptsubmitted": "userPromptSubmitted",
    "userpromptsubmit": "userPromptSubmitted",
    "precompact": "preCompact",
    "permissionrequest": "permissionRequest",
    "interrupt": "Interrupt",
    "premodelrequest": "PreModelRequest",
    "postmodelrequest": "PostModelRequest",
}


def canonical_event_name(raw: str) -> str:
    compact = raw.replace("_", "").replace("-", "")
    return _EVENT_ALIASES.get(compact.lower(), raw)


def normalize_hook_payload(payload: dict[str, Any]) -> dict[str, Any]:
    cleaned = dict(payload)
    event = cleaned.get("hook_event_name") or cleaned.get("hookEventName") or ""
    if event:
        cleaned["hook_event_name"] = str(event)
    session = (
        cleaned.get("session_id")
        or cleaned.get("sessionId")
        or cleaned.get("conversation_id")
        or cleaned.get("conversationId")
    )
    if session:
        cleaned["session_id"] = str(session)
        cleaned.setdefault("conversation_id", str(session))
    tool_name = cleaned.get("tool_name") or cleaned.get("toolName")
    if tool_name:
        cleaned["tool_name"] = str(tool_name)
    tool_id = (
        cleaned.get("tool_use_id")
        or cleaned.get("toolUseId")
        or cleaned.get("toolCallId")
        or cleaned.get("tool_call_id")
    )
    if tool_id:
        cleaned["tool_use_id"] = str(tool_id)
    if "toolArgs" in cleaned and "tool_input" not in cleaned:
        cleaned["tool_input"] = cleaned.get("toolArgs")
    if "cwd" not in cleaned and cleaned.get("CWD"):
        cleaned["cwd"] = cleaned["CWD"]
    return cleaned
