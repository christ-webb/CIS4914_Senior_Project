from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from nomore500s_collector.adapters.base import MappedEvent, SessionPlan
from nomore500s_collector.ids import (
    agent_span_id_for_session,
    event_id_for,
    span_id_for,
    trace_id_for_session,
)
from nomore500s_collector.redaction import workspace_name
from nomore500s_collector.tokens import normalize_token_count

SUPPORTED_HOOKS = {
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
    "PreModelRequest",
    "PostModelRequest",
}

STABLE_TOOL_EVENTS = {"PreToolUse", "PostToolUse"}
STABLE_SUBAGENT_EVENTS = {"SubagentStart", "SubagentStop"}
MODEL_EVENTS = {"PreModelRequest", "PostModelRequest"}
HEARTBEAT_EVENTS = {
    "Stop",
    "Interrupt",
    "UserPromptSubmit",
    "PermissionRequest",
    "PreCompact",
    "PostCompact",
}

AGENT = "codex"


def _now() -> datetime:
    return datetime.now(UTC)


def _occurred_at(payload: dict[str, Any]) -> datetime:
    raw = payload.get("occurred_at") or payload.get("timestamp")
    if isinstance(raw, str):
        try:
            value = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            return value.astimezone(UTC) if value.tzinfo else value.replace(tzinfo=UTC)
        except ValueError:
            pass
    return _now()


def _iso(value: datetime) -> str:
    return value.isoformat()


def _str(payload: dict[str, Any], key: str) -> str | None:
    value = payload.get(key)
    return str(value) if value else None


def occurrence_key(payload: dict[str, Any], delivery_id: str) -> str:
    event = str(payload.get("hook_event_name") or "")
    session_id = str(payload.get("session_id") or "")
    if event == "SessionStart":
        return f"codex:{session_id}:SessionStart"
    if event == "SessionEnd":
        return f"codex:{session_id}:SessionEnd"
    if event in STABLE_TOOL_EVENTS:
        tool_use_id = payload.get("tool_use_id")
        if tool_use_id:
            return f"codex:{session_id}:{event}:{tool_use_id}"
    if event in STABLE_SUBAGENT_EVENTS:
        agent_id = payload.get("agent_id")
        if agent_id:
            return f"codex:{session_id}:{event}:{agent_id}"
    if event in MODEL_EVENTS:
        request_id = (
            payload.get("request_id")
            or payload.get("attempt_id")
            or payload.get("model_request_id")
        )
        if request_id:
            return f"codex:{session_id}:{event}:{request_id}"
    return f"codex:delivery:{delivery_id}"


