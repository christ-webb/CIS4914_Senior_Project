from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from nomore500s_collector.history_guard import history_home, read_state, remembered, runtime_lock

from no_more_500s_api.api.routes import health, traces
from no_more_500s_api.core.config import get_settings
from no_more_500s_api.services.trace_service import InMemoryTraceRepository, TraceRepository


def create_app(repository: TraceRepository | None = None) -> FastAPI:
    settings = get_settings()
    if repository is None:
        if settings.trace_repository == "clickhouse":
            from no_more_500s_api.services.clickhouse_trace_repository import (
                ClickHouseTraceRepository,
            )

            repository = ClickHouseTraceRepository(
                host=settings.clickhouse_host,
                port=settings.clickhouse_port,
                database=settings.clickhouse_database,
                username=settings.clickhouse_user,
                password=settings.clickhouse_password,
                inactivity_seconds=settings.run_inactivity_seconds,
            )
        elif settings.trace_repository == "memory":
            repository = InMemoryTraceRepository(
                inactivity_seconds=settings.run_inactivity_seconds,
            )
        else:
            raise ValueError(f"Unknown trace repository: {settings.trace_repository}")

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        guard = runtime_lock(database=settings.clickhouse_database)
        try:
            yield
        finally:
            if guard is not None:
                guard.close()

    app = FastAPI(title="No More 500s API", version="0.1.0", lifespan=lifespan)

    @app.middleware("http")
    async def history_boundary(request: Request, call_next):  # type: ignore[no-untyped-def]
        path = history_home() / "state.json"
        generation = 0
        if path.exists():
            state = read_state(path.parent)
            generation = state["generation"]
            if state["phase"] != "ready":
                return JSONResponse(
                    {"detail": "Local history reset requires recovery"}, status_code=503
                )
            if generation and request.method == "POST":
                bits = bytes.fromhex(state["deny_traces"])
                parts = request.url.path.strip("/").split("/")
                if len(parts) > 2 and parts[1] == "runs" and remembered(bits, "trace", parts[2]):
                    return JSONResponse(
                        {"detail": "Cleared run cannot be resumed"}, status_code=409
                    )
                if request.url.path in {"/v1/traces", "/v1/runs"}:
                    try:
                        body = await request.json()
                    except ValueError:
                        return JSONResponse({"detail": "Invalid JSON"}, status_code=422)
                    if not isinstance(body, dict):
                        return JSONResponse({"detail": "Run identity required"}, status_code=422)
                    trace_id = str(body.get("trace_id", ""))
                    try:
                        started = datetime.fromisoformat(
                            body.get("started_at", "").replace("Z", "+00:00")
                        )
                        fresh = (
                            datetime.fromisoformat(state["boundary"]) < started <= datetime.now(UTC)
                        )
                    except (ValueError, TypeError):
                        fresh = False
                    if not fresh or not trace_id or remembered(bits, "trace", trace_id):
                        return JSONResponse(
                            {"detail": "A new post-reset run is required"}, status_code=409
                        )
        response = await call_next(request)
        response.headers["X-NM5-History-Generation"] = str(generation)
        return response

    app.state.trace_repository = repository
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.allowed_origins,
        allow_credentials=False,
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type"],
        expose_headers=["X-NM5-History-Generation"],
    )
    app.include_router(health.router)
    app.include_router(traces.router)
    return app


app = create_app()
