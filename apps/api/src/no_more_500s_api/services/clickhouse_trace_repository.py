"""ClickHouse implementation of the trace repository boundary.

Duplicate detection uses a process-local lock around SELECT + INSERT. That is
enough for the single-process local MVP; two API processes can still race.
Visibility of batch spans is gated by ingestion_id on the committed trace header
so a failed header insert plus retry cannot inflate the observable graph.

Live runs are stored only in the append-only run_events log. Reconstruction
from that log is authoritative; a later projection is never required for a
committed event to be visible.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Sequence
from decimal import Decimal
from threading import Lock
from typing import Any, cast
from uuid import UUID, uuid4

import clickhouse_connect  # type: ignore[import-untyped]

from no_more_500s_api.domain.models import (
    RunEventIn,
    RunEventType,
    RunFinalize,
    RunStart,
    Span,
    SpanStatus,
    SpanType,
    StoredRunEvent,
    Trace,
    TraceCreate,
    TraceSummary,
    lifecycle_from_batch_status,
)
from no_more_500s_api.services.run_reconstruction import (
    InvalidLifecycleError,
    finalize_to_stored,
    gate_new_event,
    inbound_to_stored,
    reconstruct_trace,
    start_event,
)
from no_more_500s_api.services.trace_service import (
    EventLog,
    TraceConflictError,
    TraceListQuery,
    TraceNotFoundError,
    TraceStorageError,
    apply_trace_list_query,
)

_ZERO_UUID = UUID(int=0)
_INGESTION_DEFAULT = str(_ZERO_UUID)

_RUN_EVENTS_DDL = """
CREATE TABLE IF NOT EXISTS run_events
(
    event_id UUID,
    trace_id UUID,
    event_type LowCardinality(String),
    sequence UInt64,
    occurred_at DateTime64(3, 'UTC'),
    received_at DateTime64(3, 'UTC'),
    payload_json String
)
ENGINE = MergeTree
ORDER BY (trace_id, sequence, event_id)
"""


def _dump(value: dict[str, Any] | None) -> str:
    """JSON-encode an optional dict for the *_json String columns."""
    return json.dumps(value) if value is not None else "null"


def _load(raw: str | None) -> dict[str, Any] | None:
    if raw is None or raw == "null":
        return None
    return cast(dict[str, Any], json.loads(raw))


class ClickHouseTraceRepository:
    """Persistent trace repository backed by ClickHouse.

    The ClickHouse client is created on first use so importing the FastAPI app
    does not require a live database. Unit tests must still inject
    InMemoryTraceRepository rather than exercising this class.
    """

    def __init__(
        self,
        host: str,
        port: int,
        database: str,
        username: str,
        password: str,
        inactivity_seconds: float = 120,
    ) -> None:
        self._host = host
        self._port = port
        self._database = database
        self._username = username
        self._password = password
        self.inactivity_seconds = inactivity_seconds
        self._client: Any | None = None
        self._schema_ready = False
        self._lock = Lock()
        self.fail_after_event_insert: int | None = None
        self._new_event_inserts = 0

    def save(self, trace_create: TraceCreate) -> Trace:
        trace = Trace.from_create(trace_create)
        ingestion_id = uuid4()
        with self._lock:
            try:
                if self._id_exists(trace.trace_id):
                    raise TraceConflictError(str(trace.trace_id))
                self._insert_spans(trace, ingestion_id)
                self._insert_trace(trace, ingestion_id)
            except (TraceConflictError, TraceStorageError):
                raise
            except Exception as error:
                raise TraceStorageError("Trace storage is unavailable") from error
        return trace

    def get(self, trace_id: UUID) -> Trace:
        with self._lock:
            try:
                return self._get_unlocked(trace_id)
            except (TraceNotFoundError, TraceStorageError):
                raise
            except Exception as error:
                raise TraceStorageError("Trace storage is unavailable") from error

    def list(self, query: TraceListQuery | None = None) -> Iterable[TraceSummary]:
        filters = query or TraceListQuery()
        with self._lock:
            try:
                return self._list_unlocked(filters)
            except TraceStorageError:
                raise
            except Exception as error:
                raise TraceStorageError("Trace storage is unavailable") from error

    def list_projects(self) -> Iterable[str]:
        with self._lock:
            try:
                batch = self._client_or_raise().query(
                    "SELECT DISTINCT project_id FROM traces ORDER BY project_id ASC"
                )
                live = {
                    reconstruct_trace(events, inactivity_seconds=self.inactivity_seconds).project_id
                    for events in self._load_all_events_unlocked().values()
                    if events
                }
                names = {str(row[0]) for row in batch.result_rows}
                names.update(live)
                return sorted(names)
            except TraceStorageError:
                raise
            except Exception as error:
                raise TraceStorageError("Trace storage is unavailable") from error

    def start_run(self, start: RunStart) -> Trace:
        event = start_event(start)
        with self._lock:
            try:
                existing = [*self._load_events_unlocked(start.trace_id)]
                if existing:
                    if any(item.event_id == event.event_id for item in existing):
                        return reconstruct_trace(
                            existing,
                            inactivity_seconds=self.inactivity_seconds,
                        )
                    raise TraceConflictError(str(start.trace_id))
                if self._batch_exists(start.trace_id):
                    raise TraceConflictError(str(start.trace_id))
                self._append_unlocked(existing, event)
                return reconstruct_trace(
                    self._load_events_unlocked(start.trace_id),
                    inactivity_seconds=self.inactivity_seconds,
                )
            except (
                TraceConflictError,
                TraceStorageError,
                InvalidLifecycleError,
                TraceNotFoundError,
            ):
                raise
            except Exception as error:
                raise TraceStorageError("Trace storage is unavailable") from error

    def append_events(self, trace_id: UUID, events: Sequence[RunEventIn]) -> Trace:
        with self._lock:
            try:
                existing = [*self._load_events_unlocked(trace_id)]
                if not existing:
                    raise TraceNotFoundError(str(trace_id))
                for event in events:
                    stored = inbound_to_stored(trace_id, event)
                    self._append_unlocked(existing, stored)
                    if stored.event_id not in {item.event_id for item in existing}:
                        existing.append(stored)
                return reconstruct_trace(existing, inactivity_seconds=self.inactivity_seconds)
            except (TraceNotFoundError, TraceStorageError, InvalidLifecycleError):
                raise
            except Exception as error:
                raise TraceStorageError("Trace storage is unavailable") from error

    def finalize_run(self, trace_id: UUID, finalize: RunFinalize) -> Trace:
        with self._lock:
            try:
                existing = [*self._load_events_unlocked(trace_id)]
                if not existing:
                    raise TraceNotFoundError(str(trace_id))
                stored = finalize_to_stored(trace_id, finalize)
                self._append_unlocked(existing, stored)
                if stored.event_id not in {item.event_id for item in existing}:
                    existing.append(stored)
                return reconstruct_trace(existing, inactivity_seconds=self.inactivity_seconds)
            except (TraceNotFoundError, TraceStorageError, InvalidLifecycleError):
                raise
            except Exception as error:
                raise TraceStorageError("Trace storage is unavailable") from error

    def _append_unlocked(self, existing: Sequence[StoredRunEvent], event: StoredRunEvent) -> None:
        try:
            gate_new_event(existing, event)
        except KeyError as error:
            raise TraceNotFoundError(str(event.trace_id)) from error
        if any(item.event_id == event.event_id for item in existing):
            return
        self._insert_run_event(event)
        self._new_event_inserts += 1
        if (
            self.fail_after_event_insert is not None
            and self._new_event_inserts >= self.fail_after_event_insert
        ):
            raise TraceStorageError("injected failure after event insert")

    def _client_or_raise(self) -> Any:
        if self._client is None:
            try:
                self._client = clickhouse_connect.get_client(
                    host=self._host,
                    port=self._port,
                    database=self._database,
                    username=self._username,
                    password=self._password,
                )
            except Exception as error:
                raise TraceStorageError("Trace storage is unavailable") from error
        if not self._schema_ready:
            self._ensure_schema()
            self._schema_ready = True
        return self._client

    def _ensure_schema(self) -> None:
        assert self._client is not None
        statements = (
            f"ALTER TABLE traces ADD COLUMN IF NOT EXISTS "
            f"ingestion_id UUID DEFAULT toUUID('{_INGESTION_DEFAULT}')",
            f"ALTER TABLE spans ADD COLUMN IF NOT EXISTS "
            f"ingestion_id UUID DEFAULT toUUID('{_INGESTION_DEFAULT}')",
            _RUN_EVENTS_DDL,
        )
        for statement in statements:
            self._client.command(statement)

    def _id_exists(self, trace_id: UUID) -> bool:
        return self._batch_exists(trace_id) or bool(self._load_events_unlocked(trace_id))

    def _batch_exists(self, trace_id: UUID) -> bool:
        result = self._client_or_raise().query(
            "SELECT 1 FROM traces WHERE trace_id = {trace_id:UUID} LIMIT 1",
            parameters={"trace_id": str(trace_id)},
        )
        return bool(result.result_rows)

    def _insert_run_event(self, event: StoredRunEvent) -> None:
        self._client_or_raise().insert(
            "run_events",
            [
                [
                    str(event.event_id),
                    str(event.trace_id),
                    event.event_type.value,
                    event.sequence,
                    event.occurred_at,
                    event.received_at,
                    json.dumps(event.payload, default=str),
                ]
            ],
            column_names=[
                "event_id",
                "trace_id",
                "event_type",
                "sequence",
                "occurred_at",
                "received_at",
                "payload_json",
            ],
        )

    def _load_events_unlocked(self, trace_id: UUID) -> Sequence[StoredRunEvent]:
        result = self._client_or_raise().query(
            """
            SELECT event_id, trace_id, event_type, sequence, occurred_at, received_at, payload_json
            FROM run_events
            WHERE trace_id = {trace_id:UUID}
            """,
            parameters={"trace_id": str(trace_id)},
        )
        return [_row_to_event(row) for row in result.result_rows]

    def _load_all_events_unlocked(self) -> EventLog:
        result = self._client_or_raise().query(
            """
            SELECT event_id, trace_id, event_type, sequence, occurred_at, received_at, payload_json
            FROM run_events
            """
        )
        grouped: EventLog = {}
        for row in result.result_rows:
            event = _row_to_event(row)
            grouped.setdefault(event.trace_id, []).append(event)
        return grouped

    def _insert_spans(self, trace: Trace, ingestion_id: UUID) -> None:
        if not trace.spans:
            return
        self._client_or_raise().insert(
            "spans",
            [
                [
                    str(trace.trace_id),
                    str(span.span_id),
                    str(span.parent_span_id) if span.parent_span_id else None,
                    span.type.value,
                    span.name,
                    span.started_at,
                    span.duration_ms,
                    span.status.value,
                    span.token_count,
                    Decimal(str(span.cost_usd)),
                    _dump(span.input),
                    _dump(span.output),
                    _dump(span.attributes),
                    str(ingestion_id),
                ]
                for span in trace.spans
            ],
            column_names=[
                "trace_id",
                "span_id",
                "parent_span_id",
                "type",
                "name",
                "started_at",
                "duration_ms",
                "status",
                "token_count",
                "cost_usd",
                "input_json",
                "output_json",
                "attributes_json",
                "ingestion_id",
            ],
        )

    def _insert_trace(self, trace: Trace, ingestion_id: UUID) -> None:
        self._client_or_raise().insert(
            "traces",
            [
                [
                    str(trace.trace_id),
                    trace.project_id,
                    trace.name,
                    trace.started_at,
                    trace.duration_ms,
                    trace.total_tokens,
                    Decimal(str(trace.total_cost_usd)),
                    trace.status.value,
                    _dump(trace.attributes),
                    str(ingestion_id),
                ]
            ],
            column_names=[
                "trace_id",
                "project_id",
                "name",
                "started_at",
                "duration_ms",
                "total_tokens",
                "total_cost_usd",
                "status",
                "attributes_json",
                "ingestion_id",
            ],
        )

    def _get_unlocked(self, trace_id: UUID) -> Trace:
        live_events = self._load_events_unlocked(trace_id)
        if live_events:
            return reconstruct_trace(live_events, inactivity_seconds=self.inactivity_seconds)

        trace_result = self._client_or_raise().query(
            """
            SELECT trace_id, project_id, name, started_at, duration_ms,
                   total_tokens, total_cost_usd, status, attributes_json,
                   ingestion_id
            FROM traces
            WHERE trace_id = {trace_id:UUID}
            ORDER BY started_at DESC, trace_id DESC
            LIMIT 1
            """,
            parameters={"trace_id": str(trace_id)},
        )
        if not trace_result.result_rows:
            raise TraceNotFoundError(str(trace_id))

        (
            tid,
            project_id,
            name,
            started_at,
            duration_ms,
            total_tokens,
            total_cost_usd,
            status,
            attributes_json,
            ingestion_id,
        ) = trace_result.result_rows[0]
        parsed_status = SpanStatus(status)

        span_result = self._client_or_raise().query(
            """
            SELECT span_id, parent_span_id, type, name, started_at, duration_ms,
                   status, token_count, cost_usd, input_json, output_json,
                   attributes_json
            FROM spans
            WHERE trace_id = {trace_id:UUID}
              AND ingestion_id = {ingestion_id:UUID}
            ORDER BY started_at, span_id
            """,
            parameters={"trace_id": str(trace_id), "ingestion_id": str(ingestion_id)},
        )

        spans = [
            Span(
                span_id=row[0],
                parent_span_id=row[1],
                type=SpanType(row[2]),
                name=row[3],
                started_at=row[4],
                duration_ms=row[5],
                status=SpanStatus(row[6]),
                token_count=row[7],
                cost_usd=float(row[8]),
                input=_load(row[9]),
                output=_load(row[10]),
                attributes=_load(row[11]) or {},
            )
            for row in span_result.result_rows
        ]

        return Trace(
            trace_id=tid,
            project_id=project_id,
            name=name,
            started_at=started_at,
            status=parsed_status,
            attributes=_load(attributes_json) or {},
            spans=spans,
            duration_ms=duration_ms,
            total_tokens=total_tokens,
            total_cost_usd=float(total_cost_usd),
            lifecycle=lifecycle_from_batch_status(parsed_status),
            last_activity_at=started_at,
            ended_at=started_at if parsed_status in {SpanStatus.OK, SpanStatus.ERROR} else None,
        )

    def _list_unlocked(self, query: TraceListQuery) -> Sequence[TraceSummary]:
        clauses = ["1 = 1"]
        parameters: dict[str, Any] = {}
        if query.project_id is not None:
            clauses.append("project_id = {project_id:String}")
            parameters["project_id"] = query.project_id
        if query.status is not None:
            clauses.append("status = {status:String}")
            parameters["status"] = query.status.value
        if query.started_after is not None:
            clauses.append("started_at >= {started_after:DateTime64(3)}")
            parameters["started_after"] = query.started_after
        if query.started_before is not None:
            clauses.append("started_at <= {started_before:DateTime64(3)}")
            parameters["started_before"] = query.started_before

        result = self._client_or_raise().query(
            f"""
            SELECT trace_id, project_id, name, started_at, duration_ms,
                   total_tokens, total_cost_usd, status
            FROM traces
            WHERE {" AND ".join(clauses)}
            ORDER BY started_at DESC, trace_id DESC
            """,
            parameters=parameters,
        )
        live_ids: set[UUID] = set()
        live_summaries: list[TraceSummary] = []
        for events in self._load_all_events_unlocked().values():
            if not events:
                continue
            trace = reconstruct_trace(events, inactivity_seconds=self.inactivity_seconds)
            live_ids.add(trace.trace_id)
            live_summaries.append(TraceSummary.from_trace(trace))

        batch = [
            TraceSummary(
                trace_id=row[0],
                project_id=row[1],
                name=row[2],
                started_at=row[3],
                duration_ms=row[4],
                total_tokens=row[5],
                total_cost_usd=float(row[6]),
                status=SpanStatus(row[7]),
                lifecycle=lifecycle_from_batch_status(SpanStatus(row[7])),
                last_activity_at=row[3],
                ended_at=(
                    row[3]
                    if SpanStatus(row[7]) in {SpanStatus.OK, SpanStatus.ERROR}
                    else None
                ),
            )
            for row in result.result_rows
            if row[0] not in live_ids
        ]
        return apply_trace_list_query(live_summaries + batch, query)


def _row_to_event(row: Sequence[Any]) -> StoredRunEvent:
    payload = _load(row[6]) or {}
    return StoredRunEvent(
        event_id=row[0],
        trace_id=row[1],
        event_type=RunEventType(row[2]),
        sequence=int(row[3]),
        occurred_at=row[4],
        received_at=row[5],
        payload=payload,
    )
