from collections.abc import Iterable
from threading import Lock
from typing import Protocol
from uuid import UUID

from no_more_500s_api.domain.models import Trace, TraceCreate, TraceSummary


class TraceNotFoundError(KeyError):
    pass


class TraceRepository(Protocol):
    def save(self, trace_create: TraceCreate) -> Trace: ...

    def get(self, trace_id: UUID) -> Trace: ...

    def list(self) -> Iterable[TraceSummary]: ...


class InMemoryTraceRepository:
    """Lightweight repository for tests and local development."""

    def __init__(self) -> None:
        self._traces: dict[UUID, Trace] = {}
        self._lock = Lock()

    def save(self, trace_create: TraceCreate) -> Trace:
        trace = Trace.from_create(trace_create)
        with self._lock:
            self._traces[trace.trace_id] = trace
        return trace

    def get(self, trace_id: UUID) -> Trace:
        try:
            return self._traces[trace_id]
        except KeyError as error:
            raise TraceNotFoundError(str(trace_id)) from error

    def list(self) -> Iterable[TraceSummary]:
        traces = sorted(self._traces.values(), key=lambda item: item.started_at, reverse=True)
        return [TraceSummary.from_trace(trace) for trace in traces]

