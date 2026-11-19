from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from threading import Lock
from typing import Literal, Protocol
from uuid import UUID

from no_more_500s_api.domain.models import (
    RunEventIn,
    RunFinalize,
    RunLifecycle,
    RunStart,
    SpanStatus,
    StoredRunEvent,
    Trace,
    TraceCreate,
    TraceSummary,
)
from no_more_500s_api.services.run_reconstruction import (
    finalize_to_stored,
    gate_new_event,
    inbound_to_stored,
    reconstruct_trace,
    start_event,
)

type EventLog = dict[UUID, list[StoredRunEvent]]
type TraceMap = dict[UUID, Trace]


class TraceNotFoundError(KeyError):
    pass


class TraceConflictError(ValueError):
    """Raised when a trace_id already exists. Not a storage outage."""


class TraceStorageError(RuntimeError):
    """Raised when the backing store cannot complete a read or write."""


@dataclass(frozen=True)
class TraceListQuery:
    fixtures: Literal["exclude", "only", "include"] = "include"
    activity: Literal["active", "idle", "terminal", "completed_turns"] | None = None
    project_id: str | None = None
    status: SpanStatus | None = None
    lifecycle: RunLifecycle | None = None
    source: str | None = None
    provider: str | None = None
    agent_framework: str | None = None
    started_after: datetime | None = None
    started_before: datetime | None = None
    limit: int | None = None
    offset: int = 0


class TraceRepository(Protocol):
    def save(self, trace_create: TraceCreate) -> Trace: ...

    def get(self, trace_id: UUID) -> Trace: ...

    def list(self, query: TraceListQuery | None = None) -> Iterable[TraceSummary]: ...

    def list_projects(self) -> Iterable[str]: ...

    def start_run(self, start: RunStart) -> Trace: ...

    def append_events(self, trace_id: UUID, events: Sequence[RunEventIn]) -> Trace: ...

    def finalize_run(self, trace_id: UUID, finalize: RunFinalize) -> Trace: ...


def utc_datetime(value: datetime) -> datetime:
    """Treat naive values as UTC; convert aware values to UTC."""
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def clickhouse_bind_datetime(value: datetime) -> datetime:
    """UTC-aware datetime for ClickHouse DateTime64(3, 'UTC') binding.

    Naive values are treated as UTC. Do not strip tzinfo: clickhouse_connect
    interprets naive datetimes as local time.
    """
    return utc_datetime(value)


def apply_trace_list_query(
    summaries: list[TraceSummary], query: TraceListQuery
) -> list[TraceSummary]:
    """Filter, newest-first order with trace_id tie-break, then paginate."""
    items = summaries
    if query.fixtures != "include":
        items = [item for item in items if item.is_fixture == (query.fixtures == "only")]
    if query.activity == "active":
        items = [
            item
            for item in items
            if item.lifecycle == "running" and item.execution_state == "executing"
        ]
    elif query.activity == "idle":
        items = [
            item for item in items if item.lifecycle == "running" and item.execution_state == "idle"
        ]
    elif query.activity == "terminal":
        items = [
            item
            for item in items
            if item.lifecycle in {"completed", "failed", "cancelled"} and item.ended_at is not None
        ]
    elif query.activity == "completed_turns":
        items = [item for item in items if item.turns_completed > 0]
    if query.project_id is not None:
        items = [item for item in items if item.project_id == query.project_id]
    if query.status is not None:
        items = [item for item in items if item.status == query.status]
    if query.lifecycle is not None:
        items = [item for item in items if item.lifecycle == query.lifecycle]
    if query.source is not None:
        items = [item for item in items if item.source == query.source]
    if query.provider is not None:
        items = [item for item in items if item.provider == query.provider]
    if query.agent_framework is not None:
        items = [item for item in items if item.agent_framework == query.agent_framework]
    if query.started_after is not None:
        after = utc_datetime(query.started_after)
        items = [item for item in items if utc_datetime(item.started_at) >= after]
    if query.started_before is not None:
        before = utc_datetime(query.started_before)
        items = [item for item in items if utc_datetime(item.started_at) <= before]

    items = sorted(
        items, key=lambda item: (utc_datetime(item.started_at), item.trace_id), reverse=True
    )
    offset = max(query.offset, 0)
    if query.limit is None:
        return items[offset:]
    return items[offset : offset + query.limit]


