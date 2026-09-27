from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

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
            )
        elif settings.trace_repository == "memory":
            repository = InMemoryTraceRepository()
        else:
            raise ValueError(f"Unknown trace repository: {settings.trace_repository}")
    app = FastAPI(title="No More 500s API", version="0.1.0")
    app.state.trace_repository = repository
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.allowed_origins,
        allow_credentials=False,
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type"],
    )
    app.include_router(health.router)
    app.include_router(traces.router)
    return app


app = create_app()
