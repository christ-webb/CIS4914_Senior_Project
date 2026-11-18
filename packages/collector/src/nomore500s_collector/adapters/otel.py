from __future__ import annotations

from typing import Any

from nomore500s_collector.adapters.base import MappedEvent, SessionPlan
from nomore500s_collector.adapters.common import (
    coverage_summary,
    iso,
    llm_span_id,
    occurred_at,
    start_event,
    tool_span_id,
)
from nomore500s_collector.ids import (
    agent_span_id_for_session,
    event_id_for,
    trace_id_for_session,
)
from nomore500s_collector.tokens import normalize_token_count

AGENT = "vscode-copilot"


def _operation(payload: dict[str, Any]) -> str:
    raw = str(payload.get("operation") or payload.get("name") or "").lower()
    if "execute_tool" in raw or raw.endswith("execute_tool"):
        return "execute_tool"
    if raw == "chat" or "operation.name" in raw and "chat" in raw:
        return "chat"
    if "invoke_agent" in raw:
        return "invoke_agent"
    if "execute_hook" in raw:
        return "execute_hook"
    name = str(payload.get("name") or "").lower()
    if name.startswith("chat"):
        return "chat"
    if "tool" in name:
        return "execute_tool"
    if "invoke" in name:
        return "invoke_agent"
    return raw


