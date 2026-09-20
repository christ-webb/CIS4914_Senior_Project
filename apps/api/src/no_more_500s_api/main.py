from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from no_more_500s_api.api.routes import health, traces
from no_more_500s_api.core.config import get_settings


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title="No More 500s API", version="0.1.0")
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

