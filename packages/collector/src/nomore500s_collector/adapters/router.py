from __future__ import annotations

from typing import Any

from nomore500s_collector.adapters.base import Adapter
from nomore500s_collector.adapters.codex import CodexAdapter
from nomore500s_collector.adapters.cursor import CursorAdapter
from nomore500s_collector.adapters.otel import OtelAdapter
from nomore500s_collector.adapters.vscode_copilot import VSCodeCopilotAdapter
from nomore500s_collector.normalize import canonical_event_name

_CODEX = CodexAdapter()
_CURSOR = CursorAdapter()
_VSCODE = VSCodeCopilotAdapter()
_OTEL = OtelAdapter()


def select_adapter(payload: dict[str, Any], source: str | None = None) -> Adapter:
    named = (source or str(payload.get("_nm5_source") or "")).strip().lower()
    if named in {"cursor"}:
        return _CURSOR
    if named in {"vscode", "vscode-copilot", "copilot"}:
        return _VSCODE
    if named in {"otel", "otlp"}:
        return _OTEL
    if named in {"codex", "cli"}:
        return _CODEX
    if payload.get("hook_event_name") == "OtelSpan" or payload.get("otel_span_id"):
        return _OTEL
    if payload.get("cursor_version") or payload.get("composer_mode") is not None:
        return _CURSOR
    event = canonical_event_name(str(payload.get("hook_event_name") or ""))
    if event[:1].islower() and event not in {"stop"}:
        if payload.get("sessionId") or payload.get("toolName"):
            return _VSCODE
        if payload.get("conversation_id") or payload.get("tool_use_id"):
            return _CURSOR
    if payload.get("hook_event_name") in {
        "SessionStart",
        "SessionEnd",
        "PreToolUse",
        "PostToolUse",
        "PreModelRequest",
        "PostModelRequest",
    }:
        return _CODEX
    if payload.get("conversation_id") and payload.get("workspace_roots"):
        return _CURSOR
    return _CODEX