class OtelAdapter:
    name = AGENT

    def inferred_start(
        self,
        session_id: str,
        *,
        workspace: str | None,
        model: str | None,
    ) -> dict[str, Any]:
        return {
            "hook_event_name": "OtelSpan",
            "operation": "invoke_agent",
            "session_id": session_id,
            "conversation_id": session_id,
            "name": "invoke_agent",
            "cwd": workspace or "",
            "attributes": {"gen_ai.request.model": model} if model else {},
            "source": "inferred",
        }

    def plan(self, payload: dict[str, Any], *, delivery_id: str) -> SessionPlan:
        session_id = str(payload.get("session_id") or payload.get("conversation_id") or "")
        if not session_id and payload.get("otel_trace_id"):
            session_id = f"oteltrace:{payload['otel_trace_id']}"
        operation = _operation(payload)
        occurred = occurred_at(payload)
        attrs = dict(payload.get("attributes") or {})
        workspace = None
        repo = attrs.get("github.copilot.git.repository")
        if isinstance(repo, str) and repo:
            workspace = repo.rstrip("/").split("/")[-1]
        model = str(
            attrs.get("gen_ai.request.model") or attrs.get("gen_ai.response.model") or ""
        ) or None
        otel_span = str(payload.get("otel_span_id") or delivery_id)
        key = f"otel:{session_id}:{otel_span}:{payload.get('_phase') or 'end'}"
        if not session_id or operation == "execute_hook":
            return SessionPlan(
                session_id=session_id or "unknown",
                agent=AGENT,
                occurrence_key=key,
                ignored=True,
                occurred_at=occurred,
                workspace=workspace,
                model=model,
            )
        events: list[MappedEvent] = []
        if operation == "invoke_agent" and (
            payload.get("_phase") == "start" or payload.get("source") == "inferred"
        ):
            events.append(self._start(session_id, key, occurred, attrs, workspace, model))
        elif operation == "execute_tool":
            events.append(self._tool(session_id, key, occurred, payload, attrs, otel_span))
        elif operation == "chat":
            events.append(self._chat(session_id, key, occurred, payload, attrs, otel_span))
        elif operation == "invoke_agent" and payload.get("_phase") != "start":
            events.append(
                MappedEvent(
                    event_id=event_id_for(key),
                    kind="events",
                    critical=False,
                    payload={
                        "event_id": str(event_id_for(key)),
                        "event_type": "heartbeat",
                        "occurred_at": iso(occurred),
                        "progress": "invoke_agent",
                        "_trace_id": str(trace_id_for_session(AGENT, session_id)),
                    },
                )
            )
        else:
            return SessionPlan(
                session_id=session_id,
                agent=AGENT,
                occurrence_key=key,
                ignored=True,
                occurred_at=occurred,
                workspace=workspace,
                model=model,
            )
        return SessionPlan(
            session_id=session_id,
            agent=AGENT,
            occurrence_key=key,
            coverage_event=operation,
            occurred_at=occurred,
            workspace=workspace,
            model=model,
            token_status="reported" if events and events[0].payload.get("token_count") else None,
            events=events,
        )

    def _start(
        self,
        session_id: str,
        key: str,
        occurred: Any,
        attrs: dict[str, Any],
        workspace: str | None,
        model: str | None,
    ) -> MappedEvent:
        attributes = {
            "agent.framework": "github-copilot",
            "nm5.source": "vscode",
            "nm5.agent": "github-copilot",
            "nm5.instrumentation": "otel",
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
        provider = attrs.get("gen_ai.provider.name")
        if provider:
            attributes["gen_ai.provider"] = str(provider)
        name = f"vscode-copilot:{workspace}" if workspace else "vscode-copilot-session"
        return start_event(
            agent=AGENT,
            session_id=session_id,
            key=f"vscode-copilot:{session_id}:SessionStart",
            occurred=occurred,
            name=name,
            project_id=workspace or "vscode-copilot",
            attributes=attributes,
            agent_name="github-copilot",
        )

    def _tool(
        self,
        session_id: str,
        key: str,
        occurred: Any,
        payload: dict[str, Any],
        attrs: dict[str, Any],
        otel_span: str,
    ) -> MappedEvent:
        call_id = str(attrs.get("gen_ai.tool.call.id") or otel_span)
        tool_name = str(attrs.get("gen_ai.tool.name") or payload.get("name") or "tool")[:200]
        phase = payload.get("_phase") or "end"
        identities = [f"tool:{call_id}", f"otel:{otel_span}"]
        body: dict[str, Any] = {
            "event_id": str(event_id_for(key)),
            "event_type": "span_start" if phase == "start" else "span_complete",
            "occurred_at": iso(occurred),
            "span_id": str(tool_span_id(AGENT, session_id, call_id)),
            "parent_span_id": str(agent_span_id_for_session(AGENT, session_id)),
            "type": "tool",
            "name": tool_name,
            "attributes": {
                "tool.name": tool_name,
                "nm5.tool_use_id": call_id,
                "nm5.span_source": "otel",
                "nm5.otel.span_id": otel_span,
                "nm5.otel.trace_id": str(payload.get("otel_trace_id") or ""),
            },
            "_trace_id": str(trace_id_for_session(AGENT, session_id)),
            "_span_identities": identities,
            "_span_kind": "tool",
        }
        if phase != "start":
            failed = str(payload.get("status_code") or "") in {"2", "STATUS_CODE_ERROR", "ERROR"}
            body["status"] = "error" if failed else "ok"
        return MappedEvent(event_id=event_id_for(key), kind="events", payload=body)

    def _chat(
        self,
        session_id: str,
        key: str,
        occurred: Any,
        payload: dict[str, Any],
        attrs: dict[str, Any],
        otel_span: str,
    ) -> MappedEvent:
        identity = str(attrs.get("gen_ai.response.id") or otel_span)
        model = str(
            attrs.get("gen_ai.request.model") or attrs.get("gen_ai.response.model") or "model"
        )[:200]
        phase = payload.get("_phase") or "end"
        usage = {
            "prompt_tokens": attrs.get("gen_ai.usage.input_tokens"),
            "completion_tokens": attrs.get("gen_ai.usage.output_tokens"),
        }
        count, token_status = normalize_token_count(usage)
        body: dict[str, Any] = {
            "event_id": str(event_id_for(key)),
            "event_type": "span_start" if phase == "start" else "span_complete",
            "occurred_at": iso(occurred),
            "span_id": str(llm_span_id(AGENT, session_id, identity)),
            "parent_span_id": str(agent_span_id_for_session(AGENT, session_id)),
            "type": "llm",
            "name": model,
            "attributes": {
                "gen_ai.request.model": model,
                "nm5.span_source": "otel",
                "nm5.otel.span_id": otel_span,
                "nm5.token_status": token_status,
                "nm5.cost_status": "unavailable",
            },
            "_trace_id": str(trace_id_for_session(AGENT, session_id)),
            "_span_identities": [f"llm:{identity}", f"otel:{otel_span}"],
            "_span_kind": "llm",
            "_model_pair": identity,
        }
        if phase != "start":
            failed = str(payload.get("status_code") or "") in {"2", "STATUS_CODE_ERROR", "ERROR"}
            body["status"] = "error" if failed else "ok"
            if count is not None:
                body["token_count"] = count
        return MappedEvent(event_id=event_id_for(key), kind="events", payload=body)
