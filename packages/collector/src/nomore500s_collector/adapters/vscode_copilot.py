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

AGENT = "vscode-copilot"

SUPPORTED = {
    "sessionStart",
    "sessionEnd",
    "preToolUse",
    "postToolUse",
    "postToolUseFailure",
    "subagentStart",
    "subagentStop",
    "agentStop",
    "stop",
    "preCompact",
}


class VSCodeCopilotAdapter:
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
            "cwd": workspace or "",
            "model": model or "",
            "source": "inferred",
        }

    def plan(self, payload: dict[str, Any], *, delivery_id: str) -> SessionPlan:
        event = canonical_event_name(str(payload.get("hook_event_name") or ""))
        session_id = str(payload.get("session_id") or payload.get("sessionId") or "")
        occurred = occurred_at(payload)
        workspace = pick_workspace(payload)
        model = as_str(payload, "model", "gen_ai.request.model")
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
        if event in {"agentStop", "stop", "preCompact"}:
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
        attributes = {
            "agent.framework": "github-copilot",
            "nm5.source": "vscode",
            "nm5.agent": "github-copilot",
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
        provider = as_str(payload, "provider", "gen_ai.provider")
        if provider:
            attributes["gen_ai.provider"] = provider
        name = f"vscode-copilot:{workspace}" if workspace else "vscode-copilot-session"
        return start_event(
            agent=AGENT,
            session_id=session_id,
            key=key,
            occurred=occurred,
            name=name,
            project_id=workspace or "vscode-copilot",
            attributes=attributes,
            agent_name="github-copilot",
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
        body: dict[str, Any] = {
            "event_id": str(event_id_for(key)),
            "event_type": "span_complete" if complete else "span_start",
            "occurred_at": iso(occurred),
            "span_id": str(tool_span_id(AGENT, session_id, identity)),
            "parent_span_id": str(agent_span_id_for_session(AGENT, session_id)),
            "type": "tool",
            "name": tool_name,
            "attributes": {
                "tool.name": tool_name,
                "nm5.tool_use_id": identity,
                "nm5.span_source": "hooks",
            },
            "_trace_id": str(trace_id_for_session(AGENT, session_id)),
            "_span_identities": (
                [f"tool:{identity}"] if payload.get("tool_use_id") else [f"hook:{identity}"]
            ),
            "_span_kind": "tool",
        }
        if complete:
            body["status"] = "error" if failed or payload.get("error") else "ok"
            if payload.get("error"):
                body["output"] = {"error": str(payload.get("error"))}
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
        identity = str(
            payload.get("agentId")
            or payload.get("agent_id")
            or payload.get("subagent_id")
            or key
        )
        name = str(payload.get("agentType") or payload.get("agent_type") or "subagent")[:200]
        body: dict[str, Any] = {
            "event_id": str(event_id_for(key)),
            "event_type": "span_complete" if complete else "span_start",
            "occurred_at": iso(occurred),
            "span_id": str(subagent_span_id(AGENT, session_id, identity)),
            "parent_span_id": str(agent_span_id_for_session(AGENT, session_id)),
            "type": "agent",
            "name": name,
            "attributes": {"nm5.agent_id": identity, "nm5.span_source": "hooks"},
            "_trace_id": str(trace_id_for_session(AGENT, session_id)),
            "_span_identities": [f"subagent:{identity}"],
            "_span_kind": "agent",
        }
        if complete:
            body["status"] = "ok"
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
        return f"vscode-copilot:{session_id}:SessionStart"
    if event == "sessionEnd":
        return f"vscode-copilot:{session_id}:SessionEnd"
    if event in {"preToolUse", "postToolUse", "postToolUseFailure"}:
        tool_id = payload.get("tool_use_id")
        if tool_id:
            return f"vscode-copilot:{session_id}:{event}:{tool_id}"
    if event in {"subagentStart", "subagentStop"}:
        agent_id = payload.get("agentId") or payload.get("agent_id") or payload.get("subagent_id")
        if agent_id:
            return f"vscode-copilot:{session_id}:{event}:{agent_id}"
    return f"vscode-copilot:delivery:{delivery_id}"
