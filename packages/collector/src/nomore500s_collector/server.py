from __future__ import annotations

import json
import logging
import threading
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse

from nomore500s_collector.api_client import LiveRunClient
from nomore500s_collector.auth import load_or_create_token
from nomore500s_collector.pipeline import OverflowError, Pipeline
from nomore500s_collector.settings import CollectorSettings
from nomore500s_collector.store import CollectorStore

logger = logging.getLogger("nomore500s.collector")


class CollectorApp:
    def __init__(self, settings: CollectorSettings | None = None) -> None:
        self.settings = settings or CollectorSettings()
        self.token = load_or_create_token(self.settings.home(), self.settings.collector_token)
        self.store = CollectorStore(self.settings.db_path(), max_pending=self.settings.max_pending)
        self.pipeline = Pipeline(
            self.store,
            LiveRunClient(self.settings.api_url),
            collect_io=self.settings.collect_tool_io,
            project_id=self.settings.project_id,
        )
        self._stop = threading.Event()
        self._worker: threading.Thread | None = None

    def start_worker(self) -> None:
        if self._worker is not None:
            return
        self._stop.clear()
        self._worker = threading.Thread(target=self._loop, name="nm5-collector-flush", daemon=True)
        self._worker.start()

    def stop_worker(self) -> None:
        self._stop.set()
        if self._worker is not None:
            self._worker.join(timeout=2)
            self._worker = None

    def _loop(self) -> None:
        while not self._stop.wait(0.25):
            try:
                self.pipeline.flush_once()
            except Exception:
                logger.exception("collector flush failed")

    def close(self) -> None:
        self.stop_worker()
        self.store.close()


def create_app(runtime: CollectorApp | None = None) -> FastAPI:
    holder: dict[str, CollectorApp] = {}

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        holder["runtime"] = runtime or CollectorApp()
        holder["runtime"].start_worker()
        try:
            yield
        finally:
            holder["runtime"].close()

    app = FastAPI(title="No More 500s collector", lifespan=lifespan)

    def current() -> CollectorApp:
        return holder["runtime"]

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/v1/collector/events")
    async def ingest(
        request: Request,
        authorization: str | None = Header(default=None),
        x_nm5_delivery_id: str | None = Header(default=None),
    ) -> JSONResponse:
        app_runtime = current()
        expected = f"Bearer {app_runtime.token}"
        if authorization != expected:
            raise HTTPException(status_code=401, detail="Unauthorized")
        body = await request.body()
        if len(body) > app_runtime.settings.max_body_bytes:
            raise HTTPException(status_code=413, detail="Payload too large")
        try:
            payload: Any = json.loads(body.decode("utf-8") or "null")
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise HTTPException(status_code=400, detail="Invalid JSON") from error
        if not isinstance(payload, dict):
            raise HTTPException(status_code=422, detail="Hook payload must be an object")
        if not payload.get("hook_event_name") or not payload.get("session_id"):
            raise HTTPException(
                status_code=422,
                detail="session_id and hook_event_name are required",
            )
        delivery_id = x_nm5_delivery_id or str(payload.get("delivery_id") or "")
        if not delivery_id:
            raise HTTPException(status_code=422, detail="X-NM5-Delivery-Id is required")
        try:
            result = app_runtime.pipeline.ingest(payload, delivery_id)
        except OverflowError as error:
            raise HTTPException(status_code=503, detail=str(error)) from error
        return JSONResponse(result, status_code=202)

    return app
