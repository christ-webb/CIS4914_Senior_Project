from __future__ import annotations

import logging
import threading
import time
from collections import deque
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import UUID, uuid4

import httpx

from nomore500s.models import SpanType, TraceData

logger = logging.getLogger("nomore500s")

MAX_QUEUE = 256
DEFAULT_HEARTBEAT_SECONDS = 15.0
DEFAULT_RETRIES = 3


class TelemetryError(Exception):
    """A telemetry delivery failure. Distinct from agent execution errors."""


def _now() -> datetime:
    return datetime.now(UTC)


def _jsonable(value: Any) -> Any:
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {key: _jsonable(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    return value


@dataclass
class _Pending:
    kind: Literal["start", "events", "finalize"]
    event_id: UUID
    path: str
    payload: Any
    critical: bool = True


@dataclass
class LiveSpan:
    span_id: UUID
    name: str
    type: SpanType
    input: dict[str, Any] | None = None
    output: dict[str, Any] | None = None
    attributes: dict[str, Any] = field(default_factory=dict)
    token_count: int = 0
    cost_usd: float = 0


class LiveRun:
    def __init__(
        self,
        client: NoMore500sClient,
        *,
        project_id: str,
        name: str,
        trace_id: UUID | None = None,
        attributes: dict[str, Any] | None = None,
        agent_name: str | None = None,
        agent_input: dict[str, Any] | None = None,
        heartbeat_seconds: float = DEFAULT_HEARTBEAT_SECONDS,
    ) -> None:
        self._client = client
        self.trace_id = trace_id or uuid4()
        self.project_id = project_id
        self.name = name
        self.agent_span_id = uuid4()
        self.start_event_id = uuid4()
        self.telemetry_errors: list[TelemetryError] = []
        self._sequence = 0
        self._seq_lock = threading.Lock()
        self._queue: deque[_Pending] = deque()
        self._queue_lock = threading.Lock()
        self._stop = threading.Event()
        self._closed = False
        self._heartbeat_seconds = heartbeat_seconds
        self._heartbeat_thread: threading.Thread | None = None
        pending = _Pending(
            kind="start",
            event_id=self.start_event_id,
            path="/v1/runs",
            payload={
                "trace_id": str(self.trace_id),
                "project_id": project_id,
                "name": name,
                "event_id": str(self.start_event_id),
                "started_at": _now().isoformat(),
                "attributes": attributes or {},
                "agent_name": agent_name or name,
                "agent_span_id": str(self.agent_span_id),
                "agent_input": agent_input,
            },
        )
        self._enqueue(pending)
        self._flush()
        self._heartbeat_thread = threading.Thread(
            target=self._heartbeat_loop,
            name="nm5-heartbeat",
            daemon=True,
        )
        self._heartbeat_thread.start()

    def _next_sequence(self) -> int:
        with self._seq_lock:
            self._sequence += 1
            return self._sequence

    def _enqueue(self, pending: _Pending) -> None:
        with self._queue_lock:
            if len(self._queue) >= MAX_QUEUE and not pending.critical:
                return
            while len(self._queue) >= MAX_QUEUE:
                dropped = False
                for index, item in enumerate(self._queue):
                    if not item.critical:
                        del self._queue[index]
                        dropped = True
                        break
                if not dropped:
                    logger.error("telemetry queue full; retaining critical events")
                    if pending.critical:
                        self.telemetry_errors.append(TelemetryError("telemetry queue full"))
                    return
            self._queue.append(pending)

    def _flush(self) -> None:
        while True:
            with self._queue_lock:
                if not self._queue:
                    return
                pending = self._queue[0]
            try:
                self._client._request_with_retry(
                    "POST",
                    pending.path,
                    pending.payload,
                )
            except TelemetryError as error:
                self.telemetry_errors.append(error)
                logger.warning("telemetry delivery failed: %s", error)
                if self._client.raise_on_telemetry_error:
                    raise
                return
            with self._queue_lock:
                if self._queue and self._queue[0].event_id == pending.event_id:
                    self._queue.popleft()

    def _heartbeat_loop(self) -> None:
        while not self._stop.wait(self._heartbeat_seconds):
            try:
                self.heartbeat()
            except TelemetryError:
                continue

    def heartbeat(self, progress: str | None = None) -> None:
        event_id = uuid4()
        payload = [{
            "event_id": str(event_id),
            "event_type": "heartbeat",
            "sequence": self._next_sequence(),
            "occurred_at": _now().isoformat(),
            "progress": progress,
        }]
        self._enqueue(
            _Pending(
                kind="events",
                event_id=event_id,
                path=f"/v1/runs/{self.trace_id}/events",
                payload=payload,
                critical=False,
            )
        )
        self._flush()

    def _span_event(
        self,
        event_type: str,
        span: LiveSpan,
        *,
        status: str | None = None,
        duration_ms: float | None = None,
        extra: dict[str, Any] | None = None,
    ) -> None:
        event_id = uuid4()
        body = {
            "event_id": str(event_id),
            "event_type": event_type,
            "sequence": self._next_sequence(),
            "occurred_at": _now().isoformat(),
            "span_id": str(span.span_id),
            "parent_span_id": str(self.agent_span_id),
            "type": span.type,
            "name": span.name,
            "input": span.input,
            "output": span.output,
            "attributes": span.attributes,
            "token_count": span.token_count,
            "cost_usd": span.cost_usd,
            **(extra or {}),
        }
        if status is not None:
            body["status"] = status
        if duration_ms is not None:
            body["duration_ms"] = duration_ms
        self._enqueue(
            _Pending(
                kind="events",
                event_id=event_id,
                path=f"/v1/runs/{self.trace_id}/events",
                payload=[_jsonable(body)],
            )
        )
        self._flush()

    @contextmanager
    def span(
        self,
        type: SpanType,
        name: str,
        *,
        input: dict[str, Any] | None = None,
        attributes: dict[str, Any] | None = None,
        parent_span_id: UUID | None = None,
    ) -> Iterator[LiveSpan]:
        handle = LiveSpan(
            span_id=uuid4(),
            name=name,
            type=type,
            input=input,
            attributes=attributes or {},
        )
        started = time.perf_counter()
        extra = {}
        if parent_span_id is not None:
            extra["parent_span_id"] = str(parent_span_id)
        self._span_event("span_start", handle, extra=extra)
        status: Literal["ok", "error"] = "ok"
        try:
            yield handle
        except Exception:
            status = "error"
            raise
        finally:
            duration_ms = (time.perf_counter() - started) * 1000
            self._span_event("span_complete", handle, status=status, duration_ms=duration_ms)

    def llm(self, name: str, **kwargs: Any) -> Any:
        return self.span("llm", name, **kwargs)

    def tool(self, name: str, **kwargs: Any) -> Any:
        return self.span("tool", name, **kwargs)

    def workflow(self, name: str, **kwargs: Any) -> Any:
        return self.span("workflow", name, **kwargs)

    def finalize(
        self,
        status: Literal["completed", "failed", "cancelled"] = "completed",
        error: dict[str, Any] | None = None,
    ) -> None:
        if self._closed:
            return
        self._closed = True
        self._stop.set()
        event_id = uuid4()
        payload = {
            "event_id": str(event_id),
            "sequence": self._next_sequence(),
            "occurred_at": _now().isoformat(),
            "status": status,
            "error": error,
        }
        self._enqueue(
            _Pending(
                kind="finalize",
                event_id=event_id,
                path=f"/v1/runs/{self.trace_id}/finalize",
                payload=payload,
            )
        )
        self._flush()

    def __enter__(self) -> LiveRun:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: object,
    ) -> None:
        if exc_type is not None:
            self.finalize(
                "failed",
                error={"error_type": exc_type.__name__, "message": str(exc)[:300]},
            )
        else:
            self.finalize("completed")
        self._stop.set()
        self._flush()


class NoMore500sClient:
    def __init__(
        self,
        api_url: str = "http://localhost:8000",
        timeout_seconds: float = 10,
        *,
        trust_environment: bool = True,
        retries: int = DEFAULT_RETRIES,
        raise_on_telemetry_error: bool = False,
        heartbeat_seconds: float = DEFAULT_HEARTBEAT_SECONDS,
    ) -> None:
        self._api_url = api_url.rstrip("/")
        self._timeout_seconds = timeout_seconds
        self._trust_environment = trust_environment
        self._retries = max(1, retries)
        self.raise_on_telemetry_error = raise_on_telemetry_error
        self.heartbeat_seconds = heartbeat_seconds

    def send_trace(self, trace: TraceData) -> dict[str, Any]:
        return self._request_with_retry("POST", "/v1/traces", trace.to_dict(), raise_status=True)

    def start_run(
        self,
        project_id: str,
        name: str,
        **kwargs: Any,
    ) -> LiveRun:
        return LiveRun(
            self,
            project_id=project_id,
            name=name,
            heartbeat_seconds=kwargs.pop("heartbeat_seconds", self.heartbeat_seconds),
            **kwargs,
        )

    def _request_with_retry(
        self,
        method: str,
        path: str,
        payload: Any,
        *,
        raise_status: bool = False,
    ) -> dict[str, Any]:
        last_error: Exception | None = None
        for attempt in range(self._retries):
            try:
                with httpx.Client(trust_env=self._trust_environment) as client:
                    response = client.request(
                        method,
                        f"{self._api_url}{path}",
                        json=payload,
                        timeout=self._timeout_seconds,
                    )
                if raise_status:
                    response.raise_for_status()
                elif response.status_code >= 500 or response.status_code == 429:
                    last_error = TelemetryError(
                        f"HTTP {response.status_code}: {response.text[:200]}"
                    )
                    time.sleep(min(0.05 * (2 ** attempt), 0.4))
                    continue
                elif response.status_code >= 400:
                    raise TelemetryError(f"HTTP {response.status_code}: {response.text[:200]}")
                body: dict[str, Any] = response.json()
                return body
            except TelemetryError:
                raise
            except httpx.HTTPStatusError as error:
                last_error = error
                if error.response.status_code < 500 and error.response.status_code != 429:
                    raise
            except (httpx.HTTPError, OSError, ValueError) as error:
                last_error = error
            time.sleep(min(0.05 * (2 ** attempt), 0.4))
        if raise_status and last_error is not None:
            raise last_error
        raise TelemetryError(str(last_error) if last_error else "telemetry request failed")
