"""ClickHouse implementation of the trace repository boundary."""

from __future__ import annotations

import json
from collections.abc import Iterable
from decimal import Decimal
from typing import Any, cast
from uuid import UUID

import clickhouse_connect  # type: ignore[import-untyped]

from no_more_500s_api.domain.models import (
    Span,
    SpanStatus,
    SpanType,
    Trace,
    TraceCreate,
    TraceSummary,
)
from no_more_500s_api.services.trace_service import TraceNotFoundError


def _dump(value: dict[str, Any] | None) -> str:
    """JSON-encode an optional dict for the *_json String columns."""
    return json.dumps(value) if value is not None else "null"


def _load(raw: str | None) -> dict[str, Any] | None:
    if raw is None or raw == "null":
        return None
    return cast(dict[str, Any], json.loads(raw))


# ---------------------------------------------------------------------------
# ClickHouse implementation
# ---------------------------------------------------------------------------

class ClickHouseTraceRepository:
    """Persistent trace repository backed by ClickHouse.

    Exposes the same save/get/list interface as InMemoryTraceRepository
    so it can replace `trace_repository` in services/trace_service.py
    directly (e.g. via dependency injection in the FastAPI app).
    """

    def __init__(
        self,
        host: str,
        port: int,
        database: str,
        username: str,
        password: str,
    ) -> None:
        self._client = clickhouse_connect.get_client(
            host=host, port=port, database=database, username=username, password=password
        )

    def save(self, trace_create: TraceCreate) -> Trace:
        trace = Trace.from_create(trace_create)

        self._client.insert(
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
            ],
        )

        if trace.spans:
            self._client.insert(
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
                ],
            )

        return trace

    def get(self, trace_id: UUID) -> Trace:
        trace_result = self._client.query(
            """
            SELECT trace_id, project_id, name, started_at, duration_ms,
                   total_tokens, total_cost_usd, status, attributes_json
            FROM traces
            WHERE trace_id = {trace_id:UUID}
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
        ) = trace_result.result_rows[0]

        span_result = self._client.query(
            """
            SELECT span_id, parent_span_id, type, name, started_at, duration_ms,
                   status, token_count, cost_usd, input_json, output_json,
                   attributes_json
            FROM spans
            WHERE trace_id = {trace_id:UUID}
            ORDER BY started_at, span_id
            """,
            parameters={"trace_id": str(trace_id)},
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

        trace = Trace(
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
        return trace

    def list(self) -> Iterable[TraceSummary]:
        result = self._client.query(
            """
            SELECT trace_id, project_id, name, started_at, duration_ms,
                   total_tokens, total_cost_usd, status
            FROM traces
            ORDER BY started_at DESC, trace_id DESC
            """
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
