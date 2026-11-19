from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from nomore500s_collector.adapters.base import MappedEvent
from nomore500s_collector.ids import (
    agent_span_id_for_session,
    event_id_for,
    span_id_for,
    store_session_key,
    trace_id_for_session,
)
from nomore500s_collector.redaction import workspace_name


def now() -> datetime:
    return datetime.now(UTC)


def occurred_at(payload: dict[str, Any]) -> datetime:
    raw = payload.get("occurred_at") or payload.get("timestamp")
    if isinstance(raw, (int, float)):
        value = float(raw)
        if value > 1e12:
            value /= 1000.0
        return datetime.fromtimestamp(value, tz=UTC)
    if isinstance(raw, str):
        try:
            if raw.isdigit():
                return occurred_at({"timestamp": int(raw)})
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            return parsed.astimezone(UTC) if parsed.tzinfo else parsed.replace(tzinfo=UTC)
        except ValueError:
            pass
    return now()


def iso(value: datetime) -> str:
    return value.isoformat()


def as_str(payload: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = payload.get(key)
        if value is None or value is False:
            continue
        text = str(value).strip()
        if text:
            return text
    return None


def pick_workspace(payload: dict[str, Any]) -> str | None:
    roots = payload.get("workspace_roots")
    if isinstance(roots, list) and roots:
        return workspace_name(str(roots[0]))
    return workspace_name(as_str(payload, "cwd") or None)


def coverage_summary(
    *,
    lifecycle: bool,
    tools: bool,
    model: bool,
    tokens: bool,
    cost: bool,
    errors: bool,
    subagents: bool,
) -> str:
    def flag(name: str, reported: bool) -> str:
        return f"{name}={'reported' if reported else 'unavailable'}"

    return "; ".join(
        [
            flag("lifecycle", lifecycle),
            flag("tools", tools),
            flag("model", model),
            flag("tokens", tokens),
            flag("cost", cost),
            flag("errors", errors),
            flag("subagents", subagents),
        ]
    )


def map_session_outcome(reason: str) -> tuple[str, str]:
    normalized = reason.strip().lower()
    if normalized in {"completed", "complete", "success", "ok"}:
        return "completed", "success"
    if normalized in {"error", "failed", "failure"}:
        return "failed", "failure"
    if normalized in {"timeout"}:
        return "failed", "timeout"
    return "cancelled", "cancelled"


def start_event(
    *,
    agent: str,
    session_id: str,
    key: str,
    occurred: datetime,
    name: str,
    project_id: str,
    attributes: dict[str, Any],
    agent_name: str,
) -> MappedEvent:
    return MappedEvent(
        event_id=event_id_for(key),
        kind="start",
        payload={
            "trace_id": str(trace_id_for_session(agent, session_id)),
            "project_id": project_id[:100],
            "name": name[:200],
            "event_id": str(event_id_for(key)),
            "started_at": iso(occurred),
            "attributes": attributes,
            "agent_name": agent_name,
            "agent_span_id": str(agent_span_id_for_session(agent, session_id)),
            "agent_input": None,
        },
    )


def heartbeat_event(
    agent: str,
    session_id: str,
    key: str,
    occurred: datetime,
    progress: str,
    *,
    attributes: dict[str, Any] | None = None,
    critical: bool = False,
) -> MappedEvent:
    payload: dict[str, Any] = {
        "event_id": str(event_id_for(key)),
        "event_type": "heartbeat",
        "occurred_at": iso(occurred),
        "progress": progress,
        "_trace_id": str(trace_id_for_session(agent, session_id)),
    }
    if attributes:
        payload["attributes"] = attributes
    return MappedEvent(
        event_id=event_id_for(key),
        kind="events",
        critical=critical,
        payload=payload,
    )


def finalize_event(
    agent: str,
    session_id: str,
    key: str,
    occurred: datetime,
    status: str,
    reason: str,
    outcome: str,
) -> MappedEvent:
    error = None if status == "completed" else {"reason": reason, "outcome": outcome}
    return MappedEvent(
        event_id=event_id_for(key),
        kind="finalize",
        payload={
            "event_id": str(event_id_for(key)),
            "occurred_at": iso(occurred),
            "status": status,
            "error": error,
            "attributes": {
                "nm5.end_reason": reason,
                "nm5.outcome": outcome,
            },
            "_trace_id": str(trace_id_for_session(agent, session_id)),
        },
    )


def tool_span_id(agent: str, session_id: str, identity: str) -> UUID:
    return span_id_for("tool", f"{agent}:{session_id}:{identity}")


def subagent_span_id(agent: str, session_id: str, identity: str) -> UUID:
    return span_id_for("subagent", f"{agent}:{session_id}:{identity}")


def llm_span_id(agent: str, session_id: str, identity: str) -> UUID:
    return span_id_for("llm", f"{agent}:{session_id}:{identity}")


def namespaced(agent: str, session_id: str) -> str:
    return store_session_key(agent, session_id)
