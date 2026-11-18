from __future__ import annotations

from typing import Any

from nomore500s_collector.adapters.base import MappedEvent, SessionPlan
from nomore500s_collector.adapters.common import (
    as_str,
    coverage_summary,
    finalize_event,
    heartbeat_event,
    iso,
    map_session_outcome,
    occurred_at,
    pick_workspace,
    start_event,
    subagent_span_id,
    tool_span_id,
)
from nomore500s_collector.ids import agent_span_id_for_session, event_id_for, trace_id_for_session
from nomore500s_collector.normalize import canonical_event_name

AGENT = "cursor"

SUPPORTED = {
    "sessionStart",
    "sessionEnd",
    "preToolUse",
    "postToolUse",
    "postToolUseFailure",
    "subagentStart",
    "subagentStop",
    "stop",
    "preCompact",
}


class CursorAdapter:
    name = AGENT

    def inferred_start(
        self,
        session_id: str,
        *,
        workspace: str | None,
        model: str | None,
    ) -> dict[str, Any]:
        return {
            "hook_event_name": "sessionStart",
            "session_id": session_id,
            "conversation_id": session_id,
            "cwd": workspace or "",
            "model": model or "",
            "source": "inferred",
        }

    def plan(self, payload: dict[str, Any], *, delivery_id: str) -> SessionPlan:
        event = canonical_event_name(str(payload.get("hook_event_name") or ""))
        session_id = str(
            payload.get("conversation_id")
            or payload.get("session_id")
            or ""
        )
        occurred = occurred_at(payload)
        workspace = pick_workspace(payload)
        model = as_str(payload, "model_id", "model")
        key = _occurrence_key(event, session_id, payload, delivery_id)
        if not session_id or event not in SUPPORTED:
            return SessionPlan(
                session_id=session_id or "unknown",
                agent=AGENT,
                occurrence_key=key,
                ignored=True,
                coverage_event=event or None,
                occurred_at=occurred,
                workspace=workspace,
                model=model,
            )

        if event == "sessionStart":
            return SessionPlan(
                session_id=session_id,
                agent=AGENT,
                occurrence_key=key,
                coverage_event=event,
                occurred_at=occurred,
                workspace=workspace,
                model=model,
                events=[self._start(session_id, key, occurred, payload, workspace, model)],
            )
        if event == "preToolUse":
            return self._tool_plan(
                session_id, key, occurred, payload, workspace, model, complete=False
            )
        if event in {"postToolUse", "postToolUseFailure"}:
            return self._tool_plan(
                session_id,
                key,
                occurred,
                payload,
                workspace,
                model,
                complete=True,
                failed=event == "postToolUseFailure",
            )
        if event == "subagentStart":
            return self._subagent_plan(
                session_id, key, occurred, payload, workspace, model, complete=False
            )
        if event == "subagentStop":
            return self._subagent_plan(
                session_id, key, occurred, payload, workspace, model, complete=True
            )
        if event in {"stop", "preCompact"}:
            return SessionPlan(
                session_id=session_id,
                agent=AGENT,
                occurrence_key=key,
                coverage_event=event,
                occurred_at=occurred,
                workspace=workspace,
                model=model,
                events=[heartbeat_event(AGENT, session_id, key, occurred, event)],
            )
        if event == "sessionEnd":
            reason = str(payload.get("reason") or "other")
            status, outcome = map_session_outcome(reason)
            return SessionPlan(
                session_id=session_id,
                agent=AGENT,
                occurrence_key=key,
                coverage_event=event,
                occurred_at=occurred,
                workspace=workspace,
                model=model,
                events=[finalize_event(AGENT, session_id, key, occurred, status, reason, outcome)],
            )
        return SessionPlan(
            session_id=session_id,
            agent=AGENT,
            occurrence_key=key,
            ignored=True,
            coverage_event=event,
            occurred_at=occurred,
            workspace=workspace,
            model=model,
        )

    def _start(
        self,
        session_id: str,
        key: str,
        occurred: Any,
        payload: dict[str, Any],
        workspace: str | None,
        model: str | None,
    ) -> MappedEvent:
        attributes: dict[str, Any] = {
            "agent.framework": "cursor",
            "nm5.source": "cursor",
            "nm5.agent": "cursor",
            "nm5.instrumentation": "hooks",
            "nm5.cost_status": "unavailable",
            "nm5.token_status": "unavailable",
            "nm5.coverage": coverage_summary(
                lifecycle=True,
                tools=False,
                model=bool(model),
                tokens=False,
                cost=False,
                errors=False,
                subagents=False,
            ),
            "nm5.session_id": session_id,
        }
        if workspace:
            attributes["nm5.workspace"] = workspace
        if model:
            attributes["gen_ai.request.model"] = model
        if payload.get("is_background_agent") is not None:
            attributes["nm5.background_agent"] = bool(payload.get("is_background_agent"))
        if payload.get("composer_mode"):
            attributes["nm5.composer_mode"] = str(payload.get("composer_mode"))
        name = f"cursor:{workspace}" if workspace else "cursor-session"
        return start_event(
            agent=AGENT,
            session_id=session_id,
            key=key,
            occurred=occurred,
            name=name,
            project_id=workspace or "cursor",
            attributes=attributes,
            agent_name="cursor",
        )

    def _tool_plan(
        self,
        session_id: str,
        key: str,
        occurred: Any,
        payload: dict[str, Any],
        workspace: str | None,
        model: str | None,
        *,
        complete: bool,
        failed: bool = False,
    ) -> SessionPlan:
        identity = str(payload.get("tool_use_id") or key)
        tool_name = str(payload.get("tool_name") or "tool")[:200]
        span_id = tool_span_id(AGENT, session_id, identity)
        interrupt = bool(payload.get("is_interrupt"))
        failure_type = str(payload.get("failure_type") or "")
        if failed:
            status = "ok" if interrupt or failure_type == "permission_denied" else "error"
        else:
            status = "ok"
        body: dict[str, Any] = {
            "event_id": str(event_id_for(key)),
            "event_type": "span_complete" if complete else "span_start",
            "occurred_at": iso(occurred),
            "span_id": str(span_id),
            "parent_span_id": str(agent_span_id_for_session(AGENT, session_id)),
            "type": "tool",
            "name": tool_name,
            "attributes": {
                "tool.name": tool_name,
                "nm5.tool_use_id": identity,
                "nm5.span_source": "hooks",
            },
            "_trace_id": str(trace_id_for_session(AGENT, session_id)),
            "_span_identities": [f"tool:{identity}"],
            "_span_kind": "tool",
        }
        if complete:
            body["status"] = status
            duration = payload.get("duration") or payload.get("duration_ms")
            if isinstance(duration, (int, float)) and duration >= 0:
                body["duration_ms"] = float(duration)
            if failed and payload.get("error_message"):
                body["output"] = {
                    "error": str(payload.get("error_message")),
                    "failure_type": failure_type,
                    "is_interrupt": interrupt,
                }
                if interrupt or failure_type == "permission_denied":
                    body["attributes"]["nm5.outcome"] = "cancelled"
        return SessionPlan(
            session_id=session_id,
            agent=AGENT,
            occurrence_key=key,
            coverage_event=str(payload.get("hook_event_name") or "tool"),
            occurred_at=occurred,
            workspace=workspace,
            model=model,
            events=[MappedEvent(event_id=event_id_for(key), kind="events", payload=body)],
        )

    def _subagent_plan(
        self,
        session_id: str,
        key: str,
        occurred: Any,
        payload: dict[str, Any],
        workspace: str | None,
        model: str | None,
        *,
        complete: bool,
    ) -> SessionPlan:
        identity = str(payload.get("subagent_id") or payload.get("tool_call_id") or key)
        name = str(payload.get("subagent_type") or "subagent")[:200]
        parent = str(payload.get("parent_conversation_id") or session_id)
        body: dict[str, Any] = {
            "event_id": str(event_id_for(key)),
            "event_type": "span_complete" if complete else "span_start",
            "occurred_at": iso(occurred),
            "span_id": str(subagent_span_id(AGENT, session_id, identity)),
            "parent_span_id": str(agent_span_id_for_session(AGENT, parent)),
            "type": "agent",
            "name": name,
            "attributes": {"nm5.subagent_id": identity, "nm5.span_source": "hooks"},
            "_trace_id": str(trace_id_for_session(AGENT, session_id)),
            "_span_identities": [f"subagent:{identity}"],
            "_span_kind": "agent",
        }
        if complete:
            raw = str(payload.get("status") or "completed")
            if raw in {"error", "failed"}:
                body["status"] = "error"
            elif raw in {"aborted", "cancelled", "canceled"}:
                body["status"] = "ok"
                body["attributes"]["nm5.outcome"] = "cancelled"
            else:
                body["status"] = "ok"
            duration = payload.get("duration_ms")
            if isinstance(duration, (int, float)) and duration >= 0:
                body["duration_ms"] = float(duration)
        return SessionPlan(
            session_id=session_id,
            agent=AGENT,
            occurrence_key=key,
            coverage_event="subagent",
            occurred_at=occurred,
            workspace=workspace,
            model=model,
            events=[MappedEvent(event_id=event_id_for(key), kind="events", payload=body)],
        )


def _occurrence_key(event: str, session_id: str, payload: dict[str, Any], delivery_id: str) -> str:
    if event == "sessionStart":
        return f"cursor:{session_id}:SessionStart"
    if event == "sessionEnd":
        return f"cursor:{session_id}:SessionEnd"
    if event in {"preToolUse", "postToolUse", "postToolUseFailure"}:
        tool_id = payload.get("tool_use_id")
        if tool_id:
            return f"cursor:{session_id}:{event}:{tool_id}"
    if event in {"subagentStart", "subagentStop"}:
        sub_id = payload.get("subagent_id") or payload.get("tool_call_id")
        if sub_id:
            return f"cursor:{session_id}:{event}:{sub_id}"
    return f"cursor:delivery:{delivery_id}"
