"""ClickHouse implementation of the trace repository boundary.

Duplicate detection uses a process-local lock around SELECT + INSERT. That is
enough for the single-process local MVP; two API processes can still race.
Visibility of spans is gated by ingestion_id on the committed trace header so a
failed header insert plus retry cannot inflate the observable graph.
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
    Span,
    SpanStatus,
    SpanType,
    Trace,
    TraceCreate,
    TraceSummary,
)
from no_more_500s_api.services.trace_service import (
    TraceConflictError,
    TraceListQuery,
    TraceNotFoundError,
    TraceStorageError,
)

_ZERO_UUID = UUID(int=0)
_INGESTION_DEFAULT = str(_ZERO_UUID)


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
    ) -> None:
        self._host = host
        self._port = port
        self._database = database
        self._username = username
        self._password = password
        self._client: Any | None = None
        self._schema_ready = False
        self._lock = Lock()

    def save(self, trace_create: TraceCreate) -> Trace:
        trace = Trace.from_create(trace_create)
        ingestion_id = uuid4()
        with self._lock:
            try:
                if self._trace_exists(trace.trace_id):
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
                result = self._client_or_raise().query(
                    "SELECT DISTINCT project_id FROM traces ORDER BY project_id ASC"
                )
                return [str(row[0]) for row in result.result_rows]
            except TraceStorageError:
                raise
            except Exception as error:
                raise TraceStorageError("Trace storage is unavailable") from error

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
        )
        for statement in statements:
            self._client.command(statement)

    def _trace_exists(self, trace_id: UUID) -> bool:
        result = self._client_or_raise().query(
            "SELECT 1 FROM traces WHERE trace_id = {trace_id:UUID} LIMIT 1",
            parameters={"trace_id": str(trace_id)},
        )
        return bool(result.result_rows)

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
            status=SpanStatus(status),
            attributes=_load(attributes_json) or {},
            spans=spans,
            duration_ms=duration_ms,
            total_tokens=total_tokens,
            total_cost_usd=float(total_cost_usd),
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

        limit_sql = ""
        if query.limit is not None:
            limit_sql = "LIMIT {limit:UInt32} OFFSET {offset:UInt32}"
            parameters["limit"] = query.limit
            parameters["offset"] = max(query.offset, 0)
        elif query.offset:
            limit_sql = "LIMIT {limit:UInt32} OFFSET {offset:UInt32}"
            parameters["limit"] = 1_000_000_000
            parameters["offset"] = query.offset

        result = self._client_or_raise().query(
            f"""
            SELECT trace_id, project_id, name, started_at, duration_ms,
                   total_tokens, total_cost_usd, status
            FROM traces
            WHERE {" AND ".join(clauses)}
            ORDER BY started_at DESC, trace_id DESC
            {limit_sql}
            """,
            parameters=parameters,
        )
        return [
            TraceSummary(
                trace_id=row[0],
                project_id=row[1],
                name=row[2],
                started_at=row[3],
                duration_ms=row[4],
                total_tokens=row[5],
                total_cost_usd=float(row[6]),
                status=SpanStatus(row[7]),
            )
            for row in result.result_rows
        ]
