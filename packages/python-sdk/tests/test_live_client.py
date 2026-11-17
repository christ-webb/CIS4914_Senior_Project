from uuid import UUID, uuid4

import httpx
import pytest
from nomore500s.client import LiveRun, NoMore500sClient, TelemetryError
from nomore500s.models import TraceData


def _client(handler, **kwargs: object) -> NoMore500sClient:
    original = httpx.Client

    def factory(*args: object, **kw: object) -> httpx.Client:
        return original(transport=httpx.MockTransport(handler), trust_env=False)

    kwargs.setdefault("raise_on_telemetry_error", True)
    kwargs.setdefault("retries", 2)
    kwargs.setdefault("heartbeat_seconds", 60)
    client = NoMore500sClient(**kwargs)
    return client, factory


def test_start_run_and_span_context_send_stable_ids(monkeypatch: pytest.MonkeyPatch) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(201, json={"trace_id": "11111111-1111-1111-1111-111111111111"})

    client, factory = _client(handler)
    monkeypatch.setattr(httpx, "Client", factory)
    run = client.start_run("p", "n", trace_id=UUID("11111111-1111-1111-1111-111111111111"))
    with run.tool("search", input={"q": "x"}) as span:
        span.output = {"ok": True}
        span.token_count = 3
    run.finalize("completed")
    assert any(str(request.url).endswith("/v1/runs") for request in requests)
    event_posts = [request for request in requests if request.url.path.endswith("/events")]
    assert len(event_posts) >= 2
    run._stop.set()


def test_retries_reuse_event_ids(monkeypatch: pytest.MonkeyPatch) -> None:
    attempts = {"n": 0}
    bodies: list[bytes] = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempts["n"] += 1
        bodies.append(request.content)
        if attempts["n"] == 1:
            return httpx.Response(503, json={"detail": "Trace storage is unavailable"})
        return httpx.Response(200, json={"lifecycle": "running"})

    client, factory = _client(handler)
    monkeypatch.setattr(httpx, "Client", factory)
    run = LiveRun.__new__(LiveRun)
    run._client = client
    run.trace_id = uuid4()
    run.telemetry_errors = []
    import threading
    from collections import deque

    from nomore500s.client import _Pending
    run._queue = deque()
    run._queue_lock = threading.Lock()
    event_id = uuid4()
    run._queue.append(
        _Pending(
            "events",
            event_id,
            f"/v1/runs/{run.trace_id}/events",
            [{"event_id": str(event_id)}],
        )
    )
    run._flush()
    assert attempts["n"] == 2
    assert bodies[0] == bodies[1]


def test_span_exception_finalizes_failed_and_reraises(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"lifecycle": "running"})

    client, factory = _client(handler)
    monkeypatch.setattr(httpx, "Client", factory)
    with pytest.raises(RuntimeError, match="boom"):
        with client.start_run("p", "n"):
            raise RuntimeError("boom")


def test_telemetry_error_does_not_crash_agent(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={"detail": "down"})

    client, factory = _client(handler, raise_on_telemetry_error=False, retries=1)
    monkeypatch.setattr(httpx, "Client", factory)
    with client.start_run("p", "n") as run:
        with run.tool("t"):
            pass
    assert run.telemetry_errors


def test_send_trace_still_raises_http_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(409, json={"detail": "Trace already exists"})

    client, factory = _client(handler)
    monkeypatch.setattr(httpx, "Client", factory)
    with pytest.raises(httpx.HTTPStatusError):
        client.send_trace(TraceData(project_id="p", name="n"))


def test_telemetry_error_type_is_distinct() -> None:
    error = TelemetryError("delivery")
    assert isinstance(error, Exception)
    assert error.__class__.__name__ == "TelemetryError"
