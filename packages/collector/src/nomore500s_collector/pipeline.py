from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from nomore500s_collector.adapters.codex import CodexAdapter
from nomore500s_collector.api_client import DeliveryError, LiveRunClient
from nomore500s_collector.ids import agent_span_id_for_session, trace_id_for_session
from nomore500s_collector.redaction import redact_payload, workspace_name
from nomore500s_collector.store import CollectorStore

logger = logging.getLogger("nomore500s.collector")

OVERFLOW_DROP_HEARTBEATS = "dropped_heartbeats"
OVERFLOW_REJECT = "reject"


class OverflowError(Exception):
    pass


class Pipeline:
    def __init__(
        self,
        store: CollectorStore,
        client: LiveRunClient,
        *,
        collect_io: bool = False,
        project_id: str | None = None,
        adapter: CodexAdapter | None = None,
    ) -> None:
        self.store = store
        self.client = client
        self.collect_io = collect_io
        self.project_id = project_id
        self.adapter = adapter or CodexAdapter()

    def ingest(self, payload: dict[str, Any], delivery_id: str) -> dict[str, Any]:
        cleaned = redact_payload(payload, collect_io=self.collect_io)
        with self.store.transaction():
            existing_delivery = self.store.get_delivery(delivery_id)
            if existing_delivery is not None:
                return {"status": "duplicate_delivery", "occurrence_key": existing_delivery}

            plan = self.adapter.plan(cleaned, delivery_id=delivery_id)
            seen = self.store.get_occurrence(plan.occurrence_key)
            if seen is not None:
                self.store.record_delivery(
                    delivery_id,
                    plan.occurrence_key,
                    [UUID(item) for item in seen],
                )
                return {"status": "duplicate_occurrence", "occurrence_key": plan.occurrence_key}

            if plan.ignored:
                self.store.record_delivery(delivery_id, plan.occurrence_key, [])
                return {"status": "ignored", "occurrence_key": plan.occurrence_key}

            self._ensure_capacity(plan.events[0].critical if plan.events else False)

            session_id = plan.session_id
            trace_id = trace_id_for_session(plan.agent, session_id)
            agent_span_id = agent_span_id_for_session(plan.agent, session_id)
            workspace = plan.workspace or workspace_name(str(cleaned.get("cwd") or "") or None)
            self.store.upsert_session(
                session_id=session_id,
                agent=plan.agent,
                trace_id=trace_id,
                agent_span_id=agent_span_id,
                workspace=workspace,
                model=plan.model,
            )
            session = self.store.get_session(session_id)
            assert session is not None

            if not session["started"] and (not plan.events or plan.events[0].kind != "start"):
                start_key = f"codex:{session_id}:SessionStart"
                if self.store.get_occurrence(start_key) is None:
                    start_plan = self.adapter.plan(
                        {
                            "hook_event_name": "SessionStart",
                            "session_id": session_id,
                            "cwd": workspace or "",
                            "model": plan.model or "",
                            "source": "inferred",
                        },
                        delivery_id=f"inferred-start:{session_id}",
                    )
                    self._enqueue_plan(session_id, start_plan.events, inferred=True)
                    self.store.record_delivery(
                        f"inferred-start:{session_id}",
                        start_key,
                        [item.event_id for item in start_plan.events],
                    )
                    self.store.mark_started(session_id)

            event_ids = self._enqueue_plan(session_id, plan.events)
            if plan.coverage_event:
                self.store.add_coverage(session_id, plan.coverage_event)
            if plan.token_status:
                self.store.set_token_status(session_id, plan.token_status)
            if any(item.kind == "start" for item in plan.events):
                self.store.mark_started(session_id)
            if any(item.kind == "finalize" for item in plan.events):
                self.store.mark_finalized(session_id)
            self.store.record_delivery(delivery_id, plan.occurrence_key, event_ids)
            return {
                "status": "accepted",
                "occurrence_key": plan.occurrence_key,
                "trace_id": str(trace_id),
                "event_ids": [str(item) for item in event_ids],
            }

    def _ensure_capacity(self, critical: bool) -> None:
        count = self.store.pending_count()
        if count < self.store.max_pending:
            return
        heartbeats = self.store.heartbeat_pending_ids()
        if heartbeats:
            overflow = max(0, count - self.store.max_pending + 1)
            self.store.delete_pending_ids(heartbeats[:overflow])
            logger.warning(
                "dropped %s heartbeat pending rows (overflow)",
                min(overflow, len(heartbeats)),
            )
            return
        if not critical:
            raise OverflowError("pending queue full; dropped non-critical event")
        raise OverflowError("pending queue full")

    def _enqueue_plan(
        self, session_id: str, events: list[Any], *, inferred: bool = False
    ) -> list[UUID]:
        ids: list[UUID] = []
        for item in events:
            payload = dict(item.payload)
            if item.kind == "start":
                project = self.project_id or str(payload.get("project_id") or "codex")
                payload["project_id"] = project[:100]
                sequence = 0
            else:
                sequence = self.store.next_sequence(session_id)
                payload["sequence"] = sequence
            payload.pop("_trace_id", None)
            pair = payload.pop("_model_pair", None)
            if item.kind == "events" and payload.get("event_type") == "span_start":
                self.store.put_open_span(
                    session_id,
                    str(payload["span_id"]),
                    str(payload.get("type") or "span"),
                    str(pair) if pair else None,
                )
            if item.kind == "events" and payload.get("event_type") == "span_complete":
                if payload.get("type") == "llm" and pair is None:
                    unmatched = self.store.unmatched_model_span(session_id)
                    if unmatched:
                        payload["span_id"] = unmatched
                self.store.pop_open_span(str(payload.get("span_id")))
            self.store.enqueue(
                session_id=session_id,
                event_id=item.event_id,
                kind=item.kind,
                payload=payload,
                critical=item.critical,
            )
            ids.append(item.event_id)
        if inferred:
            self.store.mark_started(session_id)
        return ids

    def flush_once(self) -> int:
        delivered = 0
        for row in self.store.due_pending():
            try:
                self._deliver(row.kind, row.payload, UUID(row.event_id), row.session_id)
            except DeliveryError as error:
                delay = 0.0 if row.attempts == 0 else min(30.0, 0.25 * (2 ** row.attempts))
                if error.retryable:
                    with self.store.transaction():
                        self.store.mark_retry(row.id, str(error), delay)
                    continue
                logger.error("dropping non-retryable event %s: %s", row.event_id, error)
                with self.store.transaction():
                    self.store.mark_delivered(row.id)
                continue
            with self.store.transaction():
                self.store.mark_delivered(row.id)
            delivered += 1
        return delivered

    def _deliver(self, kind: str, payload: dict[str, Any], event_id: UUID, session_id: str) -> None:
        session = self.store.get_session(session_id)
        if session is None:
            raise DeliveryError("unknown session", retryable=False)
        trace_id = session["trace_id"]
        body = dict(payload)
        body.pop("_trace_id", None)
        body.pop("_model_pair", None)
        if kind == "start":
            self.client.post("/v1/runs", body)
            return
        if kind == "events":
            self.client.post(f"/v1/runs/{trace_id}/events", [body])
            return
        if kind == "finalize":
            self.client.post(f"/v1/runs/{trace_id}/finalize", body)
            return
        raise DeliveryError(f"unknown kind {kind}", retryable=False)
