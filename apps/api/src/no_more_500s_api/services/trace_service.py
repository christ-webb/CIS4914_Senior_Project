from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from threading import Lock
from typing import Protocol
from uuid import UUID

from no_more_500s_api.domain.models import SpanStatus, Trace, TraceCreate, TraceSummary


class TraceNotFoundError(KeyError):
    pass


class TraceConflictError(ValueError):
    """Raised when a trace_id already exists. Not a storage outage."""


class TraceStorageError(RuntimeError):
    """Raised when the backing store cannot complete a read or write."""


@dataclass(frozen=True)
class TraceListQuery:
    project_id: str | None = None
    status: SpanStatus | None = None
    started_after: datetime | None = None
    started_before: datetime | None = None
    limit: int | None = None
    offset: int = 0


class TraceRepository(Protocol):
    def save(self, trace_create: TraceCreate) -> Trace: ...

    def get(self, trace_id: UUID) -> Trace: ...

    def list(self, query: TraceListQuery | None = None) -> Iterable[TraceSummary]: ...

    def list_projects(self) -> Iterable[str]: ...


def apply_trace_list_query(
    summaries: list[TraceSummary], query: TraceListQuery
) -> list[TraceSummary]:
    """Filter, newest-first order with trace_id tie-break, then paginate."""
    items = summaries
    if query.project_id is not None:
        items = [item for item in items if item.project_id == query.project_id]
    if query.status is not None:
        items = [item for item in items if item.status == query.status]
    if query.started_after is not None:
        items = [item for item in items if item.started_at >= query.started_after]
    if query.started_before is not None:
        items = [item for item in items if item.started_at <= query.started_before]
    items = sorted(items, key=lambda item: (item.started_at, item.trace_id), reverse=True)
    offset = max(query.offset, 0)
    if query.limit is None:
        return items[offset:]
    return items[offset : offset + query.limit]


class InMemoryTraceRepository:
    """Isolated repository for unit tests. Not used as the local-dev default."""

    def __init__(self) -> None:
        self._traces: dict[UUID, Trace] = {}
        self._lock = Lock()

    def save(self, trace_create: TraceCreate) -> Trace:
        trace = Trace.from_create(trace_create)
        with self._lock:
            if trace.trace_id in self._traces:
                raise TraceConflictError(str(trace.trace_id))
            self._traces[trace.trace_id] = trace
        return trace

    def get(self, trace_id: UUID) -> Trace:
        try:
            return self._traces[trace_id]
        except KeyError as error:
            raise TraceNotFoundError(str(trace_id)) from error

    def list(self, query: TraceListQuery | None = None) -> Iterable[TraceSummary]:
        summaries = [TraceSummary.from_trace(trace) for trace in self._traces.values()]
        return apply_trace_list_query(summaries, query or TraceListQuery())

    def list_projects(self) -> Iterable[str]:
        return sorted({trace.project_id for trace in self._traces.values()})