class InMemoryTraceRepository:
    """Isolated repository for unit tests. Not used as the local-dev default."""

    def __init__(self, inactivity_seconds: float = 120) -> None:
        self._traces: TraceMap = {}
        self._events: EventLog = {}
        self._lock = Lock()
        self.inactivity_seconds = inactivity_seconds
        self.fail_after_event_insert: int | None = None
        self._new_event_inserts = 0

    def save(self, trace_create: TraceCreate) -> Trace:
        trace = Trace.from_create(trace_create)
        with self._lock:
            if trace.trace_id in self._traces or trace.trace_id in self._events:
                raise TraceConflictError(str(trace.trace_id))
            self._traces[trace.trace_id] = trace
        return trace

    def get(self, trace_id: UUID) -> Trace:
        with self._lock:
            return self._get_unlocked(trace_id)

    def list(self, query: TraceListQuery | None = None) -> Iterable[TraceSummary]:
        with self._lock:
            summaries = [TraceSummary.from_trace(trace) for trace in self._all_traces_unlocked()]
        return apply_trace_list_query(summaries, query or TraceListQuery())

    def list_projects(self) -> Iterable[str]:
        with self._lock:
            return sorted({trace.project_id for trace in self._all_traces_unlocked()})

    def start_run(self, start: RunStart) -> Trace:
        event = start_event(start)
        with self._lock:
            if start.trace_id in self._traces or (
                start.trace_id in self._events and self._events[start.trace_id]
            ):
                existing_ids = self._events.get(start.trace_id, [])
                if any(item.event_id == event.event_id for item in existing_ids):
                    return reconstruct_trace(
                        self._events[start.trace_id],
                        inactivity_seconds=self.inactivity_seconds,
                    )
                raise TraceConflictError(str(start.trace_id))
            self._append_unlocked(start.trace_id, event)
            return reconstruct_trace(
                self._events[start.trace_id],
                inactivity_seconds=self.inactivity_seconds,
            )

    def append_events(self, trace_id: UUID, events: Sequence[RunEventIn]) -> Trace:
        with self._lock:
            if trace_id not in self._events:
                raise TraceNotFoundError(str(trace_id))
            for event in events:
                stored = inbound_to_stored(trace_id, event)
                self._append_unlocked(trace_id, stored)
            return reconstruct_trace(
                self._events[trace_id],
                inactivity_seconds=self.inactivity_seconds,
            )

    def finalize_run(self, trace_id: UUID, finalize: RunFinalize) -> Trace:
        with self._lock:
            if trace_id not in self._events:
                raise TraceNotFoundError(str(trace_id))
            stored = finalize_to_stored(trace_id, finalize)
            self._append_unlocked(trace_id, stored)
            return reconstruct_trace(
                self._events[trace_id],
                inactivity_seconds=self.inactivity_seconds,
            )

    def _append_unlocked(self, trace_id: UUID, event: StoredRunEvent) -> None:
        existing = self._events.setdefault(trace_id, [])
        try:
            gate_new_event(existing, event)
        except KeyError as error:
            raise TraceNotFoundError(str(trace_id)) from error
        if any(item.event_id == event.event_id for item in existing):
            return
        existing.append(event)
        self._new_event_inserts += 1
        if (
            self.fail_after_event_insert is not None
            and self._new_event_inserts >= self.fail_after_event_insert
        ):
            raise TraceStorageError("injected failure after event insert")

    def _get_unlocked(self, trace_id: UUID) -> Trace:
        if trace_id in self._events:
            return reconstruct_trace(
                self._events[trace_id],
                inactivity_seconds=self.inactivity_seconds,
            )
        try:
            return self._traces[trace_id]
        except KeyError as error:
            raise TraceNotFoundError(str(trace_id)) from error

    def _all_traces_unlocked(self) -> Sequence[Trace]:
        live_ids = set(self._events)
        traces = [
            reconstruct_trace(events, inactivity_seconds=self.inactivity_seconds)
            for events in self._events.values()
            if events
        ]
        traces.extend(trace for trace_id, trace in self._traces.items() if trace_id not in live_ids)
        return traces
