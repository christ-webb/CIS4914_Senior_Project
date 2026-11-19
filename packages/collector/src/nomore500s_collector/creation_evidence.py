"""Creation evidence for post-reset session admission.

Providers that do not expose an original conversation timestamp can still
prove freshness when their documented session-creation hook is observed
locally. Receipt time of ordinary continuation events is never evidence.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from nomore500s_collector.history_guard import remembered
from nomore500s_collector.ids import store_session_key, trace_id_for_session
from nomore500s_collector.normalize import canonical_event_name

OBSERVED_AT_FIELD = "_nm5_creation_observed_at"
SOURCE_FIELD = "_nm5_creation_source"
CREATION_EVENT = "sessionStart"
CODEX_NEW_SESSION_SOURCES = frozenset({"startup", "new"})
CLOCK_SKEW = timedelta(seconds=5)
MAX_OBSERVATION_AGE = timedelta(seconds=120)
EVIDENCE_MODES = ("session_created_at", "hook_observed_session_start")


def payload_identities(payload: dict[str, Any]) -> set[str]:
    return {
        str(payload[key])
        for key in ("session_id", "conversation_id", "resource_session_id")
        if payload.get(key)
    }


def stamp(payload: dict[str, Any], source: str | None) -> bool:
    event = canonical_event_name(
        str(payload.get("hook_event_name") or payload.get("hookEventName") or "")
    )
    if event != CREATION_EVENT:
        return False
    if str(payload.get("source") or "") == "inferred":
        return False
    if OBSERVED_AT_FIELD not in payload:
        payload[OBSERVED_AT_FIELD] = datetime.now(UTC).isoformat()
        payload[SOURCE_FIELD] = source or "unknown"
    return True


def parse_aware_datetime(raw: Any) -> datetime | None:
    try:
        if isinstance(raw, (float, int)):
            created = datetime.fromtimestamp(raw / 1000 if raw > 1e12 else raw, UTC)
        elif isinstance(raw, str):
            created = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        else:
            return None
        if created.tzinfo is None:
            return None
        return created.astimezone(UTC)
    except (ValueError, OverflowError, OSError, TypeError):
        return None


def _denied(agent: str, bits: bytes, identity: str) -> bool:
    return (
        remembered(bits, agent, identity)
        or remembered(bits, agent, store_session_key(agent, identity))
        or remembered(bits, "trace", str(trace_id_for_session(agent, identity)))
    )


def verify(
    agent: str,
    payload: dict[str, Any],
    *,
    boundary: str,
    now: datetime,
    deny_bits: bytes,
    known_session: bool,
) -> tuple[bool, str]:
    ids = payload_identities(payload)
    if not ids:
        return False, "missing_identity"
    if not deny_bits:
        return False, "filter_unavailable"
    if any(_denied(agent, deny_bits, identity) for identity in ids):
        return False, "denied_identity"
    if known_session:
        return True, "known_session"
    event = canonical_event_name(str(payload.get("hook_event_name") or ""))
    if event != CREATION_EVENT:
        return False, "not_session_start"
    if str(payload.get("source") or "") == "inferred":
        return False, "untrusted_session_source"
    if agent == "codex":
        source = str(payload.get("source") or "")
        if source not in CODEX_NEW_SESSION_SOURCES:
            return False, "untrusted_session_source"
    created = parse_aware_datetime(payload.get("session_created_at"))
    if created is None:
        created = parse_aware_datetime(payload.get(OBSERVED_AT_FIELD))
    if created is None:
        return False, "missing_creation_evidence"
    try:
        reset_at = datetime.fromisoformat(boundary)
    except ValueError:
        return False, "missing_creation_evidence"
    if created.tzinfo is None:
        return False, "missing_creation_evidence"
    if not (reset_at < created <= now + CLOCK_SKEW):
        return False, "stale_creation_evidence"
    if now - created > MAX_OBSERVATION_AGE:
        return False, "stale_creation_evidence"
    return True, "fresh_session"
