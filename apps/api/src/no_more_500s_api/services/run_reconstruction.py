"""Deterministic reconstruction of live runs from an append-only event log."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from no_more_500s_api.domain.models import (
    RunEventIn,
    RunEventType,
    RunFinalize,
    RunLifecycle,
    RunStart,
    Span,
    SpanStatus,
    SpanType,
    StoredRunEvent,
    Trace,
)

TERMINAL_LIFECYCLES = {RunLifecycle.COMPLETED, RunLifecycle.FAILED, RunLifecycle.CANCELLED}


class InvalidLifecycleError(ValueError):
    """Raised when a new event is incompatible with a terminal run."""


def _ensure_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _parse_datetime(value: Any, fallback: datetime) -> datetime:
    if isinstance(value, datetime):
        return _ensure_utc(value)
    if isinstance(value, str):
        return _ensure_utc(datetime.fromisoformat(value))
    return _ensure_utc(fallback)


def sort_events(events: Sequence[StoredRunEvent]) -> list[StoredRunEvent]:
    unique: dict[UUID, StoredRunEvent] = {}
    for event in events:
        unique.setdefault(event.event_id, event)
    return sorted(
        unique.values(),
        key=lambda event: (
            event.sequence,
            _ensure_utc(event.occurred_at),
            event.event_id,
        ),
    )


def _merge_status(current: SpanStatus, incoming: SpanStatus | None) -> SpanStatus:
    if incoming is None:
        return current
    if current in {SpanStatus.OK, SpanStatus.ERROR} and incoming is SpanStatus.UNSET:
        return current
    return incoming


def _as_uuid(value: Any) -> UUID | None:
    if value is None:
        return None
    return value if isinstance(value, UUID) else UUID(str(value))


def _merge_span(existing: Span | None, incoming: dict[str, Any], *, completing: bool) -> Span:
    span_id = _as_uuid(incoming["span_id"])
    assert span_id is not None
    started_at = incoming.get("started_at") or incoming.get("occurred_at")
    if existing is None:
        span_type = incoming.get("type") or SpanType.WORKFLOW
        name = incoming.get("name") or "span"
        raw_status = incoming.get("status")
        status = SpanStatus(raw_status) if raw_status else (
            SpanStatus.OK if completing else SpanStatus.UNSET
        )
        return Span(
            span_id=span_id,
            parent_span_id=_as_uuid(incoming.get("parent_span_id")),
            type=SpanType(span_type),
            name=name,
            started_at=_parse_datetime(started_at, datetime.now(UTC)),
            duration_ms=float(incoming.get("duration_ms") or 0),
            status=status,
            input=incoming.get("input"),
            output=incoming.get("output"),
            attributes=dict(incoming.get("attributes") or {}),
            token_count=int(incoming.get("token_count") or 0),
            cost_usd=float(incoming.get("cost_usd") or 0),
        )

    updates: dict[str, Any] = {}
    parent = _as_uuid(incoming.get("parent_span_id"))
    if parent and existing.parent_span_id is None:
        updates["parent_span_id"] = parent
    if incoming.get("name"):
        updates["name"] = incoming["name"]
    if incoming.get("type"):
        updates["type"] = SpanType(incoming["type"])
    if incoming.get("input") is not None and existing.input is None:
        updates["input"] = incoming["input"]
    if incoming.get("output") is not None:
        updates["output"] = incoming["output"]
    if incoming.get("attributes"):
        merged = dict(existing.attributes)
        merged.update(incoming["attributes"])
        updates["attributes"] = merged
    incoming_status = incoming.get("status")
    parsed_status = SpanStatus(incoming_status) if incoming_status else None
    if parsed_status is not None:
        updates["status"] = _merge_status(existing.status, parsed_status)
    elif completing:
        updates["status"] = _merge_status(existing.status, SpanStatus.OK)
    if incoming.get("duration_ms") is not None:
        updates["duration_ms"] = max(existing.duration_ms, float(incoming["duration_ms"]))
    token_count = incoming.get("token_count")
    if token_count is not None:
        updates["token_count"] = max(existing.token_count, int(token_count))
    cost_usd = incoming.get("cost_usd")
    if cost_usd is not None:
        updates["cost_usd"] = max(existing.cost_usd, float(cost_usd))
    return existing.model_copy(update=updates)


def reconstruct_trace(
    events: Sequence[StoredRunEvent],
    *,
    now: datetime | None = None,
    inactivity_seconds: float = 120,
) -> Trace:
    if not events:
        raise ValueError("cannot reconstruct an empty event log")

    ordered = sort_events(events)
    now_utc = _ensure_utc(now or datetime.now(UTC))
    spans: dict[UUID, Span] = {}
    project_id = "unknown"
    name = "run"
    started_at = _ensure_utc(ordered[0].occurred_at)
    attributes: dict[str, Any] = {}
    stored_lifecycle = RunLifecycle.RUNNING
    ended_at: datetime | None = None
    last_activity_at = _ensure_utc(ordered[0].received_at)
    trace_id = ordered[0].trace_id
    status = SpanStatus.UNSET
    terminal = False

    for event in ordered:
        payload = event.payload
        last_activity_at = max(last_activity_at, _ensure_utc(event.received_at))
        trace_id = event.trace_id

        if event.event_type is RunEventType.RUN_START:
            project_id = str(payload.get("project_id") or project_id)
            name = str(payload.get("name") or name)
            started_at = _parse_datetime(payload.get("started_at"), event.occurred_at)
            attributes = dict(payload.get("attributes") or {})
            agent_span_id = _as_uuid(payload.get("agent_span_id"))
            if agent_span_id:
                spans[agent_span_id] = _merge_span(
                    None,
                    {
                        "span_id": agent_span_id,
                        "type": SpanType.AGENT,
                        "name": payload.get("agent_name") or name,
                        "started_at": started_at,
                        "input": payload.get("agent_input"),
                        "attributes": payload.get("agent_attributes") or {},
                        "status": SpanStatus.UNSET,
                        "duration_ms": 0,
                    },
                    completing=False,
                )
            continue

        if event.event_type is RunEventType.FINALIZE:
            if terminal:
                continue
            terminal = True
            raw_status = str(payload.get("status") or "")
            if raw_status == "cancelled":
                stored_lifecycle = RunLifecycle.CANCELLED
                status = SpanStatus.UNSET
            elif raw_status == "completed":
                stored_lifecycle = RunLifecycle.COMPLETED
                status = SpanStatus.OK
            else:
                stored_lifecycle = RunLifecycle.FAILED
                status = SpanStatus.ERROR
            ended_at = _ensure_utc(event.occurred_at)
            extra_attrs = payload.get("attributes")
            if isinstance(extra_attrs, dict):
                attributes = {**attributes, **extra_attrs}
            if payload.get("error"):
                attributes = {**attributes, "error": payload["error"]}
            for agent_id, agent in list(spans.items()):
                if agent.type is not SpanType.AGENT:
                    continue
                duration = max(
                    agent.duration_ms,
                    (_ensure_utc(ended_at) - _ensure_utc(agent.started_at)).total_seconds() * 1000,
                )
                spans[agent_id] = agent.model_copy(
                    update={
                        "status": status if agent.status is SpanStatus.UNSET else agent.status,
                        "duration_ms": duration,
                    }
                )
            continue

        if event.event_type is RunEventType.HEARTBEAT:
            if terminal:
                continue
            extra = payload.get("attributes")
            if isinstance(extra, dict):
                attributes = {**attributes, **extra}
            if payload.get("progress"):
                attributes = {**attributes, "progress": payload["progress"]}
            continue

        span_payload = {**payload, "occurred_at": event.occurred_at}
        span_id = _as_uuid(span_payload.get("span_id"))
        if span_id is None:
            continue
        span_payload["span_id"] = span_id
        parent = _as_uuid(span_payload.get("parent_span_id"))
        if parent:
            span_payload["parent_span_id"] = parent

        if event.event_type is RunEventType.SPAN_START:
            if terminal and span_id not in spans:
                continue
            spans[span_id] = _merge_span(spans.get(span_id), span_payload, completing=False)
        elif event.event_type is RunEventType.SPAN_COMPLETE:
            spans[span_id] = _merge_span(spans.get(span_id), span_payload, completing=True)

    ordered_spans = sorted(
        spans.values(),
        key=lambda span: (_ensure_utc(span.started_at), span.span_id),
    )
    duration_ms = _duration_ms(started_at, ended_at, last_activity_at, ordered_spans)
    effective = _effective_lifecycle(
        stored_lifecycle, last_activity_at, now_utc, inactivity_seconds
    )
    return Trace(
        trace_id=trace_id,
        project_id=project_id,
        name=name,
        started_at=started_at,
        status=status if stored_lifecycle in TERMINAL_LIFECYCLES else SpanStatus.UNSET,
        attributes=attributes,
        spans=ordered_spans,
        duration_ms=duration_ms,
        total_tokens=sum(span.token_count for span in ordered_spans),
        total_cost_usd=sum(span.cost_usd for span in ordered_spans),
        lifecycle=effective,
        last_activity_at=last_activity_at,
        ended_at=ended_at,
    )


def _duration_ms(
    started_at: datetime,
    ended_at: datetime | None,
    last_activity_at: datetime,
    spans: list[Span],
) -> float:
    start = _ensure_utc(started_at)
    if ended_at is not None:
        return max(0.0, (_ensure_utc(ended_at) - start).total_seconds() * 1000)
    activity = max(0.0, (_ensure_utc(last_activity_at) - start).total_seconds() * 1000)
    return max(activity, max((span.duration_ms for span in spans), default=0.0))


def _effective_lifecycle(
    stored: RunLifecycle,
    last_activity_at: datetime,
    now: datetime,
    inactivity_seconds: float,
) -> RunLifecycle:
    if stored in TERMINAL_LIFECYCLES or stored is RunLifecycle.UNKNOWN:
        return stored
    idle = (_ensure_utc(now) - _ensure_utc(last_activity_at)).total_seconds()
    if idle > inactivity_seconds:
        return RunLifecycle.INTERRUPTED
    return RunLifecycle.RUNNING


def start_event(start: RunStart, *, received_at: datetime | None = None) -> StoredRunEvent:
    agent_span_id = start.agent_span_id
    if start.agent_name and agent_span_id is None:
        agent_span_id = uuid4()
    payload: dict[str, Any] = {
        "project_id": start.project_id,
        "name": start.name,
        "started_at": start.started_at.isoformat(),
        "attributes": start.attributes,
        "agent_name": start.agent_name,
        "agent_span_id": str(agent_span_id) if agent_span_id else None,
        "agent_input": start.agent_input,
    }
    occurred = _ensure_utc(start.started_at)
    return StoredRunEvent(
        event_id=start.event_id,
        trace_id=start.trace_id,
        event_type=RunEventType.RUN_START,
        sequence=0,
        occurred_at=occurred,
        received_at=_ensure_utc(received_at or datetime.now(UTC)),
        payload=payload,
    )


def inbound_to_stored(
    trace_id: UUID,
    event: RunEventIn,
    *,
    received_at: datetime | None = None,
) -> StoredRunEvent:
    payload = event.model_dump(mode="json")
    return StoredRunEvent(
        event_id=event.event_id,
        trace_id=trace_id,
        event_type=RunEventType(event.event_type),
        sequence=event.sequence,
        occurred_at=_ensure_utc(event.occurred_at),
        received_at=_ensure_utc(received_at or datetime.now(UTC)),
        payload=payload,
    )


def finalize_to_stored(
    trace_id: UUID,
    finalize: RunFinalize,
    *,
    received_at: datetime | None = None,
) -> StoredRunEvent:
    return StoredRunEvent(
        event_id=finalize.event_id,
        trace_id=trace_id,
        event_type=RunEventType.FINALIZE,
        sequence=finalize.sequence,
        occurred_at=_ensure_utc(finalize.occurred_at),
        received_at=_ensure_utc(received_at or datetime.now(UTC)),
        payload={
            "status": finalize.status,
            "error": finalize.error,
            "attributes": finalize.attributes,
        },
    )


def gate_new_event(existing: Sequence[StoredRunEvent], incoming: StoredRunEvent) -> None:
    """Reject genuinely new execution activity on a terminal run.

    Duplicate event_id is allowed (idempotent retry). Delayed span_complete
    and span_start for spans already in the run are reconciled without
    leaving the terminal lifecycle.
    """
    if any(event.event_id == incoming.event_id for event in existing):
        return
    if not existing:
        if incoming.event_type is not RunEventType.RUN_START:
            raise KeyError(str(incoming.trace_id))
        return

    current = reconstruct_trace(existing, inactivity_seconds=10**9)
    terminal = current.lifecycle in TERMINAL_LIFECYCLES
    if not terminal:
        return
    if incoming.event_type is RunEventType.SPAN_COMPLETE:
        return
    if incoming.event_type is RunEventType.SPAN_START:
        span_id = incoming.payload.get("span_id")
        known = {str(span.span_id) for span in current.spans}
        if span_id is not None and str(span_id) in known:
            return
    raise InvalidLifecycleError("Invalid lifecycle transition")
