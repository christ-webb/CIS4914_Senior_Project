from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol
from uuid import UUID


@dataclass(frozen=True)
class MappedEvent:
    event_id: UUID
    kind: str  # start | events | finalize
    payload: dict[str, Any]
    critical: bool = True


@dataclass
class SessionPlan:
    session_id: str
    agent: str
    occurrence_key: str
    ignored: bool = False
    coverage_event: str | None = None
    events: list[MappedEvent] = field(default_factory=list)
    token_status: str | None = None
    cost_status: str | None = None
    model: str | None = None
    workspace: str | None = None
    occurred_at: datetime | None = None


class Adapter(Protocol):
    name: str

    def plan(self, payload: dict[str, Any], *, delivery_id: str) -> SessionPlan: ...
