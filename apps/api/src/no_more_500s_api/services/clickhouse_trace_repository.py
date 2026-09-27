"""ClickHouse-backed implementation of the trace repository boundary.

Matches the real in-memory implementation's interface exactly
(services/trace_service.py: InMemoryTraceRepository -> save/get/list),
so this is a drop-in replacement, and matches the real schema in
infra/clickhouse/init.sql: separate `traces` and `spans` tables joined
on trace_id, not spans nested as JSON inside a single row.

Per docs/architecture/overview.md, Redis is deferred until justified.
This adds a small in-process TTL cache in front of get() reads instead
— traces are immutable once written, so caching is safe without a
Redis-backed invalidation strategy.
"""

from __future__ import annotations

import json
import time
from collections.abc import Iterable
from decimal import Decimal
from typing import Any
from uuid import UUID

from no_more_500s_api.domain.models import (
    Span,
    SpanStatus,
    SpanType,
    Trace,
    TraceCreate,
    TraceSummary,
)
from no_more_500s_api.services.trace_service import TraceNotFoundError

try:
    import clickhouse_connect  # type: ignore
except ImportError:  # pragma: no cover - allows import without the dep installed
    clickhouse_connect = None  # type: ignore


# ---------------------------------------------------------------------------
# Small in-process TTL cache (Redis-free, per current architecture decision)
# ---------------------------------------------------------------------------

class _TTLCache:
    def __init__(self, ttl_seconds: float = 30.0) -> None:
        self._ttl = ttl_seconds
        self._store: dict[str, tuple[float, Any]] = {}

    def get(self, key: str) -> Any | None:
        entry = self._store.get(key)
        if entry is None:
            return None
        expires_at, value = entry
        if time.monotonic() >= expires_at:
            self._store.pop(key, None)
            return None
        return value

    def set(self, key: str, value: Any) -> None:
        self._store[key] = (time.monotonic() + self._ttl, value)

    def invalidate(self, key: str) -> None:
        self._store.pop(key, None)


def _dump(value: dict[str, Any] | None) -> str:
    """JSON-encode an optional dict for the *_json String columns."""
    return json.dumps(value) if value is not None else "null"


def _load(raw: str | None) -> dict[str, Any] | None:
    if raw is None or raw == "null":
        return None
    return json.loads(raw)


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
        host: str = "localhost",
        port: int = 8123,
        database: str = "nomore500s",  # matches .env.example / init.sql
        cache_ttl_seconds: float = 30.0,
    ) -> None:
        if clickhouse_connect is None:
            raise RuntimeError(
                "clickhouse-connect is not installed. Add it to pyproject.toml "
                "(e.g. `uv add clickhouse-connect`) before using this repository."
            )
        self._client = clickhouse_connect.get_client(
            host=host, port=port, database=database
        )
        self._cache = _TTLCache(ttl_seconds=cache_ttl_seconds)

    def save(self, trace_create: TraceCreate) -> Trace:
        trace = Trace.from_create(trace_create)

        self._client.insert(
            "traces",
            [
                [
                    trace.trace_id,
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
                        trace.trace_id,
                        span.span_id,
                        span.parent_span_id,
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

        self._cache.invalidate(str(trace.trace_id))
        return trace

    def get(self, trace_id: UUID) -> Trace:
        cache_key = str(trace_id)
        cached = self._cache.get(cache_key)
        if cached is not None:
            return cached

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
            ORDER BY started_at
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
        self._cache.set(cache_key, trace)
        return trace

    def list(self) -> Iterable[TraceSummary]:
        result = self._client.query(
            """
            SELECT trace_id, project_id, name, started_at, duration_ms,
                   total_tokens, total_cost_usd, status
            FROM traces
            ORDER BY started_at DESC
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
