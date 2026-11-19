from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from nomore500s_collector.adapters.base import Adapter
from nomore500s_collector.adapters.codex import CodexAdapter
from nomore500s_collector.adapters.router import select_adapter
from nomore500s_collector.api_client import DeliveryError, LiveRunClient
from nomore500s_collector.codex_surface import UNKNOWN_SURFACE, surface_for
from nomore500s_collector.creation_evidence import payload_identities, verify
from nomore500s_collector.ids import (
    agent_span_id_for_session,
    store_session_key,
    trace_id_for_session,
)
from nomore500s_collector.normalize import normalize_hook_payload
from nomore500s_collector.redaction import redact_payload, workspace_name
from nomore500s_collector.store import CollectorStore

logger = logging.getLogger("nomore500s.collector")

DIAGNOSTICS_KEY = "diagnostics_v1"
MAX_DIAGNOSTIC_KEYS = 256
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
        adapter: Adapter | None = None,
    ) -> None:
        self.store = store
        self.client = client
        self.collect_io = collect_io
        self.project_id = project_id
        self.adapter = adapter
        self.last_flush_error: str | None = None

    # -- bounded, privacy-preserving outcome counters (no ids, no content) --

    def _diagnostics(self) -> dict[str, Any]:
        raw = self.store._meta(DIAGNOSTICS_KEY)
        try:
            value = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            value = {}
        if not isinstance(value, dict):
            value = {}
        value.setdefault("outcomes", {})
        value.setdefault("last_accepted", {})
        return value

    def record_outcome(
        self, agent: str, surface: str, status: str, reason: str | None = None
    ) -> None:
        now = datetime.now(UTC).isoformat()
        with self.store.transaction():
            data = self._diagnostics()
            outcomes: dict[str, Any] = data["outcomes"]
            key = "|".join([agent[:32], surface[:32], status[:48], (reason or "-")[:48]])
            if key not in outcomes and len(outcomes) >= MAX_DIAGNOSTIC_KEYS:
                key = "|".join([agent[:32], surface[:32], "overflow", "-"])
            entry = outcomes.setdefault(key, {"count": 0, "last_at": None})
            entry["count"] = int(entry.get("count") or 0) + 1
            entry["last_at"] = now
            if status == "accepted":
                data["last_accepted"][surface[:32]] = now
            self.store._set_meta(DIAGNOSTICS_KEY, json.dumps(data, sort_keys=True))

    def diagnostics(self) -> dict[str, Any]:
        data = self._diagnostics()
        outcomes = []
        for key, entry in sorted(data["outcomes"].items()):
            agent, surface, status, reason = (key.split("|") + ["-"] * 4)[:4]
            outcomes.append(
                {
                    "agent": agent,
                    "surface": surface,
                    "status": status,
                    "reason": None if reason == "-" else reason,
                    "count": entry.get("count", 0),
                    "last_at": entry.get("last_at"),
                }
            )
        return {"outcomes": outcomes, "last_accepted": data["last_accepted"]}

    @staticmethod
    def _surface(agent: str, payload: dict[str, Any]) -> str:
        if agent == "codex":
            return surface_for(payload)
        return agent or UNKNOWN_SURFACE

    def ingest(
        self,
        payload: dict[str, Any],
        delivery_id: str,
        *,
        source: str | None = None,
    ) -> dict[str, Any]:
        cleaned = redact_payload(normalize_hook_payload(payload), collect_io=self.collect_io)
        adapter = self.adapter or select_adapter(cleaned, source)
        result = self._ingest(cleaned, delivery_id, adapter)
        status = str(result.get("status") or "unknown")
        reason = result.get("reason")
        if status == "ignored":
            reason = "unsupported_event"
        self.record_outcome(
            adapter.name,
            self._surface(adapter.name, cleaned),
            status,
            str(reason) if reason else None,
        )
        return result

    def _ingest(
        self, cleaned: dict[str, Any], delivery_id: str, adapter: Adapter
    ) -> dict[str, Any]:
        with self.store.transaction():
            allowed, reason = self._after_reset_allowed(adapter.name, cleaned)
            if not allowed:
                return {
                    "status": "rejected_reset_boundary",
                    "reason": reason,
                }
            existing_delivery = self.store.get_delivery(delivery_id)
            if existing_delivery is not None:
                return {"status": "duplicate_delivery", "occurrence_key": existing_delivery}

            plan = adapter.plan(cleaned, delivery_id=delivery_id)
            session_id = self._canonical_session(adapter.name, plan.session_id, cleaned)
            plan.session_id = session_id
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

            store_key = store_session_key(plan.agent, session_id)
            trace_id = trace_id_for_session(plan.agent, session_id)
            agent_span_id = agent_span_id_for_session(plan.agent, session_id)
            workspace = plan.workspace or workspace_name(str(cleaned.get("cwd") or "") or None)
            self.store.upsert_session(
                session_id=store_key,
                agent=plan.agent,
                trace_id=trace_id,
                agent_span_id=agent_span_id,
                workspace=workspace,
                model=plan.model,
            )
            session = self.store.get_session(store_key)
            assert session is not None

            if not session["started"] and (not plan.events or plan.events[0].kind != "start"):
                start_key = f"{plan.agent}:{session_id}:SessionStart"
                if self.store.get_occurrence(start_key) is None:
                    start_plan = adapter.plan(
                        adapter.inferred_start(
                            session_id,
                            workspace=workspace,
                            model=plan.model,
                        ),
                        delivery_id=f"inferred-start:{store_key}",
                    )
                    self._enqueue_plan(store_key, start_plan.events, inferred=True)
                    self.store.record_delivery(
                        f"inferred-start:{store_key}",
                        start_key,
                        [item.event_id for item in start_plan.events],
                    )
                    self.store.mark_started(store_key)

            event_ids = self._enqueue_plan(store_key, plan.events)
            if plan.coverage_event:
                self.store.add_coverage(store_key, plan.coverage_event)
            if plan.token_status:
                self.store.set_token_status(store_key, plan.token_status)
            if any(item.kind == "start" for item in plan.events):
                self.store.mark_started(store_key)
            if any(item.kind == "finalize" for item in plan.events):
                self.store.mark_finalized(store_key)
            self.store.record_delivery(delivery_id, plan.occurrence_key, event_ids)
            return {
                "status": "accepted",
                "occurrence_key": plan.occurrence_key,
                "trace_id": str(trace_id),
                "event_ids": [str(item) for item in event_ids],
            }

    def _after_reset_allowed(self, agent: str, payload: dict[str, Any]) -> tuple[bool, str]:
        boundary = self.store._meta("history_boundary")
        if not boundary:
            return True, "no_boundary"
        ids = payload_identities(payload)
        known = bool(ids) and all(
            self.store.get_session(store_session_key(agent, identity)) is not None
            for identity in ids
        )
        return verify(
            agent,
            payload,
            boundary=boundary,
            now=datetime.now(UTC),
            deny_bits=bytes.fromhex(self.store._meta("history_deny") or ""),
            known_session=known,
        )

    def ingest_otlp_spans(self, spans: list[dict[str, Any]], delivery_id: str) -> dict[str, Any]:
        results: list[dict[str, Any]] = []
        for index, span in enumerate(spans):
            for phase in ("start", "end"):
                payload = dict(span)
                payload["_phase"] = phase
                payload["_nm5_source"] = "otel"
                if phase == "end" and not span.get("ended_at") and not span.get("occurred_at"):
                    continue
                if phase == "start" and span.get("occurred_at"):
                    payload["occurred_at"] = span["occurred_at"]
                elif phase == "end" and span.get("ended_at"):
                    payload["occurred_at"] = span["ended_at"]
                child_id = f"{delivery_id}:{span.get('otel_span_id') or index}:{phase}"
                results.append(self.ingest(payload, child_id, source="otel"))
        accepted = sum(1 for item in results if item.get("status") == "accepted")
        return {
            "status": "accepted" if results else "ignored",
            "spans": accepted,
            "results": results,
        }

    def _canonical_session(self, agent: str, session_id: str, payload: dict[str, Any]) -> str:
        candidates = [
            session_id,
            str(payload.get("conversation_id") or ""),
            str(payload.get("resource_session_id") or ""),
        ]
        if payload.get("conversation_id") and payload.get("session_id"):
            left = str(payload.get("session_id"))
            right = str(payload.get("conversation_id"))
            if left and right and left == right:
                self.store.link_identities(agent, "session", [left, right], left, "exact")
        mapped = self.store.resolve_canonical(agent, "session", candidates)
        if mapped:
            return mapped
        if payload.get("conversation_id") and payload.get("resource_session_id"):
            conversation = str(payload.get("conversation_id"))
            resource = str(payload.get("resource_session_id"))
            if conversation and resource and conversation != resource:
                known = self.store.resolve_canonical(agent, "session", [resource])
                if known:
                    self.store.link_identities(
                        agent, "session", [conversation, resource, known], known, "exact"
                    )
                    return known
                known_conv = self.store.resolve_canonical(agent, "session", [conversation])
                if known_conv:
                    self.store.link_identities(
                        agent, "session", [conversation, resource, known_conv], known_conv, "exact"
                    )
                    return known_conv
        return session_id

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
                project = self.project_id or str(payload.get("project_id") or "external")
                payload["project_id"] = project[:100]
                sequence = 0
            else:
                sequence = self.store.next_sequence(session_id)
                payload["sequence"] = sequence
            payload.pop("_trace_id", None)
            pair = payload.pop("_model_pair", None)
            identities = [str(value) for value in payload.pop("_span_identities", []) if value]
            parent_identities = [
                str(value) for value in payload.pop("_parent_identities", []) if value
            ]
            kind = str(payload.get("type") or payload.pop("_span_kind", None) or "span")
            if item.kind == "events" and payload.get("event_type") in {
                "span_start",
                "span_complete",
            }:
                if identities:
                    existing = self.store.span_id_for_identities(session_id, identities)
                    if existing:
                        payload["span_id"] = existing
                    elif payload.get("span_id"):
                        self.store.remember_span_identities(
                            session_id, identities, str(payload["span_id"])
                        )
                if parent_identities:
                    parent = self.store.span_id_for_identities(session_id, parent_identities)
                    if parent:
                        payload["parent_span_id"] = parent
            if item.kind == "events" and payload.get("event_type") == "span_start":
                self.store.put_open_span(
                    session_id,
                    str(payload["span_id"]),
                    kind,
                    str(pair) if pair else (identities[0] if identities else None),
                )
            if item.kind == "events" and payload.get("event_type") == "span_complete":
                if payload.get("type") == "llm" and pair is None:
                    unmatched = self.store.unmatched_model_span(session_id)
                    if unmatched:
                        payload["span_id"] = unmatched
                self.store.pop_open_span(str(payload.get("span_id")))
            if item.kind == "events" and payload.get("event_type") == "heartbeat":
                coverage = self.store.get_session(session_id)
                if coverage is not None:
                    attrs = dict(payload.get("attributes") or {})
                    payload["attributes"] = attrs
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
        try:
            for row in self.store.due_pending():
                try:
                    self._deliver(row.kind, row.payload, UUID(row.event_id), row.session_id)
                except DeliveryError as error:
                    delay = 0.0 if row.attempts == 0 else min(30.0, 0.25 * (2**row.attempts))
                    if error.retryable:
                        with self.store.transaction():
                            self.store.mark_retry(row.id, str(error), delay)
                        self.last_flush_error = str(error)
                        continue
                    logger.error("dropping non-retryable event %s: %s", row.event_id, error)
                    with self.store.transaction():
                        self.store.mark_delivered(row.id)
                    self.last_flush_error = str(error)
                    continue
                with self.store.transaction():
                    self.store.mark_delivered(row.id)
                delivered += 1
            if delivered:
                self.last_flush_error = None
        except Exception as error:
            self.last_flush_error = str(error)
            raise
        return delivered

    def _deliver(self, kind: str, payload: dict[str, Any], event_id: UUID, session_id: str) -> None:
        session = self.store.get_session(session_id)
        if session is None:
            raise DeliveryError("unknown session", retryable=False)
        trace_id = session["trace_id"]
        body = dict(payload)
        body.pop("_trace_id", None)
        body.pop("_model_pair", None)
        body.pop("_span_identities", None)
        body.pop("_parent_identities", None)
        body.pop("_span_kind", None)
        if kind == "start":
            path = "/v1/runs"
            sent: Any = body
        elif kind == "events":
            path = f"/v1/runs/{trace_id}/events"
            sent = [body]
        elif kind == "finalize":
            path = f"/v1/runs/{trace_id}/finalize"
            sent = body
        else:
            raise DeliveryError(f"unknown kind {kind}", retryable=False)
        if self.client.post(path, sent) == 409:
            # The API refused the transition (e.g. an event after finalize). The
            # completed run is intentionally not reopened; record the drop.
            self.record_outcome(
                str(session["agent"]), str(session["agent"]), "api_conflict", f"{kind}_dropped"
            )


__all__ = ["CodexAdapter", "OverflowError", "Pipeline"]
