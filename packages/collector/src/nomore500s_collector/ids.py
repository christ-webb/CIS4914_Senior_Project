from __future__ import annotations

from uuid import UUID, uuid5

NM5_NAMESPACE = UUID("3f0a7c2e-9c41-4d6f-8b1a-0b5c6d7e8f90")


def namespaced(name: str) -> UUID:
    return uuid5(NM5_NAMESPACE, name)


def trace_id_for_session(agent: str, session_id: str) -> UUID:
    return namespaced(f"{agent}:session:{session_id}")


def agent_span_id_for_session(agent: str, session_id: str) -> UUID:
    return namespaced(f"{agent}:agent:{session_id}")


def event_id_for(occurrence_key: str) -> UUID:
    return namespaced(f"event:{occurrence_key}")


def span_id_for(kind: str, identity: str) -> UUID:
    return namespaced(f"span:{kind}:{identity}")


def store_session_key(agent: str, session_id: str) -> str:
    prefix = f"{agent}:"
    if session_id.startswith(prefix):
        return session_id
    return f"{prefix}{session_id}"
