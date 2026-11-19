from __future__ import annotations

import json
import logging
import threading
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any
from uuid import uuid4

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse

from nomore500s_collector.api_client import LiveRunClient
from nomore500s_collector.auth import load_or_create_token
from nomore500s_collector.creation_evidence import EVIDENCE_MODES
from nomore500s_collector.history_guard import acquire_lock, runtime_lock
from nomore500s_collector.normalize import normalize_hook_payload
from nomore500s_collector.otlp import decode_otlp
from nomore500s_collector.pipeline import OverflowError, Pipeline
from nomore500s_collector.settings import CollectorSettings
from nomore500s_collector.store import CollectorStore

logger = logging.getLogger("nomore500s.collector")


class CollectorApp:
    def __init__(self, settings: CollectorSettings | None = None) -> None:
        self.settings = settings or CollectorSettings()
        self.settings.home().mkdir(parents=True, exist_ok=True)
        self._history_lock = runtime_lock(
            store=self.settings.db_path(), api_url=self.settings.api_url
        )
        self._store_lock = acquire_lock(
            self.settings.db_path().with_suffix(".sqlite.runtime.lock"), exclusive=True
        )
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
            self._worker.join()
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
        self._store_lock.close()
        if self._history_lock is not None:
            self._history_lock.close()


def _authorized(runtime: CollectorApp, authorization: str | None) -> bool:
    expected = f"Bearer {runtime.token}"
    return authorization == expected


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
    def health() -> dict[str, Any]:
        app_runtime = current()
        return {
            "status": "ok",
            "pending": app_runtime.store.pending_count(),
            "history_generation": int(app_runtime.store._meta("history_generation") or "0"),
            "requires_session_creation_evidence": bool(app_runtime.store._meta("history_boundary")),
            "creation_evidence_modes": list(EVIDENCE_MODES),
            "last_flush_error": app_runtime.pipeline.last_flush_error,
            "otlp": True,
            "token_configured": bool(app_runtime.token),
            "otlp_path_token": app_runtime.settings.otlp_path_token,
            "adapters": ["codex", "cursor", "vscode-copilot", "otel"],
        }

    @app.post("/v1/collector/events")
    async def ingest(
        request: Request,
        authorization: str | None = Header(default=None),
        x_nm5_delivery_id: str | None = Header(default=None),
        x_nm5_source: str | None = Header(default=None),
    ) -> JSONResponse:
        app_runtime = current()
        if not _authorized(app_runtime, authorization):
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
        normalized = normalize_hook_payload(payload)
        if not normalized.get("hook_event_name") or not normalized.get("session_id"):
            raise HTTPException(
                status_code=422,
                detail="session_id and hook_event_name are required",
            )
        delivery_id = x_nm5_delivery_id or str(payload.get("delivery_id") or "")
        if not delivery_id:
            raise HTTPException(status_code=422, detail="X-NM5-Delivery-Id is required")
        try:
            result = app_runtime.pipeline.ingest(normalized, delivery_id, source=x_nm5_source)
        except OverflowError as error:
            raise HTTPException(status_code=503, detail=str(error)) from error
        return JSONResponse(
            result, status_code=409 if result.get("status") == "rejected_reset_boundary" else 202
        )

    async def ingest_otlp(
        request: Request,
        authorization: str | None,
        path_token: str | None = None,
    ) -> JSONResponse:
        app_runtime = current()
        if path_token is not None:
            if not app_runtime.settings.otlp_path_token or path_token != app_runtime.token:
                raise HTTPException(status_code=401, detail="Unauthorized")
        elif not _authorized(app_runtime, authorization):
            raise HTTPException(status_code=401, detail="Unauthorized")
        body = await request.body()
        if len(body) > app_runtime.settings.max_body_bytes:
            raise HTTPException(status_code=413, detail="Payload too large")
        content_type = request.headers.get("content-type", "application/json")
        try:
            spans = decode_otlp(body, content_type)
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        delivery_id = request.headers.get("X-NM5-Delivery-Id") or str(uuid4())
        try:
            result = app_runtime.pipeline.ingest_otlp_spans(spans, delivery_id)
        except OverflowError as error:
            raise HTTPException(status_code=503, detail=str(error)) from error
        return JSONResponse(result, status_code=202)

    @app.post("/v1/traces")
    async def otlp_traces(
        request: Request,
        authorization: str | None = Header(default=None),
    ) -> JSONResponse:
        return await ingest_otlp(request, authorization)

    @app.post("/otlp/{path_token}/v1/traces")
    async def otlp_traces_path(
        path_token: str,
        request: Request,
        authorization: str | None = Header(default=None),
    ) -> JSONResponse:
        return await ingest_otlp(request, authorization, path_token=path_token)

    return app