class CodexAdapter:
    name = AGENT

    def plan(self, payload: dict[str, Any], *, delivery_id: str) -> SessionPlan:
        event = str(payload.get("hook_event_name") or "")
        session_id = str(payload.get("session_id") or "")
        occurred = _occurred_at(payload)
        key = occurrence_key(payload, delivery_id)
        workspace = workspace_name(str(payload.get("cwd") or "") or None)
        model = _str(payload, "model")
        if not session_id or event not in SUPPORTED_HOOKS:
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

        if event in HEARTBEAT_EVENTS:
            return SessionPlan(
                session_id=session_id,
                agent=AGENT,
                occurrence_key=key,
                coverage_event=event,
                occurred_at=occurred,
                workspace=workspace,
                model=model,
                events=[self._heartbeat(session_id, key, occurred, event)],
            )

        if event == "SessionStart":
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

        if event == "PreToolUse":
            return SessionPlan(
                session_id=session_id,
                agent=AGENT,
                occurrence_key=key,
                coverage_event=event,
                occurred_at=occurred,
                workspace=workspace,
                model=model,
                events=[self._tool_start(session_id, key, occurred, payload)],
            )

        if event == "PostToolUse":
            return SessionPlan(
                session_id=session_id,
                agent=AGENT,
                occurrence_key=key,
                coverage_event=event,
                occurred_at=occurred,
                workspace=workspace,
                model=model,
                events=[self._tool_complete(session_id, key, occurred, payload)],
            )

        if event == "SubagentStart":
            return SessionPlan(
                session_id=session_id,
                agent=AGENT,
                occurrence_key=key,
                coverage_event=event,
                occurred_at=occurred,
                workspace=workspace,
                model=model,
                events=[self._subagent_start(session_id, key, occurred, payload)],
            )

        if event == "SubagentStop":
            return SessionPlan(
                session_id=session_id,
                agent=AGENT,
                occurrence_key=key,
                coverage_event=event,
                occurred_at=occurred,
                workspace=workspace,
                model=model,
                events=[self._subagent_complete(session_id, key, occurred, payload)],
            )

        if event == "PreModelRequest":
            return SessionPlan(
                session_id=session_id,
                agent=AGENT,
                occurrence_key=key,
                coverage_event=event,
                occurred_at=occurred,
                workspace=workspace,
                model=model,
                events=[self._model_start(session_id, key, occurred, payload, delivery_id)],
            )

        if event == "PostModelRequest":
            count, token_status = normalize_token_count(payload.get("token_usage"))
            return SessionPlan(
                session_id=session_id,
                agent=AGENT,
                occurrence_key=key,
                coverage_event=event,
                occurred_at=occurred,
                workspace=workspace,
                model=model,
                token_status=token_status,
                cost_status="unavailable",
                events=[
                    self._model_complete(
                        session_id, key, occurred, payload, delivery_id, count
                    )
                ],
            )

        if event == "SessionEnd":
            reason = str(payload.get("reason") or "other")
            status = "failed" if reason in {"error", "failed"} else "completed"
            return SessionPlan(
                session_id=session_id,
                agent=AGENT,
                occurrence_key=key,
                coverage_event=event,
                occurred_at=occurred,
                workspace=workspace,
                model=model,
                events=[self._finalize(session_id, key, occurred, status, reason)],
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

    def _trace(self, session_id: str) -> UUID:
        return trace_id_for_session(AGENT, session_id)

    def _agent_span(self, session_id: str) -> UUID:
        return agent_span_id_for_session(AGENT, session_id)

    def _start(
        self,
        session_id: str,
        key: str,
        occurred: datetime,
        payload: dict[str, Any],
        workspace: str | None,
        model: str | None,
    ) -> MappedEvent:
        attributes = {
            "agent.framework": "codex",
            "gen_ai.provider": "openai",
            "nm5.source": "cli",
            "nm5.agent": "codex",
            "nm5.instrumentation": "hooks",
            "nm5.cost_status": "unavailable",
            "nm5.token_status": "unavailable",
            "nm5.coverage": (
                "lifecycle+tools; model unavailable unless Codex >=0.164; "
                "cost unavailable"
            ),
            "nm5.session_id": session_id,
        }
        if workspace:
            attributes["nm5.workspace"] = workspace
        if model:
            attributes["gen_ai.request.model"] = model
        source = payload.get("source")
        if source:
            attributes["nm5.session_source"] = str(source)
        name = f"codex:{workspace}" if workspace else "codex-session"
        return MappedEvent(
            event_id=event_id_for(key),
            kind="start",
            payload={
                "trace_id": str(self._trace(session_id)),
                "project_id": workspace or "codex",
                "name": name[:200],
                "event_id": str(event_id_for(key)),
                "started_at": _iso(occurred),
                "attributes": attributes,
                "agent_name": "codex",
                "agent_span_id": str(self._agent_span(session_id)),
                "agent_input": None,
            },
        )

    def _heartbeat(
        self, session_id: str, key: str, occurred: datetime, progress: str
    ) -> MappedEvent:
        return MappedEvent(
            event_id=event_id_for(key),
            kind="events",
            critical=False,
            payload={
                "event_id": str(event_id_for(key)),
                "event_type": "heartbeat",
                "occurred_at": _iso(occurred),
                "progress": progress,
                "_trace_id": str(self._trace(session_id)),
            },
        )

    def _tool_start(
        self, session_id: str, key: str, occurred: datetime, payload: dict[str, Any]
    ) -> MappedEvent:
        tool_use_id = str(payload.get("tool_use_id") or key)
        tool_name = str(payload.get("tool_name") or "tool")[:200]
        return MappedEvent(
            event_id=event_id_for(key),
            kind="events",
            payload={
                "event_id": str(event_id_for(key)),
                "event_type": "span_start",
                "occurred_at": _iso(occurred),
                "span_id": str(span_id_for("tool", f"{session_id}:{tool_use_id}")),
                "parent_span_id": str(self._agent_span(session_id)),
                "type": "tool",
                "name": tool_name,
                "attributes": {
                    "tool.name": tool_name,
                    "nm5.tool_use_id": tool_use_id,
                },
                "_trace_id": str(self._trace(session_id)),
            },
        )

    def _tool_complete(
        self, session_id: str, key: str, occurred: datetime, payload: dict[str, Any]
    ) -> MappedEvent:
        tool_use_id = str(payload.get("tool_use_id") or key)
        tool_name = str(payload.get("tool_name") or "tool")[:200]
        status = "error" if payload.get("error") else "ok"
        duration = payload.get("duration_ms")
        body: dict[str, Any] = {
            "event_id": str(event_id_for(key)),
            "event_type": "span_complete",
            "occurred_at": _iso(occurred),
            "span_id": str(span_id_for("tool", f"{session_id}:{tool_use_id}")),
            "parent_span_id": str(self._agent_span(session_id)),
            "type": "tool",
            "name": tool_name,
            "status": status,
            "attributes": {"tool.name": tool_name, "nm5.tool_use_id": tool_use_id},
            "_trace_id": str(self._trace(session_id)),
        }
        if isinstance(duration, (int, float)) and duration >= 0:
            body["duration_ms"] = float(duration)
        return MappedEvent(event_id=event_id_for(key), kind="events", payload=body)

    def _subagent_start(
        self, session_id: str, key: str, occurred: datetime, payload: dict[str, Any]
    ) -> MappedEvent:
        agent_id = str(payload.get("agent_id") or key)
        name = str(payload.get("agent_type") or "subagent")[:200]
        return MappedEvent(
            event_id=event_id_for(key),
            kind="events",
            payload={
                "event_id": str(event_id_for(key)),
                "event_type": "span_start",
                "occurred_at": _iso(occurred),
                "span_id": str(span_id_for("subagent", f"{session_id}:{agent_id}")),
                "parent_span_id": str(self._agent_span(session_id)),
                "type": "agent",
                "name": name,
                "attributes": {"nm5.agent_id": agent_id},
                "_trace_id": str(self._trace(session_id)),
            },
        )

    def _subagent_complete(
        self, session_id: str, key: str, occurred: datetime, payload: dict[str, Any]
    ) -> MappedEvent:
        agent_id = str(payload.get("agent_id") or key)
        name = str(payload.get("agent_type") or "subagent")[:200]
        return MappedEvent(
            event_id=event_id_for(key),
            kind="events",
            payload={
                "event_id": str(event_id_for(key)),
                "event_type": "span_complete",
                "occurred_at": _iso(occurred),
                "span_id": str(span_id_for("subagent", f"{session_id}:{agent_id}")),
                "parent_span_id": str(self._agent_span(session_id)),
                "type": "agent",
                "name": name,
                "status": "ok",
                "attributes": {"nm5.agent_id": agent_id},
                "_trace_id": str(self._trace(session_id)),
            },
        )

    def _model_start(
        self,
        session_id: str,
        key: str,
        occurred: datetime,
        payload: dict[str, Any],
        delivery_id: str,
    ) -> MappedEvent:
        identity = str(
            payload.get("request_id")
            or payload.get("attempt_id")
            or payload.get("model_request_id")
            or delivery_id
        )
        model = str(payload.get("model") or "model")[:200]
        return MappedEvent(
            event_id=event_id_for(key),
            kind="events",
            payload={
                "event_id": str(event_id_for(key)),
                "event_type": "span_start",
                "occurred_at": _iso(occurred),
                "span_id": str(span_id_for("llm", f"{session_id}:{identity}")),
                "parent_span_id": str(self._agent_span(session_id)),
                "type": "llm",
                "name": model,
                "attributes": {"gen_ai.request.model": model, "nm5.model_occurrence": identity},
                "_trace_id": str(self._trace(session_id)),
                "_model_pair": identity,
            },
        )

    def _model_complete(
        self,
        session_id: str,
        key: str,
        occurred: datetime,
        payload: dict[str, Any],
        delivery_id: str,
        token_count: int | None,
    ) -> MappedEvent:
        identity = str(
            payload.get("request_id")
            or payload.get("attempt_id")
            or payload.get("model_request_id")
            or delivery_id
        )
        model = str(payload.get("model") or "model")[:200]
        status_raw = str(payload.get("status") or "completed")
        status = "ok" if status_raw in {"completed", "ok", "success"} else "error"
        body: dict[str, Any] = {
            "event_id": str(event_id_for(key)),
            "event_type": "span_complete",
            "occurred_at": _iso(occurred),
            "span_id": str(span_id_for("llm", f"{session_id}:{identity}")),
            "parent_span_id": str(self._agent_span(session_id)),
            "type": "llm",
            "name": model,
            "status": status,
            "attributes": {
                "gen_ai.request.model": model,
                "nm5.cost_status": "unavailable",
                "nm5.token_status": "reported" if token_count is not None else "unavailable",
            },
            "_trace_id": str(self._trace(session_id)),
            "_model_pair": identity,
        }
        if token_count is not None:
            body["token_count"] = token_count
        return MappedEvent(event_id=event_id_for(key), kind="events", payload=body)

    def _finalize(
        self, session_id: str, key: str, occurred: datetime, status: str, reason: str
    ) -> MappedEvent:
        return MappedEvent(
            event_id=event_id_for(key),
            kind="finalize",
            payload={
                "event_id": str(event_id_for(key)),
                "occurred_at": _iso(occurred),
                "status": status,
                "error": None if status == "completed" else {"reason": reason},
                "_trace_id": str(self._trace(session_id)),
            },
        )
