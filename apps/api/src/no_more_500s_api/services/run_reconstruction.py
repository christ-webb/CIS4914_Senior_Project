"""Deterministic reconstruction of live runs from an append-only event log."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from no_more_500s_api.domain.models import (
    ExecutionState,
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
from no_more_500s_api.pricing.estimate import annotate_span_estimate, estimate_trace_usage
from no_more_500s_api.services.turns import COMPLETED, FAILED, TurnLedger

CURSOR_TURN_COMPLETE = {"stop", "afterAgentResponse"}
# Cursor documents sessionEnd reasons completed/aborted/error/window_close/user_close.
# Only the two closure reasons are ambiguous about whether the agent had finished.
CURSOR_CLOSE_REASONS = {"user_close", "window_close"}
# Separate hook processes can deliver stop/afterAgentResponse just after sessionEnd.
CURSOR_LATE_TURN_GRACE_SECONDS = 15.0
CURSOR_THOUGHT = "afterAgentThought"
USAGE_EVENTS = {"stop", "afterAgentResponse"}
USAGE_ATTR_KEYS = {
    "nm5.usage_event",
    "nm5.usage_source",
    "nm5.usage_scope",
    "nm5.usage_measured_at",
    "nm5.generation_id",
    "nm5.conversation_id",
    "nm5.token_status",
    "nm5.token_accounting",
    "nm5.token_total_completeness",
    "nm5.billable_breakdown",
    "nm5.usage_conflict",
    "nm5.cost_status",
    "nm5.model_id",
    "nm5.model_variant",
    "gen_ai.usage.input_tokens",
    "gen_ai.usage.output_tokens",
    "gen_ai.usage.cache_read_tokens",
    "gen_ai.usage.cache_write_tokens",
    "gen_ai.usage.uncached_input_tokens",
    "gen_ai.request.model",
}
USAGE_CATEGORY_KEYS = (
    "gen_ai.usage.input_tokens",
    "gen_ai.usage.output_tokens",
    "gen_ai.usage.cache_read_tokens",
    "gen_ai.usage.cache_write_tokens",
)
USAGE_EVENT_RANK = {"afterAgentResponse": 1, "stop": 2}

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
        status = (
            SpanStatus(raw_status)
            if raw_status
            else (SpanStatus.OK if completing else SpanStatus.UNSET)
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
        incoming_attrs = dict(incoming.get("attributes") or {})
        merged = dict(existing.attributes)
        usage_incoming = _usage_event(incoming_attrs)
        for key, value in incoming_attrs.items():
            if usage_incoming and key in USAGE_ATTR_KEYS:
                continue
            merged[key] = value
        updates["attributes"] = merged
    incoming_status = incoming.get("status")
    parsed_status = SpanStatus(incoming_status) if incoming_status else None
    if parsed_status is not None:
        updates["status"] = _merge_status(existing.status, parsed_status)
    elif completing:
        updates["status"] = _merge_status(existing.status, SpanStatus.OK)
    if incoming.get("duration_ms") is not None:
        updates["duration_ms"] = max(existing.duration_ms, float(incoming["duration_ms"]))
    if not _apply_usage_precedence(existing, incoming, updates):
        token_count = incoming.get("token_count")
        if token_count is not None:
            updates["token_count"] = max(existing.token_count, int(token_count))
        cost_usd = incoming.get("cost_usd")
        if cost_usd is not None:
            updates["cost_usd"] = max(existing.cost_usd, float(cost_usd))
    return existing.model_copy(update=updates)


def _usage_event(attributes: dict[str, Any] | None) -> str | None:
    if not isinstance(attributes, dict):
        return None
    event = attributes.get("nm5.usage_event")
    return str(event) if event in USAGE_EVENTS else None


def _usage_reported(attributes: dict[str, Any] | None, token_count: Any) -> bool:
    if isinstance(attributes, dict) and attributes.get("nm5.token_status") == "reported":
        return True
    if isinstance(attributes, dict) and attributes.get("nm5.token_status") in {
        "unavailable",
        "unknown",
    }:
        return False
    return token_count is not None


def _recompute_usage_total(attrs: dict[str, Any], fallback: int | None) -> int | None:
    input_tokens = attrs.get("gen_ai.usage.input_tokens")
    output_tokens = attrs.get("gen_ai.usage.output_tokens")
    cache_read = attrs.get("gen_ai.usage.cache_read_tokens")
    cache_write = attrs.get("gen_ai.usage.cache_write_tokens")
    accounting = str(attrs.get("nm5.token_accounting") or "raw")
    if accounting == "inclusive_cache" and isinstance(input_tokens, int):
        return input_tokens + (output_tokens if isinstance(output_tokens, int) else 0)
    if accounting == "inconsistent":
        parts = [value for value in (input_tokens, output_tokens) if isinstance(value, int)]
        return sum(parts) if parts else fallback
    parts = [
        value
        for value in (input_tokens, output_tokens, cache_read, cache_write)
        if isinstance(value, int)
    ]
    if parts:
        return sum(parts)
    return fallback


def _merge_usage_measurements(
    preferred_attrs: dict[str, Any],
    other_attrs: dict[str, Any],
    preferred_count: Any,
    other_count: Any,
    preferred_event: str,
) -> tuple[dict[str, Any], int | None, bool]:
    merged = dict(preferred_attrs)
    filled = False
    conflict = bool(
        preferred_attrs.get("nm5.usage_conflict") or other_attrs.get("nm5.usage_conflict")
    )
    for key in USAGE_CATEGORY_KEYS:
        preferred = preferred_attrs.get(key)
        other = other_attrs.get(key)
        if preferred is not None and other is not None and preferred != other:
            conflict = True
            merged[key] = preferred
        elif preferred is None and other is not None:
            merged[key] = other
            filled = True
    for key in USAGE_ATTR_KEYS:
        if key in USAGE_CATEGORY_KEYS:
            continue
        if key in {"nm5.token_accounting", "nm5.usage_event", "nm5.usage_conflict"}:
            continue
        if merged.get(key) in (None, "") and other_attrs.get(key) not in (None, ""):
            merged[key] = other_attrs[key]
            filled = True
    if preferred_attrs.get("nm5.model_id") and other_attrs.get("nm5.model_id"):
        if preferred_attrs.get("nm5.model_id") != other_attrs.get("nm5.model_id"):
            conflict = True
    token_count: int | None = int(preferred_count) if preferred_count is not None else None
    if token_count is None and other_count is not None:
        token_count = int(other_count)
        filled = True
    elif (
        token_count is not None
        and other_count is not None
        and int(other_count) != token_count
        and not filled
    ):
        conflict = True
    if filled and not conflict:
        cache_read = merged.get("gen_ai.usage.cache_read_tokens")
        cache_write = merged.get("gen_ai.usage.cache_write_tokens")
        input_tokens = merged.get("gen_ai.usage.input_tokens")
        if isinstance(input_tokens, int) and (cache_read is not None or cache_write is not None):
            cache_total = (cache_read or 0) + (cache_write or 0)
            if input_tokens >= cache_total:
                merged["nm5.token_accounting"] = "inclusive_cache"
                merged["gen_ai.usage.uncached_input_tokens"] = input_tokens - cache_total
                merged["nm5.billable_breakdown"] = (
                    "complete"
                    if merged.get("gen_ai.usage.output_tokens") is not None
                    else "partial"
                )
        token_count = _recompute_usage_total(merged, token_count)
    merged["nm5.usage_event"] = preferred_event
    if conflict:
        merged["nm5.usage_conflict"] = True
        merged["nm5.token_accounting"] = "inconsistent"
        merged["nm5.billable_breakdown"] = "unavailable"
    return merged, token_count, conflict


def _apply_usage_precedence(
    existing: Span,
    incoming: dict[str, Any],
    updates: dict[str, Any],
) -> bool:
    incoming_attrs = dict(incoming.get("attributes") or {})
    incoming_event = _usage_event(incoming_attrs)
    if incoming_event is None:
        return False
    existing_event = _usage_event(existing.attributes)
    incoming_reported = _usage_reported(incoming_attrs, incoming.get("token_count"))
    existing_reported = _usage_reported(existing.attributes, existing.token_count)
    if incoming_event == "stop" and not incoming_reported:
        merged = dict(existing.attributes)
        merged["nm5.usage_event"] = "stop"
        generation_id = incoming_attrs.get("nm5.generation_id")
        if generation_id:
            merged["nm5.generation_id"] = generation_id
        updates["attributes"] = merged
        return True
    if not incoming_reported:
        return True

    incoming_rank = USAGE_EVENT_RANK.get(incoming_event, 0)
    existing_rank = USAGE_EVENT_RANK.get(existing_event or "", 0) if existing_reported else 0
    existing_count = existing.token_count if existing_reported else None
    incoming_count = incoming.get("token_count") if incoming_reported else None
    if incoming_rank >= existing_rank:
        preferred_attrs, other_attrs = incoming_attrs, dict(existing.attributes)
        preferred_count, other_count = incoming_count, existing_count
        preferred_event = incoming_event
    else:
        preferred_attrs, other_attrs = dict(existing.attributes), incoming_attrs
        preferred_count, other_count = existing_count, incoming_count
        preferred_event = existing_event or incoming_event

    usage_attrs, token_count, _conflict = _merge_usage_measurements(
        preferred_attrs,
        other_attrs,
        preferred_count,
        other_count,
        preferred_event,
    )
    merged = dict(existing.attributes)
    merged.update(usage_attrs)
    for key, value in usage_attrs.items():
        merged[key] = value
    updates["attributes"] = merged
    if token_count is not None:
        updates["token_count"] = token_count
    return True


def reconstruct_trace(
    events: Sequence[StoredRunEvent],
    *,
    now: datetime | None = None,
    inactivity_seconds: float = 120,
) -> Trace:
    if not events:
        raise ValueError("cannot reconstruct an empty event log")

    open_work: set[str] = set()
    generic_heartbeat = False
    ordered = sort_events(events)
    now_utc = _ensure_utc(now or datetime.now(UTC))
    spans: dict[UUID, Span] = {}
    project_id = "unknown"
    name = "run"
    started_at = _ensure_utc(ordered[0].occurred_at)
    attributes: dict[str, Any] = {}
    stored_lifecycle = RunLifecycle.RUNNING
    ended_at: datetime | None = None
    last_activity_at = min(_ensure_utc(ordered[0].received_at), _ensure_utc(ordered[0].occurred_at))
    trace_id = ordered[0].trace_id
    status = SpanStatus.UNSET
    terminal = False
    execution_signals: list[tuple[str, str | None]] = []
    ledger = TurnLedger(source="sdk")
    cursor_finalize: StoredRunEvent | None = None

    for event in ordered:
        payload = event.payload
        last_activity_at = max(
            last_activity_at, min(_ensure_utc(event.received_at), _ensure_utc(event.occurred_at))
        )
        trace_id = event.trace_id

        if event.event_type is RunEventType.RUN_START:
            project_id = str(payload.get("project_id") or project_id)
            name = str(payload.get("name") or name)
            started_at = _parse_datetime(payload.get("started_at"), event.occurred_at)
            attributes = dict(payload.get("attributes") or {})
            ledger.source = _turn_source(attributes)
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
            ended_at = _ensure_utc(event.occurred_at)
            if _is_cursor_source(attributes):
                cursor_finalize = event
                continue
            stored_lifecycle, status, attributes = _apply_finalize(
                payload,
                ended_at,
                attributes,
                spans,
                ledger,
                hook=_is_hook_source(attributes),
            )
            continue

        if event.event_type is RunEventType.HEARTBEAT:
            if terminal and not _late_cursor_turn_evidence(cursor_finalize, event):
                continue
            extra = payload.get("attributes")
            if isinstance(extra, dict):
                attributes = {**attributes, **extra}
            generic_heartbeat = True
            progress = payload.get("progress")
            if progress:
                attributes = {**attributes, "progress": progress}
            ledger.observe_heartbeat(
                str(progress) if progress else None,
                extra if isinstance(extra, dict) else None,
                _ensure_utc(event.occurred_at),
            )
            generation_id = None
            if isinstance(extra, dict) and extra.get("nm5.generation_id"):
                generation_id = str(extra["nm5.generation_id"])
            if progress in CURSOR_TURN_COMPLETE | {"Stop", "agentStop"}:
                open_work.clear()
                generic_heartbeat = False
                execution_signals.append(("turn_complete", generation_id))
            elif progress == CURSOR_THOUGHT:
                execution_signals.append(("thought", generation_id))
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
            if _is_execution_work(span_payload):
                open_work.add(str(span_id))
            if terminal and span_id not in spans:
                continue
            spans[span_id] = _merge_span(spans.get(span_id), span_payload, completing=False)
            if _is_execution_work(span_payload):
                execution_signals.append(("work", _generation_from_payload(span_payload)))
                if not terminal:
                    ledger.observe_work(
                        span_payload.get("attributes"), _ensure_utc(event.occurred_at)
                    )
        elif event.event_type is RunEventType.SPAN_COMPLETE:
            open_work.discard(str(span_id))
            generic_heartbeat = False
            spans[span_id] = _merge_span(spans.get(span_id), span_payload, completing=True)
            if _is_execution_work(span_payload):
                execution_signals.append(("work", _generation_from_payload(span_payload)))
                if not terminal:
                    ledger.observe_work(
                        span_payload.get("attributes"), _ensure_utc(event.occurred_at)
                    )

    if cursor_finalize is not None and ended_at is not None:
        stored_lifecycle, status, attributes = _apply_finalize(
            _reconcile_cursor_session_end(cursor_finalize.payload, ledger, execution_signals),
            ended_at,
            attributes,
            spans,
            ledger,
            hook=True,
        )

    ordered_spans = sorted(
        spans.values(),
        key=lambda span: (_ensure_utc(span.started_at), span.span_id),
    )
    duration_ms = _duration_ms(started_at, ended_at, last_activity_at, ordered_spans)
    cursor = _is_cursor_source(attributes)
    execution_state = _execution_state(
        stored_lifecycle,
        execution_signals,
        cursor=cursor,
    )
    hook_source = cursor or attributes.get("nm5.source") in {
        "cli",
        "codex",
        "vscode",
        "vscode-copilot",
    }
    if not cursor and stored_lifecycle not in TERMINAL_LIFECYCLES:
        execution_state = (
            ExecutionState.EXECUTING if open_work or generic_heartbeat else ExecutionState.UNKNOWN
        )
    if (
        hook_source
        and stored_lifecycle not in TERMINAL_LIFECYCLES
        and execution_signals
        and execution_signals[-1][0] == "turn_complete"
    ):
        execution_state = ExecutionState.IDLE
    effective = _effective_lifecycle(
        stored_lifecycle,
        last_activity_at,
        now_utc,
        inactivity_seconds,
        execution_state=execution_state,
        cursor=cursor or (hook_source and execution_state is ExecutionState.IDLE),
    )
    if effective in {RunLifecycle.INTERRUPTED, RunLifecycle.UNKNOWN}:
        execution_state = ExecutionState.UNKNOWN
    if effective is RunLifecycle.INTERRUPTED:
        ledger.mark_stale(last_activity_at)
    attributes.update(ledger.summary_attributes())
    attributes["nm5.last_turn_completed"] = attributes["nm5.latest_turn_status"] == "completed"
    ordered_spans = [annotate_span_estimate(span) for span in ordered_spans]
    attributes = _rollup_usage_attributes(attributes, ordered_spans)
    return Trace(
        trace_id=trace_id,
        project_id=project_id,
        name=name,
        started_at=started_at,
        status=status if stored_lifecycle in TERMINAL_LIFECYCLES else SpanStatus.UNSET,
        attributes=attributes,
        spans=ordered_spans,
        duration_ms=duration_ms,
        total_tokens=_sum_span_tokens(ordered_spans),
        total_cost_usd=_sum_span_costs(ordered_spans),
        lifecycle=effective,
        execution_state=execution_state,
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


def _is_cursor_source(attributes: dict[str, Any]) -> bool:
    source = str(attributes.get("nm5.source") or "")
    framework = str(attributes.get("agent.framework") or attributes.get("nm5.agent") or "")
    return source == "cursor" or framework == "cursor"


def _is_hook_source(attributes: dict[str, Any]) -> bool:
    return _is_cursor_source(attributes) or attributes.get("nm5.source") in {
        "cli",
        "codex",
        "vscode",
        "vscode-copilot",
    }


def _turn_source(attributes: dict[str, Any]) -> str:
    if _is_cursor_source(attributes):
        return "cursor"
    agent = str(attributes.get("nm5.agent") or attributes.get("agent.framework") or "")
    if agent == "codex":
        return "codex"
    source = str(attributes.get("nm5.source") or "")
    return source if source in {"vscode", "vscode-copilot", "cli"} else "sdk"


def _apply_finalize(
    payload: dict[str, Any],
    ended_at: datetime,
    attributes: dict[str, Any],
    spans: dict[UUID, Span],
    ledger: TurnLedger,
    *,
    hook: bool,
) -> tuple[RunLifecycle, SpanStatus, dict[str, Any]]:
    raw_status = str(payload.get("status") or "")
    if raw_status == "cancelled":
        lifecycle, status = RunLifecycle.CANCELLED, SpanStatus.UNSET
    elif raw_status == "completed":
        lifecycle, status = RunLifecycle.COMPLETED, SpanStatus.OK
    else:
        lifecycle, status = RunLifecycle.FAILED, SpanStatus.ERROR
    extra_attrs = payload.get("attributes")
    ledger.observe_finalize(
        raw_status,
        extra_attrs if isinstance(extra_attrs, dict) else None,
        ended_at,
        hook=hook,
    )
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
    return lifecycle, status, attributes


def _cursor_close_reason(payload: dict[str, Any]) -> str | None:
    attrs = payload.get("attributes")
    if payload.get("status") != "cancelled" or not isinstance(attrs, dict):
        return None
    reason = str(attrs.get("nm5.end_reason") or "").strip().lower()
    return reason if reason in CURSOR_CLOSE_REASONS else None


def _late_cursor_turn_evidence(finalize: StoredRunEvent | None, event: StoredRunEvent) -> bool:
    if finalize is None or _cursor_close_reason(finalize.payload) is None:
        return False
    if event.payload.get("progress") not in CURSOR_TURN_COMPLETE:
        return False
    lag = (_ensure_utc(event.occurred_at) - _ensure_utc(finalize.occurred_at)).total_seconds()
    return lag <= CURSOR_LATE_TURN_GRACE_SECONDS


def _reconcile_cursor_session_end(
    payload: dict[str, Any],
    ledger: TurnLedger,
    signals: list[tuple[str, str | None]],
) -> dict[str, Any]:
    """Resolve a Cursor sessionEnd against the full turn history.

    Only closure reasons are reconciled. Explicit provider reasons (completed,
    aborted, error) are kept as reported.
    """
    attrs = dict(payload.get("attributes") or {})
    attrs["nm5.reported_session_status"] = str(payload.get("status") or "")
    reason = _cursor_close_reason(payload)
    if reason is None:
        attrs["nm5.lifecycle_evidence"] = "cursor.sessionEnd"
        return {**payload, "attributes": attrs}

    latest = ledger.latest()
    idle = (
        not ledger.executing()
        and _execution_state(RunLifecycle.RUNNING, signals, cursor=True) is ExecutionState.IDLE
    )
    observed_stop = (
        latest is not None and latest.evidence == "cursor.stop" and latest.provenance == "observed"
    )
    if latest is None:
        evidence = "cursor.close_without_turn_evidence"
    elif observed_stop and latest.status == COMPLETED and idle:
        attrs["nm5.lifecycle_evidence"] = "cursor.close_after_completed_turn"
        attrs["nm5.outcome"] = "completed"
        return {**payload, "status": "completed", "error": None, "attributes": attrs}
    elif observed_stop and latest.status == FAILED and idle:
        attrs["nm5.lifecycle_evidence"] = "cursor.close_after_failed_turn"
        attrs["nm5.outcome"] = "failure"
        error = {"reason": reason, "outcome": "failure", "evidence": "cursor.stop"}
        return {**payload, "status": "failed", "error": error, "attributes": attrs}
    else:
        evidence = "cursor.close_before_turn_completed"
    attrs["nm5.lifecycle_evidence"] = evidence
    return {**payload, "attributes": attrs}


def _generation_from_payload(payload: dict[str, Any]) -> str | None:
    attrs = payload.get("attributes")
    if isinstance(attrs, dict) and attrs.get("nm5.generation_id"):
        return str(attrs["nm5.generation_id"])
    return None


def _is_execution_work(payload: dict[str, Any]) -> bool:
    attrs = payload.get("attributes") if isinstance(payload.get("attributes"), dict) else None
    if _usage_event(attrs):
        return False
    span_type = payload.get("type")
    return span_type in {SpanType.TOOL, SpanType.LLM, "tool", "llm"}


def _execution_state(
    stored: RunLifecycle,
    signals: list[tuple[str, str | None]],
    *,
    cursor: bool,
) -> ExecutionState:
    if stored in TERMINAL_LIFECYCLES:
        return ExecutionState.IDLE
    if not cursor:
        return ExecutionState.UNKNOWN
    state = ExecutionState.IDLE
    completed: set[str] = set()
    for kind, generation_id in signals:
        if kind == "turn_complete":
            if generation_id:
                completed.add(generation_id)
            state = ExecutionState.IDLE
        elif kind == "thought":
            if generation_id and generation_id in completed:
                continue
            state = ExecutionState.EXECUTING
        elif kind == "work":
            if generation_id and generation_id in completed:
                continue
            state = ExecutionState.EXECUTING
    return state


def _status_present(span: Span, key: str) -> str | None:
    value = span.attributes.get(key)
    return str(value) if isinstance(value, str) and value else None


def _include_numeric(span: Span, status_key: str, numeric: float) -> bool:
    status = _status_present(span, status_key)
    if status == "reported":
        return True
    if status:
        return False
    return numeric != 0


def _sum_span_tokens(spans: list[Span]) -> int:
    return sum(
        span.token_count
        for span in spans
        if _include_numeric(span, "nm5.token_status", span.token_count)
    )


def _sum_span_costs(spans: list[Span]) -> float:
    return sum(
        span.cost_usd for span in spans if _include_numeric(span, "nm5.cost_status", span.cost_usd)
    )


def _sum_category(spans: list[Span], key: str) -> int | None:
    values: list[int] = []
    for span in spans:
        if span.attributes.get("nm5.token_status") != "reported":
            continue
        value = span.attributes.get(key)
        if isinstance(value, int):
            values.append(value)
    if not values:
        return None
    return sum(values)


def _rollup_usage_attributes(
    attributes: dict[str, Any],
    spans: list[Span],
) -> dict[str, Any]:
    rolled = dict(attributes)
    if any(span.attributes.get("nm5.token_status") == "reported" for span in spans):
        rolled["nm5.token_status"] = "reported"
        coverage = str(rolled.get("nm5.coverage") or "")
        if coverage:
            rolled["nm5.coverage"] = coverage.replace("tokens=unavailable", "tokens=reported")
    scopes = {
        str(span.attributes.get("nm5.usage_scope"))
        for span in spans
        if span.attributes.get("nm5.usage_scope")
    }
    subagent_tokens = any(
        span.type is SpanType.AGENT
        and span.attributes.get("nm5.subagent_id")
        and span.attributes.get("nm5.token_status") == "reported"
        for span in spans
    )
    if "parent_agent" in scopes and not subagent_tokens:
        rolled["nm5.usage_scope"] = "parent_agent"
    elif subagent_tokens:
        rolled["nm5.usage_scope"] = "full_session"

    reported = [span for span in spans if span.attributes.get("nm5.token_status") == "reported"]
    if reported:
        for source, dest in (
            ("gen_ai.usage.input_tokens", "nm5.usage.input_tokens"),
            ("gen_ai.usage.output_tokens", "nm5.usage.output_tokens"),
            ("gen_ai.usage.cache_read_tokens", "nm5.usage.cache_read_tokens"),
            ("gen_ai.usage.cache_write_tokens", "nm5.usage.cache_write_tokens"),
        ):
            total = _sum_category(reported, source)
            if total is not None:
                rolled[dest] = total
        totals_complete = all(
            span.attributes.get("nm5.token_total_completeness") != "unavailable" or span.token_count
            for span in reported
        )
        billable_complete = all(
            span.attributes.get("nm5.billable_breakdown") == "complete" for span in reported
        )
        rolled["nm5.token_total_completeness"] = "complete" if totals_complete else "partial"
        rolled["nm5.billable_breakdown"] = "complete" if billable_complete else "partial"
        models = {
            str(span.attributes.get("nm5.model_id") or span.attributes.get("gen_ai.request.model"))
            for span in reported
            if span.attributes.get("nm5.model_id") or span.attributes.get("gen_ai.request.model")
        }
        variants = {
            str(span.attributes.get("nm5.model_variant"))
            for span in reported
            if span.attributes.get("nm5.model_variant")
        }
        if len(models) == 1:
            rolled["nm5.model_id"] = next(iter(models))
        if len(variants) == 1:
            rolled["nm5.model_variant"] = next(iter(variants))
        elif reported and not variants:
            rolled.pop("nm5.model_variant", None)
    for key, value in estimate_trace_usage(spans).items():
        if value is not None:
            rolled[key] = value
        elif key in rolled:
            rolled.pop(key, None)
    return rolled


def _effective_lifecycle(
    stored: RunLifecycle,
    last_activity_at: datetime,
    now: datetime,
    inactivity_seconds: float,
    *,
    execution_state: ExecutionState = ExecutionState.UNKNOWN,
    cursor: bool = False,
) -> RunLifecycle:
    if stored in TERMINAL_LIFECYCLES or stored is RunLifecycle.UNKNOWN:
        return stored
    idle = (_ensure_utc(now) - _ensure_utc(last_activity_at)).total_seconds()
    if idle <= inactivity_seconds:
        return RunLifecycle.RUNNING
    if cursor and execution_state is ExecutionState.IDLE:
        return RunLifecycle.RUNNING
    if cursor and execution_state is not ExecutionState.EXECUTING:
        return RunLifecycle.RUNNING
    return RunLifecycle.INTERRUPTED


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
    leaving the terminal lifecycle. Cursor stop/afterAgentResponse arriving
    within the grace window after sessionEnd are accepted as turn evidence.
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
    if incoming.event_type is RunEventType.HEARTBEAT and _is_cursor_source(current.attributes):
        finalize = next(
            (item for item in sort_events(existing) if item.event_type is RunEventType.FINALIZE),
            None,
        )
        if _late_cursor_turn_evidence(finalize, incoming):
            return
    if incoming.event_type is RunEventType.SPAN_COMPLETE:
        span_id = incoming.payload.get("span_id")
        known = {str(span.span_id) for span in current.spans}
        if span_id is not None and str(span_id) in known:
            return
        if _identity_verified_usage(current, incoming):
            return
        raise InvalidLifecycleError("Invalid lifecycle transition")
    if incoming.event_type is RunEventType.SPAN_START:
        span_id = incoming.payload.get("span_id")
        known = {str(span.span_id) for span in current.spans}
        if span_id is not None and str(span_id) in known:
            return
    raise InvalidLifecycleError("Invalid lifecycle transition")


def _identity_verified_usage(current: Trace, incoming: StoredRunEvent) -> bool:
    payload = incoming.payload
    if payload.get("type") not in {SpanType.LLM, "llm"}:
        return False
    attrs = payload.get("attributes") if isinstance(payload.get("attributes"), dict) else {}
    usage_event = _usage_event(attrs)
    generation_id = attrs.get("nm5.generation_id") if isinstance(attrs, dict) else None
    conversation_id = None
    if isinstance(attrs, dict):
        conversation_id = attrs.get("nm5.conversation_id") or attrs.get("nm5.session_id")
    session_id = current.attributes.get("nm5.session_id") or current.attributes.get(
        "nm5.conversation_id"
    )
    if usage_event not in USAGE_EVENTS or not generation_id or not session_id:
        return False
    if conversation_id and str(conversation_id) != str(session_id):
        return False
    return True
